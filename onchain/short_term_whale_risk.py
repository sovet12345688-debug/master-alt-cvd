#!/usr/bin/env python3
"""MASTER MARKET free/no-key short-term whale profit-taking risk proxy.

This is NOT CryptoQuant's exact STH Whale Unrealized P&L.
It combines free public Checkonchain STH cohort charts with existing
MASTER MARKET Hyperliquid BTC whale NET and Bitget BTC CVD.

Design goals:
- no paid API / no API key / no login
- no OCR / no browser automation
- no backfill / interpolation / guessed value
- simple static Plotly HTML parsing with freshness guards
"""
from __future__ import annotations

import base64
import csv
import json
import math
import statistics
import struct
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "onchain/output/latest_short_term_whale_risk.json"
HISTORY = ROOT / "onchain/data/short_term_whale_risk_history.csv"
ACTOR = ROOT / "market_vault/output/latest_actor_flows.json"
MICRO = ROOT / "derivatives/output/latest_microstructure.json"

ENGINE = "MASTER_ST_WHALE_PROFIT_TAKING_RISK_PROXY_V1"
SCHEMA = "1.2"
MAX_STH_AGE_HOURS = 72.0

LIVE = "https://charts.checkonchain.com"
RAW = "https://raw.githubusercontent.com/checkmatey/checkonchain.com/main"

# Try the live public chart first. Raw GitHub is fallback transport only and is
# accepted only when the trace itself passes the same timestamp freshness guard.
SOURCES = {
    "nupl": [
        (f"{LIVE}/btconchain/unrealised/nupl_bycohort/nupl_bycohort_light.html", "Checkonchain live"),
        (f"{LIVE}/btconchain/unrealised/nupl_sth/nupl_sth_light.html", "Checkonchain live"),
        (f"{RAW}/btconchain/unrealised/nupl_bycohort/nupl_bycohort_light.html", "Checkonchain GitHub fallback"),
    ],
    "mvrv": [
        (f"{LIVE}/btconchain/unrealised/mvrv_sth/mvrv_sth_light.html", "Checkonchain live"),
        (f"{LIVE}/btconchain/unrealised/sthmvrv_indicator/sthmvrv_indicator_light.html", "Checkonchain live"),
        (f"{RAW}/btconchain/unrealised/mvrv_sth/mvrv_sth_light.html", "Checkonchain GitHub fallback"),
        (f"{RAW}/btconchain/unrealised/sthmvrv_indicator/sthmvrv_indicator_light.html", "Checkonchain GitHub fallback"),
    ],
    "sopr": [
        (f"{LIVE}/btconchain/realised/sopr_sth/sopr_sth_light.html", "Checkonchain live"),
        (f"{LIVE}/btconchain/realised/sthsopr_indicator/sthsopr_indicator_light.html", "Checkonchain live"),
        (f"{RAW}/btconchain/realised/sopr_sth/sopr_sth_light.html", "Checkonchain GitHub fallback"),
        (f"{RAW}/btconchain/realised/sthsopr_indicator/sthsopr_indicator_light.html", "Checkonchain GitHub fallback"),
    ],
}

TRACE_NAMES = {
    "nupl": ["STH-NUPL"],
    "mvrv": ["STH-MVRV"],
    "sopr": ["STH-SOPR", "STH-SOPR 7-day MA"],
}

POINTS = {"🔴": 1.0, "🟡": 0.5, "🟢": 0.0}


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def parse_dt(v: Any) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


def age_hours(v: str | None) -> float | None:
    d = parse_dt(v)
    return None if d is None else (now() - d).total_seconds() / 3600.0


def fetch_text(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; MASTER-MARKET-free-proxy/1.3)",
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def extract_json_array(text: str, start: int) -> str:
    depth = 0
    quoted = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if quoted:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                quoted = False
            continue
        if c == '"':
            quoted = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError("unterminated Plotly trace array")


def plotly_traces(raw: str) -> list[dict[str, Any]]:
    p = raw.find("Plotly.newPlot")
    if p < 0:
        raise ValueError("Plotly.newPlot not found")
    s = raw.find("[", p)
    if s < 0:
        raise ValueError("Plotly data array not found")
    data = json.loads(extract_json_array(raw, s))
    return [x for x in data if isinstance(x, dict)]


def decode_array(v: Any) -> list[Any]:
    if isinstance(v, list):
        return v
    if not isinstance(v, dict) or "bdata" not in v:
        return []
    raw = base64.b64decode(v["bdata"])
    dtype = str(v.get("dtype") or "f8")
    endian = "<"
    if dtype and dtype[0] in "<>":
        endian, dtype = dtype[0], dtype[1:]
    elif dtype and dtype[0] in "=|":
        dtype = dtype[1:]
    fmts = {
        "f8": "d", "f4": "f",
        "i8": "q", "i4": "i", "i2": "h", "i1": "b",
        "u8": "Q", "u4": "I", "u2": "H", "u1": "B",
    }
    fmt = fmts.get(dtype)
    if not fmt:
        raise ValueError(f"unsupported Plotly dtype={dtype}")
    size = struct.calcsize(fmt)
    if len(raw) % size:
        raise ValueError("typed-array byte length mismatch")
    return [x[0] for x in struct.iter_unpack(endian + fmt, raw)]


def find_trace(ts: list[dict[str, Any]], names: list[str]) -> dict[str, Any]:
    lookup = {str(t.get("name") or "").strip().lower(): t for t in ts}
    for name in names:
        t = lookup.get(name.lower())
        if t is not None:
            return t
    available = [str(t.get("name")) for t in ts if t.get("name")][:30]
    raise ValueError(f"target trace not found; wanted={names}; available={available}")


def trace_stats(t: dict[str, Any]) -> dict[str, Any]:
    x = decode_array(t.get("x"))
    if not x and isinstance(t.get("x"), list):
        x = t["x"]
    y = decode_array(t.get("y"))
    if not y and isinstance(t.get("y"), list):
        y = t["y"]

    pairs: list[tuple[datetime | None, float]] = []
    for i, v in enumerate(y):
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        v = float(v)
        if not math.isfinite(v):
            continue
        d = parse_dt(x[i]) if i < len(x) else None
        pairs.append((d, v))

    if not pairs:
        raise ValueError(f"no numeric values in trace={t.get('name')}")

    dated = [(d, v) for d, v in pairs if d is not None]
    if dated:
        latest_dt, value = dated[-1]
        source_age = (now() - latest_dt).total_seconds() / 3600.0
        if source_age < -2:
            raise ValueError(f"future-dated trace source_time={iso(latest_dt)}")
        if source_age > MAX_STH_AGE_HOURS:
            raise ValueError(
                f"stale Checkonchain trace age_hours={source_age:.1f} "
                f"source_time={iso(latest_dt)}"
            )
        hist = [v for d, v in dated if d >= latest_dt - timedelta(days=1461)]
        last7 = [v for d, v in dated if d >= latest_dt - timedelta(days=7)]
    else:
        raise ValueError("trace has no parseable timestamps; freshness cannot be verified")

    if len(hist) < 30:
        hist = [v for _, v in dated]

    percentile = 100.0 * sum(v <= value for v in hist) / len(hist)
    return {
        "status": "OK",
        "trace_name": t.get("name"),
        "source_time_utc": iso(latest_dt),
        "source_age_hours": round(source_age, 2),
        "value": value,
        "percentile_4y": percentile,
        "avg_7d": statistics.fmean(last7) if last7 else None,
        "history_points_4y": len(hist),
    }


def sth_metric(kind: str) -> dict[str, Any]:
    errors: list[str] = []
    for url, source_label in SOURCES[kind]:
        try:
            raw = fetch_text(url)
            ts = plotly_traces(raw)
            t = find_trace(ts, TRACE_NAMES[kind])
            p = trace_stats(t)
            p.update({
                "source": f"{source_label} public static Plotly HTML (no API key)",
                "source_url": url,
            })
            return p
        except Exception as e:
            errors.append(f"{source_label} {url}: {type(e).__name__}: {e}")

    return {
        "status": "N/A",
        "value": None,
        "percentile_4y": None,
        "avg_7d": None,
        "source": "Checkonchain public static Plotly HTML (live first; GitHub fallback if fresh)",
        "source_url": None,
        "error": " | ".join(errors)[:4000],
    }


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}


def whale() -> dict[str, Any]:
    try:
        p = read_json(ACTOR)
        c = p["whale"]["BTC"]["current"]
        net = float(c["net_usd"])
        longv = float(c["long_usd"])
        shortv = float(c["short_usd"])
        ts = c.get("snapshot_time_utc")
        age = age_hours(ts)
        if age is None or age > 6:
            raise ValueError(f"stale actor flow age_hours={age}")
        gross = longv + shortv
        return {
            "status": "OK",
            "value": net,
            "net_ratio": net / gross if gross else None,
            "long_usd": longv,
            "short_usd": shortv,
            "source_time_utc": ts,
            "source": "market_vault/output/latest_actor_flows.json (Hyperliquid >=$20M BTC aggregate)",
        }
    except Exception as e:
        return {"status": "N/A", "value": None, "error": f"{type(e).__name__}: {e}"}


def cvd() -> dict[str, Any]:
    try:
        p = read_json(MICRO)
        if p.get("engine") != "MASTER_DERIVATIVES_MICROSTRUCTURE_V2":
            raise ValueError("wrong derivatives engine")

        assets = p.get("assets")
        if isinstance(assets, list):
            a = next(x for x in assets if x.get("symbol") == "BTCUSDT")
            value = float(a["cvd_notional_usdt"])
            total = float(a["trade_notional_1h_usdt"])
            ts = a.get("window_end_utc") or p.get("generated_at_utc")
        elif isinstance(assets, dict):
            a = assets["BTC"]
            cvd_obj = a.get("CVD_USD") or {}
            value = float(cvd_obj["current"])
            total = float(
                (a.get("TRADE_NOTIONAL_1H_USDT") or {}).get("current")
                or a.get("trade_notional_1h_usdt")
                or 0.0
            )
            ts = a.get("window_end_utc") or p.get("generated_at_utc")
        else:
            raise ValueError("unsupported derivatives assets shape")

        age = age_hours(ts)
        if age is None or age > 4:
            raise ValueError(f"stale CVD age_hours={age}")

        ratio = value / total if total else None
        return {
            "status": "OK",
            "value": value,
            "cvd_to_volume_ratio": ratio,
            "trade_notional_1h_usdt": total if total else None,
            "source_time_utc": ts,
            "source": "derivatives/output/latest_microstructure.json (Bitget BTC 1H)",
        }
    except Exception as e:
        return {"status": "N/A", "value": None, "error": f"{type(e).__name__}: {e}"}


def sig_unreal(m: dict[str, Any]) -> tuple[str, str]:
    if m.get("status") != "OK":
        return "⚪", "N/A"
    v = float(m["value"])
    p = float(m["percentile_4y"])
    if v > 0 and p >= 90:
        return "🔴", "미실현 수익 과열권"
    if v > 0 and p >= 75:
        return "🟡", "미실현 수익 상단"
    if v > 0:
        return "🟡", "단기보유 미실현 수익권"
    return "🟢", "차익실현 잠재압력 낮음"


def sig_mvrv(m: dict[str, Any]) -> tuple[str, str]:
    if m.get("status") != "OK":
        return "⚪", "N/A"
    v = float(m["value"])
    p = float(m["percentile_4y"])
    if v > 1 and p >= 90:
        return "🔴", "단기보유자 수익 과열"
    if v > 1 and p >= 75:
        return "🟡", "단기보유자 수익권 상단"
    if v > 1:
        return "🟡", "단기보유자 수익권"
    return "🟢", "손익분기 이하/과열 아님"


def sig_sopr(m: dict[str, Any]) -> tuple[str, str]:
    if m.get("status") != "OK":
        return "⚪", "N/A"
    v = float(m["value"])
    p = float(m["percentile_4y"])
    a = m.get("avg_7d")
    if v > 1 and a is not None and float(a) > 1 and p >= 75:
        return "🔴", "실제 이익실현 압력 확인"
    if v > 1:
        return "🟡", "실제 이익실현 중"
    return "🟢", "이익실현 압력 낮음/손실실현"


def sig_whale(m: dict[str, Any]) -> tuple[str, str]:
    if m.get("status") != "OK" or m.get("net_ratio") is None:
        return "⚪", "N/A"
    r = float(m["net_ratio"])
    if r <= -0.10:
        return "🔴", "고래 순포지션 숏 우세"
    if r >= 0.10:
        return "🟢", "고래 순포지션 롱 우세"
    return "🟡", "고래 롱·숏 혼조"


def sig_cvd(m: dict[str, Any]) -> tuple[str, str]:
    if m.get("status") != "OK":
        return "⚪", "N/A"
    r = m.get("cvd_to_volume_ratio")
    v = float(m["value"])
    if r is not None:
        r = float(r)
        if r <= -0.05:
            return "🔴", "적극매도 우세"
        if r >= 0.05:
            return "🟢", "적극매수 우세"
        return "🟡", "매수·매도 혼조"
    if v <= -25_000_000:
        return "🔴", "적극매도 강함"
    if v <= -5_000_000:
        return "🟡", "매도 우세"
    if v >= 25_000_000:
        return "🟢", "적극매수 강함"
    return "🟡", "매수·매도 혼조"


def num(v: Any, n: int = 3) -> str:
    return "N/A" if v is None else f"{float(v):.{n}f}"


def money(v: Any) -> str:
    if v is None:
        return "N/A"
    x = float(v)
    sign = "-" if x < 0 else "+"
    x = abs(x)
    if x >= 1e9:
        return f"{sign}${x/1e9:.2f}B"
    if x >= 1e6:
        return f"{sign}${x/1e6:.1f}M"
    return f"{sign}${x:,.0f}"


def row(name: str, m: dict[str, Any], fn, display: str) -> dict[str, Any]:
    signal, state = fn(m)
    return {
        "signal": signal,
        "indicator": name,
        "display_value": display if signal != "⚪" else "N/A",
        "state": state,
        "raw": m,
    }


def build() -> dict[str, Any]:
    u = sth_metric("nupl")
    m = sth_metric("mvrv")
    s = sth_metric("sopr")
    w = whale()
    c = cvd()

    rows = [
        row(
            "STH 미실현 수익상태 (Whale 대체)",
            u,
            sig_unreal,
            f"STH-NUPL {num(u.get('value'))} / 4Y {num(u.get('percentile_4y'),1)}%ile",
        ),
        row(
            "STH-MVRV",
            m,
            sig_mvrv,
            f"{num(m.get('value'))} / 4Y {num(m.get('percentile_4y'),1)}%ile",
        ),
        row(
            "STH-SOPR",
            s,
            sig_sopr,
            f"{num(s.get('value'))} / 7D평균 {num(s.get('avg_7d'))}",
        ),
        row("Hyperliquid 고래 NET", w, sig_whale, money(w.get("value"))),
        row("BTC CVD", c, sig_cvd, money(c.get("value"))),
    ]

    available = [r for r in rows if r["signal"] in POINTS]
    risk_points = sum(POINTS[r["signal"]] for r in available)
    intensity = risk_points / len(available) if available else None

    if len(available) < 3 or intensity is None:
        overall_signal, level = "⚪", "확인 제한"
    elif intensity >= 0.80:
        overall_signal, level = "🔴", "매우 높음"
    elif intensity >= 0.60:
        overall_signal, level = "🔴", "높음"
    elif intensity >= 0.35:
        overall_signal, level = "🟡", "주의"
    else:
        overall_signal, level = "🟢", "낮음"

    return {
        "engine": ENGINE,
        "schema_version": SCHEMA,
        "generated_at_utc": iso(now()),
        "name_ko": "단기 고래 차익실현 위험",
        "role": "AUXILIARY_RISK_ONLY",
        "score_weight": 0,
        "exact_cryptoquant_sth_whale": False,
        "proxy_note": (
            "Exact CryptoQuant STH Whale P&L is not reproduced. "
            "Fresh public Checkonchain STH-NUPL/MVRV/SOPR plus current "
            "Hyperliquid whale NET and BTC CVD are combined."
        ),
        "rows": rows,
        "overall": {
            "signal": overall_signal,
            "level": level,
            "risk_points": round(risk_points, 2),
            "available_rows": len(available),
            "total_rows": 5,
            "coverage": round(len(available) / 5.0, 3),
            "method": (
                "red=1, yellow=0.5, green=0; equal-weight display-only auxiliary. "
                "<3 available rows => 확인 제한."
            ),
        },
        "guards": {
            "free_public_checkonchain_only_for_sth": True,
            "live_source_first": True,
            "github_fallback_requires_same_freshness_guard": True,
            "no_api_key": True,
            "no_paid_api": True,
            "no_backfill": True,
            "no_interpolation": True,
            "no_ocr": True,
            "no_guessing": True,
            "sth_source_freshness_max_hours": MAX_STH_AGE_HOURS,
            "cannot_alone_change": [
                "Market Positive Score",
                "BTC Liquidity Lead",
                "Crypto Money Inflow",
                "ALT Money Inflow",
                "final direction",
                "Risk Veto",
                "WATCH",
            ],
        },
        "failures": [
            {"indicator": r["indicator"], "error": r["raw"].get("error")}
            for r in rows
            if r["signal"] == "⚪"
        ],
    }


def append_history(payload: dict[str, Any]) -> None:
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "generated_at_utc",
        "risk_level",
        "risk_signal",
        "risk_points",
        "coverage",
        "sth_nupl",
        "sth_mvrv",
        "sth_sopr",
        "whale_net_usd",
        "btc_cvd_usdt",
    ]
    r = {x["indicator"]: x for x in payload["rows"]}
    rec = {
        "generated_at_utc": payload["generated_at_utc"],
        "risk_level": payload["overall"]["level"],
        "risk_signal": payload["overall"]["signal"],
        "risk_points": payload["overall"]["risk_points"],
        "coverage": payload["overall"]["coverage"],
        "sth_nupl": r["STH 미실현 수익상태 (Whale 대체)"]["raw"].get("value"),
        "sth_mvrv": r["STH-MVRV"]["raw"].get("value"),
        "sth_sopr": r["STH-SOPR"]["raw"].get("value"),
        "whale_net_usd": r["Hyperliquid 고래 NET"]["raw"].get("value"),
        "btc_cvd_usdt": r["BTC CVD"]["raw"].get("value"),
    }
    exists = HISTORY.exists() and HISTORY.stat().st_size > 0
    with HISTORY.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow(rec)


def main() -> None:
    payload = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    append_history(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
