#!/usr/bin/env python3
"""Lightweight free/no-key proxy for short-term whale profit-taking risk.

This does NOT reproduce CryptoQuant's exact STH Whale Unrealized P&L.
It combines three public STH cohort metrics with existing MASTER MARKET
Hyperliquid whale NET and Bitget BTC CVD. No backfill/interpolation/guessing.
"""
from __future__ import annotations

import csv
import json
import math
import statistics
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "onchain" / "output" / "latest_short_term_whale_risk.json"
HISTORY = ROOT / "onchain" / "data" / "short_term_whale_risk_history.csv"
ACTOR = ROOT / "market_vault" / "output" / "latest_actor_flows.json"
MICRO = ROOT / "derivatives" / "output" / "latest_microstructure.json"

ENGINE = "MASTER_ST_WHALE_PROFIT_TAKING_RISK_PROXY_V1"
SCHEMA = "1.0"
SOURCE_BASE = "https://raw.githubusercontent.com/checkmatey/checkonchain.com/main"
CHECKONCHAIN = {
    "STH_UNREALIZED_PROXY": (
        f"{SOURCE_BASE}/btconchain/unrealised/nupl_bycohort/nupl_bycohort_light.html",
        [("sth", "nupl"), ("short-term", "nupl"), ("short term", "nupl")],
        ["lth", "price", "z-score", "zscore"],
    ),
    "STH_MVRV": (
        f"{SOURCE_BASE}/btconchain/unrealised/mvrv_sth/mvrv_sth_light.html",
        [("sth", "mvrv"), ("short-term", "mvrv"), ("short term", "mvrv")],
        ["price", "realised price", "realized price", "cost basis", "z-score", "zscore"],
    ),
    "STH_SOPR": (
        f"{SOURCE_BASE}/btconchain/realised/sthsopr_indicator/sthsopr_indicator_light.html",
        [("sth", "sopr"), ("short-term", "sopr"), ("short term", "sopr")],
        ["price", "sma", "threshold", "band"],
    ),
}
SIGNAL_POINTS = {"🔴": 1.0, "🟡": 0.5, "🟢": 0.0}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def fetch_text(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "MASTER-MARKET-free-proxy/1.0", "Accept": "text/html,text/plain,*/*"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def balanced_json_array(text: str, start: int) -> str:
    if start < 0 or start >= len(text) or text[start] != "[":
        raise ValueError("array start not found")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError("unterminated Plotly data array")


def extract_plotly_traces(html: str) -> list[dict[str, Any]]:
    anchor = html.find("Plotly.newPlot")
    if anchor < 0:
        raise ValueError("Plotly.newPlot not found")
    start = html.find("[", anchor)
    traces = json.loads(balanced_json_array(html, start))
    if not isinstance(traces, list):
        raise ValueError("Plotly trace payload is not a list")
    return [x for x in traces if isinstance(x, dict)]


def has_numeric_y(trace: dict[str, Any]) -> bool:
    y = trace.get("y")
    if not isinstance(y, list):
        return False
    return any(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))
        for v in y[-100:]
    )


def choose_trace(traces: list[dict[str, Any]], groups: list[tuple[str, ...]], excludes: list[str]) -> dict[str, Any]:
    def name(t: dict[str, Any]) -> str:
        return str(t.get("name") or "").strip().lower()

    for t in traces:
        n = name(t)
        if not has_numeric_y(t) or any(ex in n for ex in excludes):
            continue
        if any(all(tok in n for tok in grp) for grp in groups):
            return t

    generic_ex = excludes + ["price", "btc", "bitcoin", "threshold", "sma", "band"]
    candidates = [t for t in traces if has_numeric_y(t) and not any(ex in name(t) for ex in generic_ex)]
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError(f"target trace ambiguous/not found; traces={[str(t.get('name')) for t in traces[:20]]}")


def parse_dt(v: Any) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        d = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


def series_stats(trace: dict[str, Any]) -> dict[str, Any]:
    x = trace.get("x") or []
    y = trace.get("y") or []
    pairs: list[tuple[datetime | None, float]] = []
    for i, v in enumerate(y):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        fv = float(v)
        if not math.isfinite(fv):
            continue
        pairs.append((parse_dt(x[i]) if i < len(x) else None, fv))
    if not pairs:
        raise ValueError("no numeric series values")

    latest_dt, current = pairs[-1]
    if latest_dt:
        hist = [v for dt, v in pairs if dt is not None and dt >= latest_dt - timedelta(days=1461)]
        last7 = [v for dt, v in pairs if dt is not None and dt >= latest_dt - timedelta(days=7)]
    else:
        hist = [v for _, v in pairs[-1460:]]
        last7 = [v for _, v in pairs[-7:]]
    if len(hist) < 30:
        hist = [v for _, v in pairs]
    percentile = 100.0 * sum(1 for v in hist if v <= current) / max(1, len(hist))
    return {
        "trace_name": str(trace.get("name") or ""),
        "source_time_utc": iso(latest_dt),
        "value": current,
        "percentile_4y": percentile,
        "avg_7d": statistics.fmean(last7) if last7 else None,
        "history_points_4y": len(hist),
    }


def checkonchain_metric(key: str) -> dict[str, Any]:
    url, groups, excludes = CHECKONCHAIN[key]
    try:
        html = fetch_text(url)
        trace = choose_trace(extract_plotly_traces(html), groups, excludes)
        p = series_stats(trace)
        p.update({"status": "OK", "source": "Checkonchain public static Plotly HTML", "source_url": url})
        return p
    except Exception as e:
        return {
            "status": "N/A",
            "value": None,
            "percentile_4y": None,
            "avg_7d": None,
            "source": "Checkonchain public static Plotly HTML",
            "source_url": url,
            "error": f"{type(e).__name__}: {e}",
        }


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}


def age_hours(ts: str | None) -> float | None:
    dt = parse_dt(ts)
    return None if not dt else (utcnow() - dt).total_seconds() / 3600.0


def whale_net_btc() -> dict[str, Any]:
    p = read_json(ACTOR)
    try:
        current = p["whale"]["BTC"]["current"]
        net = float(current["net_usd"])
        long_usd = float(current["long_usd"])
        short_usd = float(current["short_usd"])
        gross = long_usd + short_usd
        ratio = net / gross if gross > 0 else None
        ts = current.get("snapshot_time_utc")
        age = age_hours(ts)
        if age is None or age > 6:
            raise ValueError(f"stale actor flow: age_hours={age}")
        return {
            "status": "OK", "value": net, "unit": "USD", "net_ratio": ratio,
            "long_usd": long_usd, "short_usd": short_usd, "source_time_utc": ts,
            "source": "market_vault/output/latest_actor_flows.json (Hyperliquid >=$20M aggregate)",
        }
    except Exception as e:
        return {"status": "N/A", "value": None, "error": f"{type(e).__name__}: {e}"}


def cvd_btc() -> dict[str, Any]:
    p = read_json(MICRO)
    try:
        if p.get("engine") != "MASTER_DERIVATIVES_MICROSTRUCTURE_V2":
            raise ValueError("unexpected derivatives engine")
        a = next(x for x in p["assets"] if x.get("symbol") == "BTCUSDT")
        cvd = float(a["cvd_notional_usdt"])
        total = float(a["trade_notional_1h_usdt"])
        ratio = cvd / total if total > 0 else None
        ts = a.get("window_end_utc") or p.get("generated_at_utc")
        age = age_hours(ts)
        if age is None or age > 4:
            raise ValueError(f"stale microstructure: age_hours={age}")
        return {
            "status": "OK", "value": cvd, "unit": "USDT notional",
            "cvd_to_volume_ratio": ratio, "trade_notional_1h_usdt": total,
            "source_time_utc": ts,
            "source": "derivatives/output/latest_microstructure.json (Bitget USDT Futures, 1H)",
        }
    except Exception as e:
        return {"status": "N/A", "value": None, "error": f"{type(e).__name__}: {e}"}


def classify_sth_unrealized(m: dict[str, Any]) -> tuple[str, str]:
    if m.get("status") != "OK": return "⚪", "N/A"
    v, pct = float(m["value"]), float(m["percentile_4y"])
    if v > 0 and pct >= 90: return "🔴", "미실현 수익 과열권"
    if v > 0 and pct >= 75: return "🟡", "미실현 수익 상단"
    return "🟢", "차익실현 잠재압력 낮음"


def classify_mvrv(m: dict[str, Any]) -> tuple[str, str]:
    if m.get("status") != "OK": return "⚪", "N/A"
    v, pct = float(m["value"]), float(m["percentile_4y"])
    if v > 1 and pct >= 90: return "🔴", "단기보유자 수익 과열"
    if v > 1 and pct >= 75: return "🟡", "단기보유자 수익권 상단"
    if v > 1: return "🟡", "단기보유자 수익권"
    return "🟢", "손익분기 이하/과열 아님"


def classify_sopr(m: dict[str, Any]) -> tuple[str, str]:
    if m.get("status") != "OK": return "⚪", "N/A"
    v, pct, avg7 = float(m["value"]), float(m["percentile_4y"]), m.get("avg_7d")
    if v > 1 and avg7 is not None and float(avg7) > 1 and pct >= 75:
        return "🔴", "실제 이익실현 압력 확인"
    if v > 1: return "🟡", "이익실현 중"
    return "🟢", "이익실현 압력 낮음"


def classify_whale(m: dict[str, Any]) -> tuple[str, str]:
    if m.get("status") != "OK" or m.get("net_ratio") is None: return "⚪", "N/A"
    r = float(m["net_ratio"])
    if r <= -0.10: return "🔴", "고래 순포지션 숏 우세"
    if r >= 0.10: return "🟢", "고래 순포지션 롱 우세"
    return "🟡", "고래 롱·숏 혼조"


def classify_cvd(m: dict[str, Any]) -> tuple[str, str]:
    if m.get("status") != "OK" or m.get("cvd_to_volume_ratio") is None: return "⚪", "N/A"
    r = float(m["cvd_to_volume_ratio"])
    if r <= -0.05: return "🔴", "적극매도 우세"
    if r >= 0.05: return "🟢", "적극매수 우세"
    return "🟡", "매수·매도 혼조"


def fmt_num(v: Any, decimals: int = 3) -> str:
    return "N/A" if v is None else f"{float(v):.{decimals}f}"


def fmt_usd(v: Any) -> str:
    if v is None: return "N/A"
    x = float(v); sign = "-" if x < 0 else "+"; x = abs(x)
    if x >= 1e9: return f"{sign}${x/1e9:.2f}B"
    if x >= 1e6: return f"{sign}${x/1e6:.1f}M"
    return f"{sign}${x:,.0f}"


def make_row(name: str, metric: dict[str, Any], classifier, display_value: str) -> dict[str, Any]:
    signal, state = classifier(metric)
    return {"signal": signal, "indicator": name, "display_value": display_value if signal != "⚪" else "N/A", "state": state, "raw": metric}


def build() -> dict[str, Any]:
    sth_unreal = checkonchain_metric("STH_UNREALIZED_PROXY")
    sth_mvrv = checkonchain_metric("STH_MVRV")
    sth_sopr = checkonchain_metric("STH_SOPR")
    whale = whale_net_btc()
    cvd = cvd_btc()

    rows = [
        make_row("STH 미실현 수익상태 (Whale 대체)", sth_unreal, classify_sth_unrealized,
                 f"{fmt_num(sth_unreal.get('value'))} / 4Y {fmt_num(sth_unreal.get('percentile_4y'),1)}%ile"),
        make_row("STH-MVRV", sth_mvrv, classify_mvrv,
                 f"{fmt_num(sth_mvrv.get('value'))} / 4Y {fmt_num(sth_mvrv.get('percentile_4y'),1)}%ile"),
        make_row("STH-SOPR", sth_sopr, classify_sopr,
                 f"{fmt_num(sth_sopr.get('value'))} / 7D평균 {fmt_num(sth_sopr.get('avg_7d'))}"),
        make_row("Hyperliquid 고래 NET", whale, classify_whale, fmt_usd(whale.get("value"))),
        make_row("BTC CVD", cvd, classify_cvd, fmt_usd(cvd.get("value"))),
    ]
    available = [r for r in rows if r["signal"] in SIGNAL_POINTS]
    points = sum(SIGNAL_POINTS[r["signal"]] for r in available)
    intensity = points / len(available) if available else None
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
        "generated_at_utc": iso(utcnow()),
        "name_ko": "단기 고래 차익실현 위험",
        "role": "AUXILIARY_RISK_ONLY",
        "score_weight": 0,
        "exact_cryptoquant_sth_whale": False,
        "proxy_note": "Exact CryptoQuant STH Whale P&L is not reproduced. STH cohort profit-state proxies + current whale futures NET + BTC CVD are combined.",
        "rows": rows,
        "overall": {
            "signal": overall_signal, "level": level, "risk_points": round(points, 2),
            "available_rows": len(available), "total_rows": 5,
            "coverage": round(len(available) / 5.0, 3),
            "method": "red=1, yellow=0.5, green=0; equal-weight auxiliary. <3 available rows => 확인 제한.",
        },
        "guards": {
            "no_api_key": True, "no_paid_source": True, "no_backfill": True,
            "no_interpolation": True, "no_guessing": True,
            "cannot_alone_change": ["Market Positive Score", "BTC Liquidity Lead", "Crypto Money Inflow", "ALT Money Inflow", "final direction", "Risk Veto", "WATCH"],
        },
        "failures": [{"indicator": r["indicator"], "error": r["raw"].get("error")} for r in rows if r["signal"] == "⚪"],
    }


def append_history(p: dict[str, Any]) -> None:
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    fields = ["generated_at_utc", "risk_level", "risk_signal", "risk_points", "coverage", "sth_unrealized_value", "sth_mvrv", "sth_sopr", "whale_net_usd", "btc_cvd_usdt"]
    rows = {r["indicator"]: r for r in p["rows"]}
    record = {
        "generated_at_utc": p["generated_at_utc"], "risk_level": p["overall"]["level"],
        "risk_signal": p["overall"]["signal"], "risk_points": p["overall"]["risk_points"], "coverage": p["overall"]["coverage"],
        "sth_unrealized_value": rows["STH 미실현 수익상태 (Whale 대체)"]["raw"].get("value"),
        "sth_mvrv": rows["STH-MVRV"]["raw"].get("value"), "sth_sopr": rows["STH-SOPR"]["raw"].get("value"),
        "whale_net_usd": rows["Hyperliquid 고래 NET"]["raw"].get("value"), "btc_cvd_usdt": rows["BTC CVD"]["raw"].get("value"),
    }
    exists = HISTORY.exists() and HISTORY.stat().st_size > 0
    with HISTORY.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if not exists: w.writeheader()
        w.writerow(record)


def main() -> None:
    p = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(p, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    append_history(p)
    print(json.dumps(p, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
