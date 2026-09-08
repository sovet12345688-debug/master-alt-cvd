#!/usr/bin/env python3
"""MASTER MARKET free/no-key short-term whale profit-taking risk proxy.

This is NOT CryptoQuant's exact STH Whale Unrealized P&L.
It uses three publicly visible, no-login Glassnode STH latest-value pages plus
existing MASTER MARKET Hyperliquid whale NET and Bitget BTC CVD.
No paid API, API key, backfill, interpolation, OCR, or guessed value.
"""
from __future__ import annotations

import csv
import html as html_lib
import json
import math
import re
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
SCHEMA = "1.1"
GLASSNODE = {
    "STH_NUPL": "https://studio.glassnode.com/charts/indicators.NuplLess155?a=BTC&category=&zoom=all",
    "STH_MVRV": "https://studio.glassnode.com/charts/market.MvrvLess155?a=BTC&chartStyle=line&ema=0&resolution=24h",
    "STH_SOPR": "https://studio.glassnode.com/charts/indicators.SoprLess155?a=BTC&mAvg=",
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
    return None if d is None else (now() - d).total_seconds() / 3600


def fetch_text(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 MASTER-MARKET-free-proxy/1.2",
            "Accept": "text/html,application/xhtml+xml,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def visible_text(raw: str) -> str:
    # Keep JSON/script text too because some public chart pages server-embed Latest Values there.
    text = html_lib.unescape(raw)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\\u003c[^>]*?\\u003e", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text)
    return text


def extract_latest_value(raw: str) -> tuple[float, str]:
    text = visible_text(raw)
    candidates = []
    for marker in ("Latest Values", "Latest Value"):
        pos = text.lower().find(marker.lower())
        if pos >= 0:
            candidates.append(text[pos : pos + 1200])
    candidates.append(text)

    patterns = [
        r"Latest Values?\s*(?:as of [^0-9+-]{0,40})?\s*([-+]?\d+(?:\.\d+)?)\s*(\d+\s+(?:minutes?|hours?|days?)\s+ago)",
        r"Latest Values?\s*([-+]?\d+(?:\.\d+)?)\s*(24\s+hours\s+ago)",
        r"([-+]?\d+(?:\.\d+)?)\s*(24\s+hours\s+ago)",
    ]
    for chunk in candidates:
        for pat in patterns:
            m = re.search(pat, chunk, flags=re.I)
            if m:
                return float(m.group(1)), m.group(2).strip()
    raise ValueError("public Latest Values number/freshness label not found")


def freshness_hours(label: str) -> float | None:
    m = re.match(r"\s*(\d+)\s+(minute|minutes|hour|hours|day|days)\s+ago\s*", label, re.I)
    if not m:
        return None
    n = float(m.group(1))
    unit = m.group(2).lower()
    if unit.startswith("minute"):
        return n / 60.0
    if unit.startswith("hour"):
        return n
    return n * 24.0


def glassnode_metric(key: str) -> dict[str, Any]:
    url = GLASSNODE[key]
    try:
        raw = fetch_text(url)
        value, label = extract_latest_value(raw)
        fh = freshness_hours(label)
        if fh is None or fh > 72:
            raise ValueError(f"stale/unknown public freshness: {label}")
        return {
            "status": "OK",
            "value": value,
            "freshness_label": label,
            "freshness_hours": fh,
            "retrieved_at_utc": iso(now()),
            "source": "Glassnode Studio public chart page (visible Latest Values; no login/API key)",
            "source_url": url,
        }
    except Exception as e:
        return {
            "status": "N/A",
            "value": None,
            "source": "Glassnode Studio public chart page (visible Latest Values; no login/API key)",
            "source_url": url,
            "error": f"{type(e).__name__}: {e}",
        }


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}


def whale() -> dict[str, Any]:
    try:
        c = read_json(ACTOR)["whale"]["BTC"]["current"]
        net, longv, shortv = float(c["net_usd"]), float(c["long_usd"]), float(c["short_usd"])
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
        a = next(x for x in p["assets"] if x.get("symbol") == "BTCUSDT")
        value = float(a["cvd_notional_usdt"])
        total = float(a["trade_notional_1h_usdt"])
        ts = a.get("window_end_utc") or p.get("generated_at_utc")
        age = age_hours(ts)
        if age is None or age > 4:
            raise ValueError(f"stale CVD age_hours={age}")
        return {
            "status": "OK",
            "value": value,
            "cvd_to_volume_ratio": value / total if total else None,
            "trade_notional_1h_usdt": total,
            "source_time_utc": ts,
            "source": "derivatives/output/latest_microstructure.json (Bitget BTC 1H)",
        }
    except Exception as e:
        return {"status": "N/A", "value": None, "error": f"{type(e).__name__}: {e}"}


def sig_nupl(m: dict[str, Any]) -> tuple[str, str]:
    if m.get("status") != "OK":
        return "⚪", "N/A"
    v = float(m["value"])
    if v >= 0.25:
        return "🔴", "단기보유 미실현 수익 높음"
    if v >= 0.10:
        return "🟡", "단기보유 미실현 수익권"
    return "🟢", "차익실현 잠재압력 낮음"


def sig_mvrv(m: dict[str, Any]) -> tuple[str, str]:
    if m.get("status") != "OK":
        return "⚪", "N/A"
    v = float(m["value"])
    if v >= 1.35:
        return "🔴", "단기보유자 수익 과열"
    if v >= 1.15:
        return "🟡", "단기보유자 수익권 상단"
    if v > 1.0:
        return "🟡", "단기보유자 수익권"
    return "🟢", "단기보유자 손실권/과열 아님"


def sig_sopr(m: dict[str, Any]) -> tuple[str, str]:
    if m.get("status") != "OK":
        return "⚪", "N/A"
    v = float(m["value"])
    if v >= 1.03:
        return "🔴", "실제 이익실현 강함"
    if v > 1.0:
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
    if m.get("status") != "OK" or m.get("cvd_to_volume_ratio") is None:
        return "⚪", "N/A"
    r = float(m["cvd_to_volume_ratio"])
    if r <= -0.05:
        return "🔴", "적극매도 우세"
    if r >= 0.05:
        return "🟢", "적극매수 우세"
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


def row(name: str, metric: dict[str, Any], fn, display: str) -> dict[str, Any]:
    signal, state = fn(metric)
    return {
        "signal": signal,
        "indicator": name,
        "display_value": display if signal != "⚪" else "N/A",
        "state": state,
        "raw": metric,
    }


def build() -> dict[str, Any]:
    nupl = glassnode_metric("STH_NUPL")
    mvrv = glassnode_metric("STH_MVRV")
    sopr = glassnode_metric("STH_SOPR")
    w = whale()
    c = cvd()

    rows = [
        row("STH 미실현 수익상태 (Whale 대체)", nupl, sig_nupl, f"STH-NUPL {num(nupl.get('value'))}"),
        row("STH-MVRV", mvrv, sig_mvrv, num(mvrv.get("value"))),
        row("STH-SOPR", sopr, sig_sopr, num(sopr.get("value"))),
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
        overall_signal, level = "🟡", "중간"
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
        "proxy_note": "Exact CryptoQuant STH Whale P&L is not reproduced. Public STH-NUPL/MVRV/SOPR plus current Hyperliquid whale NET and BTC CVD are combined.",
        "rows": rows,
        "overall": {
            "signal": overall_signal,
            "level": level,
            "risk_points": round(risk_points, 2),
            "available_rows": len(available),
            "total_rows": 5,
            "coverage": round(len(available) / 5.0, 3),
            "method": "red=1, yellow=0.5, green=0; equal-weight display-only auxiliary. <3 available rows => 확인 제한.",
        },
        "guards": {
            "free_public_pages_only_for_sth": True,
            "no_api_key": True,
            "no_paid_api": True,
            "no_backfill": True,
            "no_interpolation": True,
            "no_ocr": True,
            "no_guessing": True,
            "sth_page_freshness_max_hours": 72,
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
            for r in rows if r["signal"] == "⚪"
        ],
    }


def append_history(payload: dict[str, Any]) -> None:
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "generated_at_utc", "risk_level", "risk_signal", "risk_points", "coverage",
        "sth_nupl", "sth_mvrv", "sth_sopr", "whale_net_usd", "btc_cvd_usdt",
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
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    append_history(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
