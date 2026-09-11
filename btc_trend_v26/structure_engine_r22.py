from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

HERE = Path(__file__).resolve().parent
CONTRACT_PATH = HERE / "structure_contract_r22.json"
BITGET_CANDLES = "https://api.bitget.com/api/v3/market/candles"
BITGET_HISTORY_CANDLES = "https://api.bitget.com/api/v3/market/history-candles"
DAY_MS = 24 * 60 * 60 * 1000
INTERVAL_MS = {
    "4H": 4 * 60 * 60 * 1000,
    "1D": DAY_MS,
    "1W": 7 * DAY_MS,
}


class StructureEngineError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_contract() -> dict[str, Any]:
    raw = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise StructureEngineError("structure contract root must be object")
    return raw


def get_json(url: str, params: dict[str, Any]) -> Any:
    r = requests.get(
        url,
        params=params,
        timeout=20,
        headers={"User-Agent": "btc-trend-v26-r22-structure-shadow/1.0"},
    )
    r.raise_for_status()
    return r.json()


def _bitget_rows(url: str, interval: str, **extra: str) -> list[list[Any]]:
    params = {
        "category": "USDT-FUTURES",
        "symbol": "BTCUSDT",
        "interval": interval,
        "type": "market",
        **extra,
    }
    payload = get_json(url, params)
    if not isinstance(payload, dict) or str(payload.get("code")) != "00000":
        raise StructureEngineError(f"Bitget candles error: {payload!r}")
    rows = payload.get("data") or []
    if not isinstance(rows, list):
        raise StructureEngineError(f"unexpected Bitget candle payload: {interval}")
    return rows


def _parse_completed(rows: list[list[Any]], interval: str, now_ms: int) -> dict[int, dict[str, Any]]:
    duration = INTERVAL_MS[interval]
    parsed: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 5:
            continue
        try:
            open_ms = int(row[0])
            o = float(row[1])
            h = float(row[2])
            l = float(row[3])
            c = float(row[4])
        except (TypeError, ValueError):
            continue
        close_ms = open_ms + duration
        if close_ms > now_ms:
            continue
        if not all(math.isfinite(x) for x in (o, h, l, c)):
            continue
        if h < max(o, c) or l > min(o, c) or h < l:
            continue
        parsed[open_ms] = {
            "open_time_ms": open_ms,
            "close_time_ms": close_ms,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
        }
    return parsed


def fetch_completed_ohlc(interval: str, now: datetime, minimum: int) -> list[dict[str, Any]]:
    if interval not in INTERVAL_MS:
        raise StructureEngineError(f"unsupported interval: {interval}")
    now_ms = int(now.timestamp() * 1000)
    all_rows: dict[int, dict[str, Any]] = {}

    recent = _bitget_rows(BITGET_CANDLES, interval, limit="1000")
    all_rows.update(_parse_completed(recent, interval, now_ms))

    loops = 0
    while len(all_rows) < minimum and loops < 24:
        loops += 1
        end_ms = (min(all_rows) - 1) if all_rows else (now_ms - 1)
        start_ms = end_ms - 89 * DAY_MS
        history = _bitget_rows(
            BITGET_HISTORY_CANDLES,
            interval,
            startTime=str(start_ms),
            endTime=str(end_ms),
            limit="100",
        )
        parsed = _parse_completed(history, interval, now_ms)
        before = len(all_rows)
        all_rows.update(parsed)
        if len(all_rows) == before:
            break

    ordered = [all_rows[k] for k in sorted(all_rows)]
    if len(ordered) < minimum:
        raise StructureEngineError(
            f"{interval}: only {len(ordered)} completed Bitget bars after history pagination; need {minimum}"
        )
    return ordered


def confirmed_pivots(
    bars: list[dict[str, Any]], left: int, right: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if left < 1 or right < 1:
        raise StructureEngineError("pivot left/right must be >=1")
    highs: list[dict[str, Any]] = []
    lows: list[dict[str, Any]] = []
    n = len(bars)
    for i in range(left, n - right):
        h = float(bars[i]["high"])
        l = float(bars[i]["low"])
        left_highs = [float(bars[j]["high"]) for j in range(i - left, i)]
        right_highs = [float(bars[j]["high"]) for j in range(i + 1, i + right + 1)]
        left_lows = [float(bars[j]["low"]) for j in range(i - left, i)]
        right_lows = [float(bars[j]["low"]) for j in range(i + 1, i + right + 1)]
        if all(h > x for x in left_highs + right_highs):
            highs.append({
                "index": i,
                "price": h,
                "open_time_ms": int(bars[i]["open_time_ms"]),
                "confirmed_after_index": i + right,
            })
        if all(l < x for x in left_lows + right_lows):
            lows.append({
                "index": i,
                "price": l,
                "open_time_ms": int(bars[i]["open_time_ms"]),
                "confirmed_after_index": i + right,
            })
    return highs, lows


def classify_structure(
    bars: list[dict[str, Any]], left: int, right: int
) -> dict[str, Any]:
    if len(bars) < max(left + right + 5, 12):
        raise StructureEngineError("insufficient bars for structure classification")
    highs, lows = confirmed_pivots(bars, left, right)
    if len(highs) < 2 or len(lows) < 2:
        raise StructureEngineError(
            f"insufficient confirmed swings: highs={len(highs)} lows={len(lows)}"
        )

    prev_high, last_high = highs[-2], highs[-1]
    prev_low, last_low = lows[-2], lows[-1]
    latest_close = float(bars[-1]["close"])
    previous_close = float(bars[-2]["close"])

    hh = last_high["price"] > prev_high["price"]
    lh = last_high["price"] < prev_high["price"]
    hl = last_low["price"] > prev_low["price"]
    ll = last_low["price"] < prev_low["price"]
    bos_up = latest_close > float(last_high["price"])
    bos_down = latest_close < float(last_low["price"])
    reclaim = previous_close <= float(last_high["price"]) and bos_up
    breakdown = previous_close >= float(last_low["price"]) and bos_down
    bull_sequence = hh and hl
    bear_sequence = lh and ll

    if bos_up and bull_sequence:
        state = "STRONG_LONG"
    elif bos_down and bear_sequence:
        state = "STRONG_SHORT"
    elif bos_up:
        state = "LONG"
    elif bos_down:
        state = "SHORT"
    elif bull_sequence:
        state = "LONG"
    elif bear_sequence:
        state = "SHORT"
    else:
        state = "NEUTRAL"

    if bos_up and bos_down:
        raise StructureEngineError("invalid simultaneous BOS_UP and BOS_DOWN")

    return {
        "state": state,
        "facts": {
            "HH": hh,
            "LH": lh,
            "HL": hl,
            "LL": ll,
            "BOS_UP": bos_up,
            "BOS_DOWN": bos_down,
            "RECLAIM": reclaim,
            "BREAKDOWN": breakdown,
        },
        "latest_close": latest_close,
        "previous_close": previous_close,
        "previous_swing_high": prev_high,
        "latest_swing_high": last_high,
        "previous_swing_low": prev_low,
        "latest_swing_low": last_low,
        "confirmed_high_count": len(highs),
        "confirmed_low_count": len(lows),
        "pivot_left": left,
        "pivot_right": right,
    }


def _current_record(
    feature_id: str,
    interval: str,
    result: dict[str, Any],
    bars: list[dict[str, Any]],
) -> dict[str, Any]:
    close_ts = iso(datetime.fromtimestamp(int(bars[-1]["close_time_ms"]) / 1000, tz=timezone.utc))
    return {
        "availability": "CURRENT",
        "state": result["state"],
        "feature_timestamp": close_ts,
        "source_timestamps": {"latest_completed_candle": close_ts},
        "lineage": {
            "engine": "R2.2",
            "contract": "btc_trend_v26/structure_contract_r22.json",
            "source": "Bitget USDT Futures BTCUSDT",
            "interval": interval,
            "completed_candle_only": True,
            "cross_venue_fallback": False,
            "lookahead": False,
        },
        "details": result,
    }


def build_structure_features(now: datetime | None = None) -> dict[str, dict[str, Any]]:
    now = now or utc_now()
    contract = load_contract()
    out: dict[str, dict[str, Any]] = {}
    for feature_id, cfg in contract["timeframes"].items():
        try:
            interval = str(cfg["interval"])
            minimum = int(cfg["minimum_completed_bars"])
            bars = fetch_completed_ohlc(interval, now, minimum)
            result = classify_structure(
                bars,
                left=int(cfg["pivot_left"]),
                right=int(cfg["pivot_right"]),
            )
            out[feature_id] = _current_record(feature_id, interval, result, bars)
        except StructureEngineError as exc:
            reason = "N_A_INSUFFICIENT_HISTORY" if "insufficient" in str(exc) or "only " in str(exc) else "N_A_VALIDATION_FAIL"
            out[feature_id] = {"availability": reason, "note": str(exc)}
        except Exception as exc:
            out[feature_id] = {
                "availability": "N_A_SOURCE_MISSING",
                "note": f"{type(exc).__name__}: {str(exc)[:180]}",
            }
    return out
