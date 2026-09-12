#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "market_vault/output/latest_summary.json"
OUT = ROOT / "market_vault/output/latest_oil.json"
HEADERS = {"User-Agent": "Mozilla/5.0 MASTER-MARKET-OIL/1.0"}
SERIES = {
    "WTI": {"symbol": "CL=F", "label": "WTI front-month futures"},
    "BRENT": {"symbol": "BZ=F", "label": "Brent front-month futures"},
}


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def f(value: Any) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must be object")
    return data


def fetch_chart(symbol: str) -> dict[str, Any]:
    urls = [
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}",
    ]
    params = {"range": "1mo", "interval": "1d", "events": "history"}
    last: Exception | None = None
    for url in urls:
        for attempt in range(2):
            try:
                r = requests.get(url, params=params, headers=HEADERS, timeout=20)
                r.raise_for_status()
                doc = r.json()
                result = (((doc.get("chart") or {}).get("result")) or [None])[0]
                if not isinstance(result, dict):
                    raise RuntimeError(f"Yahoo chart result missing for {symbol}")
                return result
            except Exception as exc:
                last = exc
                if attempt == 0:
                    time.sleep(0.8)
    raise RuntimeError(f"Yahoo fetch failed for {symbol}: {last}")


def actual_points(result: dict[str, Any]) -> list[tuple[datetime, float]]:
    stamps = result.get("timestamp") or []
    quote = (((result.get("indicators") or {}).get("quote")) or [{}])[0]
    closes = quote.get("close") or []
    pts: list[tuple[datetime, float]] = []
    for ts, close in zip(stamps, closes):
        value = f(close)
        if value is None:
            continue
        try:
            dt = datetime.fromtimestamp(int(ts), tz=UTC)
        except Exception:
            continue
        pts.append((dt, value))
    if len(pts) < 2:
        raise RuntimeError("insufficient actual daily observations")
    pts.sort(key=lambda x: x[0])
    return pts


def nearest(points: list[tuple[datetime, float]], target: datetime, tolerance_hours: float) -> tuple[datetime, float] | None:
    candidates = [p for p in points if p[0] < points[-1][0]]
    if not candidates:
        return None
    best = min(candidates, key=lambda p: abs((p[0] - target).total_seconds()))
    gap = abs((best[0] - target).total_seconds()) / 3600.0
    return best if gap <= tolerance_hours else None


def delta_block(current: float, prior: tuple[datetime, float] | None, target: datetime) -> dict[str, Any] | None:
    if prior is None:
        return None
    dt, value = prior
    delta = current - value
    return {
        "previous_value": value,
        "delta": delta,
        "delta_pct": (delta / abs(value) * 100.0) if value else None,
        "previous_snapshot_hour_utc": iso(dt),
        "source_observation_time": iso(dt),
        "target_gap_minutes": abs((dt - target).total_seconds()) / 60.0,
    }


def build_metric(metric: str, symbol: str, label: str) -> dict[str, Any]:
    points = actual_points(fetch_chart(symbol))
    current_dt, current = points[-1]
    previous = points[-2]
    p1 = nearest(points, current_dt - timedelta(days=1), 36)
    p3 = nearest(points, current_dt - timedelta(days=3), 48)
    p7 = nearest(points, current_dt - timedelta(days=7), 72)
    block = {
        "metric": metric,
        "value": current,
        "unit": "USD/barrel",
        "source": f"Yahoo Finance:{symbol}",
        "source_label": label,
        "source_observation_time": iso(current_dt),
        "source_frequency": "daily_market",
        "timestamp_quality": "exchange_chart_timestamp",
        "new_source_observation_since_prior_snapshot": True,
        "vs_prior_snapshot": delta_block(current, previous, current_dt),
        "vs_24h": delta_block(current, p1, current_dt),
        "vs_3d": delta_block(current, p3, current_dt),
        "vs_7d": delta_block(current, p7, current_dt),
    }
    if block["vs_24h"] is None or block["vs_3d"] is None or block["vs_7d"] is None:
        raise RuntimeError(f"{metric} comparison window missing")
    return block


def main() -> int:
    summary = load_json(SUMMARY)
    generated = summary.get("generated_at_utc")
    if not isinstance(generated, str):
        raise SystemExit("OIL_ADAPTER_BLOCKED_SUMMARY_TIMESTAMP")
    summary_dt = datetime.fromisoformat(generated.replace("Z", "+00:00"))
    if summary_dt.tzinfo is None:
        summary_dt = summary_dt.replace(tzinfo=UTC)
    age = (datetime.now(UTC) - summary_dt.astimezone(UTC)).total_seconds() / 60.0
    if age > 120:
        raise SystemExit(f"OIL_ADAPTER_BLOCKED_STALE_SUMMARY age_min={age:.1f}")

    built = [build_metric(name, cfg["symbol"], cfg["label"]) for name, cfg in SERIES.items()]
    names = set(SERIES)
    metrics = [m for m in summary.get("metrics", []) if isinstance(m, dict) and m.get("metric") not in names]
    metrics.extend(built)
    metrics.sort(key=lambda x: str(x.get("metric") or ""))
    summary["metrics"] = metrics
    collection = summary.setdefault("collection", {})
    collection["oil_adapter"] = {
        "status": "OK",
        "engine": "MASTER_MARKET_OIL_ADAPTER_V1",
        "sources": ["Yahoo Finance:CL=F", "Yahoo Finance:BZ=F"],
        "actual_observations_only": True,
        "generated_at_utc": iso(datetime.now(UTC)),
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    out = {
        "engine": "MASTER_MARKET_OIL_ADAPTER_V1",
        "schema_version": "1.0",
        "generated_at_utc": iso(datetime.now(UTC)),
        "metrics": built,
        "rule": "actual Yahoo CL=F/BZ=F observations only; no interpolation or cross-instrument substitution",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("MASTER_MARKET_OIL_ADAPTER=PASS " + " ".join(f"{m['metric']}={m['value']:.4f}" for m in built))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
