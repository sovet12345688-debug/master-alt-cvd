from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "onchain"
OUT_DIR = BASE / "output"
STATE_DIR = BASE / "state"
SUMMARY_JSON = OUT_DIR / "latest_summary.json"
STATE_PATH = STATE_DIR / "collector_state.json"

BASE_URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "MASTER-MARKET-ONCHAIN/1.0"})

CORE_METRICS = [
    "SOPR",
    "SOPRSth155d",
    "CapMVRVCur",
    "CapRealUSD",
    "SplyCur",
    "PriceUSD",
    "NUPL",
    "AdrActCnt",
    "TxTfrValAdjUSD",
]

EXCHANGE_FLOW_METRICS = {
    "Binance": ("FlowInBNBUSD", "FlowOutBNBUSD"),
    "Coinbase": ("FlowInCBSUSD", "FlowOutCBSUSD"),
    "Kraken": ("FlowInKRKUSD", "FlowOutKRKUSD"),
    "OKX": ("FlowInOKXUSD", "FlowOutOKXUSD"),
    "Bybit": ("FlowInBITUSD", "FlowOutBITUSD"),
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def safe_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_metric(metric: str, days: int = 12) -> tuple[list[dict[str, Any]], str | None]:
    end = now_utc().date() + timedelta(days=1)
    start = end - timedelta(days=days)
    params = {
        "assets": "btc",
        "metrics": metric,
        "frequency": "1d",
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "page_size": "100",
    }
    try:
        r = SESSION.get(BASE_URL, params=params, timeout=25)
        if r.status_code != 200:
            return [], f"HTTP {r.status_code}: {r.text[:160]}"
        data = r.json().get("data") or []
        rows: list[dict[str, Any]] = []
        for x in data:
            if metric not in x:
                continue
            val = safe_float(x.get(metric))
            if val is None:
                continue
            rows.append({"time": x.get("time"), "value": val})
        rows.sort(key=lambda x: str(x.get("time") or ""))
        return rows, None if rows else "metric unavailable or empty in Community API"
    except Exception as e:
        return [], f"{type(e).__name__}: {str(e)[:180]}"


def value_days_ago(rows: list[dict[str, Any]], days: int) -> float | None:
    if not rows:
        return None
    latest_time = datetime.fromisoformat(str(rows[-1]["time"]).replace("Z", "+00:00"))
    target = latest_time - timedelta(days=days)
    best = None
    best_dt = None
    for x in rows:
        try:
            dt = datetime.fromisoformat(str(x["time"]).replace("Z", "+00:00"))
        except Exception:
            continue
        if dt <= target and (best_dt is None or dt > best_dt):
            best = x["value"]
            best_dt = dt
    return best


def delta(cur: float | None, past: float | None) -> float | None:
    if cur is None or past is None:
        return None
    return cur - past


def metric_payload(metric: str, rows: list[dict[str, Any]], error: str | None) -> dict[str, Any]:
    current = rows[-1]["value"] if rows else None
    return {
        "metric": metric,
        "status": "OK" if rows else "N/A",
        "observation_time": rows[-1]["time"] if rows else None,
        "current": current,
        "1D_delta": delta(current, value_days_ago(rows, 1)),
        "3D_delta": delta(current, value_days_ago(rows, 3)),
        "7D_delta": delta(current, value_days_ago(rows, 7)),
        "error": error,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    metrics: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for metric in CORE_METRICS:
        rows, err = fetch_metric(metric)
        metrics[metric] = metric_payload(metric, rows, err)
        if err:
            errors[metric] = err

    cap_real = metrics.get("CapRealUSD", {}).get("current")
    supply = metrics.get("SplyCur", {}).get("current")
    realized_price = (cap_real / supply) if cap_real is not None and supply not in (None, 0) else None

    exchange_rows: dict[str, Any] = {}
    aggregate_in = 0.0
    aggregate_out = 0.0
    usable_exchange_count = 0
    for exchange, (in_metric, out_metric) in EXCHANGE_FLOW_METRICS.items():
        in_rows, in_err = fetch_metric(in_metric)
        out_rows, out_err = fetch_metric(out_metric)
        in_cur = in_rows[-1]["value"] if in_rows else None
        out_cur = out_rows[-1]["value"] if out_rows else None
        status = "OK" if in_cur is not None and out_cur is not None else "N/A"
        net = (in_cur - out_cur) if status == "OK" else None
        exchange_rows[exchange] = {
            "status": status,
            "inflow_usd": in_cur,
            "outflow_usd": out_cur,
            "netflow_usd": net,
            "in_metric": in_metric,
            "out_metric": out_metric,
            "error": " | ".join(x for x in [in_err, out_err] if x) or None,
        }
        if status == "OK":
            usable_exchange_count += 1
            aggregate_in += float(in_cur)
            aggregate_out += float(out_cur)

    aggregate_net = aggregate_in - aggregate_out if usable_exchange_count else None

    payload = {
        "engine": "MASTER_MARKET_ONCHAIN_V1",
        "source": "Coin Metrics Community API",
        "generated_at_utc": iso(now_utc()),
        "asset": "BTC",
        "frequency": "1d",
        "metrics": metrics,
        "derived": {
            "aggregate_realized_price_usd": realized_price,
            "aggregate_realized_price_rule": "CapRealUSD / SplyCur; this is aggregate realized price, NOT STH realized price.",
        },
        "exchange_netflow": {
            "status": "OK" if usable_exchange_count else "N/A",
            "covered_exchanges": usable_exchange_count,
            "configured_exchanges": len(EXCHANGE_FLOW_METRICS),
            "inflow_usd": aggregate_in if usable_exchange_count else None,
            "outflow_usd": aggregate_out if usable_exchange_count else None,
            "netflow_usd": aggregate_net,
            "by_exchange": exchange_rows,
            "rule": "Only Coin Metrics exchange-attributed flows that are actually returned are summed. Missing exchanges are not treated as zero.",
        },
        "interpretation_rules": {
            "SOPR": "Above 1 generally means spent coins realize profit; below 1 means realized loss.",
            "SOPRSth155d": "Short-term holder SOPR using 155-day segmentation when available from source.",
            "CapMVRVCur": "Whole-network MVRV; this is not STH-MVRV.",
            "secondary_only": "On-chain block is secondary confirmation only and cannot flip direction or cross a MASTER threshold by itself.",
        },
        "availability_note": "Community API availability varies by metric. Unsupported/pro-only fields remain explicit N/A; no substitute number is invented.",
        "errors": errors,
    }
    SUMMARY_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    state = {
        "last_run_utc": iso(now_utc()),
        "ok_core_metrics": sum(v.get("status") == "OK" for v in metrics.values()),
        "na_core_metrics": sum(v.get("status") != "OK" for v in metrics.values()),
        "exchange_flow_coverage": usable_exchange_count,
        "status": "OK" if any(v.get("status") == "OK" for v in metrics.values()) else "N/A",
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
