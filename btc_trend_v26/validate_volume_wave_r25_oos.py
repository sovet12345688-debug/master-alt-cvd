from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from volume_wave_engine_r24 import (
    INTERVAL_MS,
    MIN_BARS,
    classify_tf,
    combine_primary,
    combine_strict,
)

HISTORY_URL = "https://api.bitget.com/api/v3/market/history-candles"
CATEGORY = "USDT-FUTURES"
SYMBOL = "BTCUSDT"
EVAL_DAYS = 720
RECENT_EXCLUSION_DAYS = 120
WARMUP_DAYS = 60

# Predeclared before this OOS run. Do not tune after seeing the result.
GATES = {
    "min_total_signals": 40,
    "min_total_hit_rate_pct": 53.0,
    "avg_signed_forward_return_must_be_positive": True,
    "min_holdout_signals": 8,
    "min_holdout_hit_rate_pct": 50.0,
    "holdout_avg_signed_forward_return_must_be_positive": True,
    "min_positive_folds": 3,
    "fold_count": 4,
}


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _request(interval: str, start_ms: int, end_ms: int) -> list[list[str]]:
    r = requests.get(
        HISTORY_URL,
        params={
            "category": CATEGORY,
            "symbol": SYMBOL,
            "interval": interval,
            "startTime": str(start_ms),
            "endTime": str(end_ms),
            "type": "market",
            "limit": "100",
        },
        timeout=20,
        headers={"User-Agent": "btc-trend-v26-volume-wave-r25-oos/1.0"},
    )
    r.raise_for_status()
    payload = r.json()
    if not isinstance(payload, dict) or str(payload.get("code")) != "00000":
        raise RuntimeError(f"Bitget history error: {payload!r}")
    data = payload.get("data") or []
    if not isinstance(data, list):
        raise RuntimeError("Bitget history data is not a list")
    return data


def fetch_history(interval: str, start: datetime, end: datetime) -> list[dict[str, float]]:
    if interval not in INTERVAL_MS:
        raise RuntimeError(f"unsupported interval {interval}")
    # history-candles max range is 90d and max 100 rows. Use conservative windows.
    chunk = timedelta(days=80 if interval == "1D" else 15)
    cursor = start
    rows_by_ts: dict[int, dict[str, float]] = {}
    start_ms_all = int(start.timestamp() * 1000)
    end_ms_all = int(end.timestamp() * 1000)

    while cursor < end:
        chunk_end = min(cursor + chunk, end)
        raw = _request(interval, int(cursor.timestamp() * 1000), int(chunk_end.timestamp() * 1000))
        for row in raw:
            if not isinstance(row, list) or len(row) < 7:
                continue
            try:
                ts = int(row[0])
                o, h, l, c = map(float, row[1:5])
                quote_vol = float(row[6])
            except (TypeError, ValueError):
                continue
            if ts < start_ms_all or ts > end_ms_all:
                continue
            if ts + INTERVAL_MS[interval] > end_ms_all + INTERVAL_MS[interval]:
                continue
            rows_by_ts[ts] = {
                "ts": float(ts),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": quote_vol,
            }
        cursor = chunk_end
        time.sleep(0.06)

    rows = [rows_by_ts[k] for k in sorted(rows_by_ts)]
    if len(rows) < MIN_BARS + 2:
        raise RuntimeError(f"{interval}: insufficient historical rows: {len(rows)}")
    return rows


def _h4_asof(rows: list[dict[str, float]], asof_ms: int) -> list[dict[str, float]]:
    return [r for r in rows if int(r["ts"]) + INTERVAL_MS["4H"] <= asof_ms]


def evaluate(
    d1_rows: list[dict[str, float]],
    h4_rows: list[dict[str, float]],
    eval_start: datetime,
    eval_end: datetime,
    variant: str,
) -> dict[str, Any]:
    combine = combine_strict if variant == "A_STRICT" else combine_primary
    start_ms = int(eval_start.timestamp() * 1000)
    end_ms = int(eval_end.timestamp() * 1000)
    records: list[dict[str, Any]] = []
    insufficient = true_neutral = 0

    for i in range(MIN_BARS - 1, len(d1_rows) - 1):
        signal_asof = int(d1_rows[i]["ts"]) + INTERVAL_MS["1D"]
        if signal_asof < start_ms or signal_asof > end_ms:
            continue
        h4 = _h4_asof(h4_rows, signal_asof)
        if len(h4) < MIN_BARS:
            continue
        d1_rec = classify_tf(d1_rows[: i + 1])
        h4_rec = classify_tf(h4)
        decision = combine(d1_rec, h4_rec)
        if decision["decision_status"] != "CURRENT":
            insufficient += 1
            continue
        state = decision["state"]
        if state == "NEUTRAL":
            true_neutral += 1
            continue
        if state not in {"LONG", "SHORT"}:
            insufficient += 1
            continue
        fwd = d1_rows[i + 1]["close"] / d1_rows[i]["close"] - 1.0
        signed = fwd if state == "LONG" else -fwd
        records.append({
            "signal_asof_ms": signal_asof,
            "state": state,
            "signed_forward_return": signed,
            "hit": signed > 0,
        })

    def summarize(sub: list[dict[str, Any]]) -> dict[str, Any]:
        n = len(sub)
        hits = sum(1 for r in sub if r["hit"])
        avg = sum(r["signed_forward_return"] for r in sub) / n if n else None
        return {
            "signals": n,
            "hits": hits,
            "hit_rate_pct": round(100.0 * hits / n, 2) if n else None,
            "avg_signed_forward_return_pct": round(100.0 * avg, 4) if avg is not None else None,
            "long_signals": sum(1 for r in sub if r["state"] == "LONG"),
            "short_signals": sum(1 for r in sub if r["state"] == "SHORT"),
        }

    total = summarize(records)
    span_ms = end_ms - start_ms
    folds = []
    for idx in range(GATES["fold_count"]):
        lo = start_ms + span_ms * idx // GATES["fold_count"]
        hi = start_ms + span_ms * (idx + 1) // GATES["fold_count"]
        sub = [r for r in records if lo <= r["signal_asof_ms"] < (hi if idx < GATES["fold_count"] - 1 else hi + 1)]
        folds.append({
            "fold": idx + 1,
            "start_utc": iso(datetime.fromtimestamp(lo / 1000, tz=timezone.utc)),
            "end_utc": iso(datetime.fromtimestamp(hi / 1000, tz=timezone.utc)),
            **summarize(sub),
        })

    holdout_start_ms = start_ms + span_ms * 3 // 4
    holdout = summarize([r for r in records if r["signal_asof_ms"] >= holdout_start_ms])
    return {
        "variant": variant,
        **total,
        "true_neutral": true_neutral,
        "insufficient_evidence": insufficient,
        "folds": folds,
        "holdout_last_25pct": {
            "start_utc": iso(datetime.fromtimestamp(holdout_start_ms / 1000, tz=timezone.utc)),
            **holdout,
        },
    }


def validate(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    eval_end = now - timedelta(days=RECENT_EXCLUSION_DAYS)
    eval_start = eval_end - timedelta(days=EVAL_DAYS)
    fetch_start = eval_start - timedelta(days=WARMUP_DAYS)
    fetch_end = eval_end + timedelta(days=2)

    d1 = fetch_history("1D", fetch_start, fetch_end)
    h4 = fetch_history("4H", fetch_start, fetch_end)
    strict = evaluate(d1, h4, eval_start, eval_end, "A_STRICT")
    primary = evaluate(d1, h4, eval_start, eval_end, "B_1D_PRIMARY")

    positive_folds = sum(
        1 for f in strict["folds"]
        if f["signals"] > 0 and (f["avg_signed_forward_return_pct"] or 0.0) > 0.0
    )
    holdout = strict["holdout_last_25pct"]
    checks = {
        "total_signal_count": strict["signals"] >= GATES["min_total_signals"],
        "total_hit_rate": (strict["hit_rate_pct"] or 0.0) >= GATES["min_total_hit_rate_pct"],
        "total_positive_signed_return": (strict["avg_signed_forward_return_pct"] or 0.0) > 0.0,
        "holdout_signal_count": holdout["signals"] >= GATES["min_holdout_signals"],
        "holdout_hit_rate": (holdout["hit_rate_pct"] or 0.0) >= GATES["min_holdout_hit_rate_pct"],
        "holdout_positive_signed_return": (holdout["avg_signed_forward_return_pct"] or 0.0) > 0.0,
        "fold_stability": positive_folds >= GATES["min_positive_folds"],
    }
    promotion_gate = "PASS" if all(checks.values()) else "HOLD"
    return {
        "schema_version": "1.0",
        "validation_id": "BTC_TREND_V26_VOLUME_WAVE_R25_EXTENDED_OOS",
        "status": "TECHNICAL_PASS",
        "candidate_fixed_before_run": "A_STRICT",
        "anti_tuning": "R2.4 thresholds and A_STRICT semantics frozen; R2.5 only expands unseen historical evaluation window.",
        "evaluation_window": {
            "start_utc": iso(eval_start),
            "end_utc": iso(eval_end),
            "recent_exclusion_days": RECENT_EXCLUSION_DAYS,
            "eval_days": EVAL_DAYS,
            "warmup_days": WARMUP_DAYS,
        },
        "source": {
            "venue": "Bitget USDT Futures",
            "symbol": SYMBOL,
            "endpoint": "/api/v3/market/history-candles",
            "d1_rows": len(d1),
            "h4_rows": len(h4),
            "completed_candle_only": True,
        },
        "predeclared_gates": GATES,
        "checks": checks,
        "positive_folds": positive_folds,
        "promotion_gate": promotion_gate,
        "A_STRICT": strict,
        "B_1D_PRIMARY_DIAGNOSTIC": primary,
        "production_approved": False,
        "official_state_write_allowed": False,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    try:
        out = validate()
    except Exception as exc:
        out = {
            "schema_version": "1.0",
            "validation_id": "BTC_TREND_V26_VOLUME_WAVE_R25_EXTENDED_OOS",
            "status": "VALIDATION_FAIL",
            "error": f"{type(exc).__name__}: {exc}",
            "promotion_gate": "HOLD",
            "production_approved": False,
            "official_state_write_allowed": False,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("status") == "TECHNICAL_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
