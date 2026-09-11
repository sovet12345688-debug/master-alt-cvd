from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

BITGET_CANDLES = "https://api.bitget.com/api/v3/market/candles"
INTERVAL_MS = {"4H": 4 * 60 * 60 * 1000, "1D": 24 * 60 * 60 * 1000}
MIN_BARS = 16


class VolumeWaveError(RuntimeError):
    pass


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _get_json(url: str, params: dict[str, Any]) -> Any:
    r = requests.get(url, params=params, timeout=20, headers={"User-Agent": "btc-trend-v26-volume-wave-r23/1.1"})
    r.raise_for_status()
    return r.json()


def fetch_completed_ohlcv(interval: str, now: datetime | None = None, limit: int = 300) -> list[dict[str, float]]:
    if interval not in INTERVAL_MS:
        raise VolumeWaveError(f"unsupported interval: {interval}")
    now = now or datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    payload = _get_json(BITGET_CANDLES, {
        "category": "USDT-FUTURES",
        "symbol": "BTCUSDT",
        "interval": interval,
        "type": "market",
        "limit": str(limit),
    })
    if not isinstance(payload, dict) or str(payload.get("code")) != "00000":
        raise VolumeWaveError(f"Bitget candles error: {payload!r}")

    out: dict[int, dict[str, float]] = {}
    for row in payload.get("data") or []:
        if not isinstance(row, list) or len(row) < 6:
            continue
        try:
            ts = int(row[0])
            if ts + INTERVAL_MS[interval] > now_ms:
                continue
            o, h, l, c = map(float, row[1:5])
            base_vol = float(row[5])
            quote_vol = float(row[6]) if len(row) > 6 else base_vol
            vals = (o, h, l, c, quote_vol)
            if not all(math.isfinite(v) for v in vals) or quote_vol < 0:
                continue
        except (TypeError, ValueError):
            continue
        out[ts] = {
            "ts": float(ts),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": quote_vol,
        }
    rows = [out[k] for k in sorted(out)]
    if len(rows) < MIN_BARS:
        raise VolumeWaveError(f"{interval}: insufficient completed bars: {len(rows)}")
    return rows


def mean(xs: list[float]) -> float:
    if not xs:
        raise VolumeWaveError("empty mean input")
    return sum(xs) / len(xs)


def atr14(rows: list[dict[str, float]]) -> float:
    if len(rows) < 15:
        raise VolumeWaveError("need at least 15 bars for ATR14")
    trs: list[float] = []
    for i in range(len(rows) - 14, len(rows)):
        r = rows[i]
        prev_close = rows[i - 1]["close"]
        tr = max(
            r["high"] - r["low"],
            abs(r["high"] - prev_close),
            abs(r["low"] - prev_close),
        )
        trs.append(tr)
    value = mean(trs)
    if not math.isfinite(value) or value <= 0:
        raise VolumeWaveError("invalid ATR14")
    return value


def classify_tf(rows: list[dict[str, float]]) -> dict[str, Any]:
    if len(rows) < MIN_BARS:
        raise VolumeWaveError(f"need at least {MIN_BARS} completed bars")
    cur = rows[-1]
    prev = rows[-2]

    prior_vols = [x["volume"] for x in rows[:-1]]
    ma5 = mean(prior_vols[-5:])
    ma10 = mean(prior_vols[-10:])
    vol = cur["volume"]
    volume_confirmed = vol >= ma5 and vol >= ma10

    impulse = cur["close"] - prev["close"]
    body = cur["close"] - cur["open"]
    impulse_sign = 1 if impulse > 0 else -1 if impulse < 0 else 0
    body_sign = 1 if body > 0 else -1 if body < 0 else 0
    price_body_coherent = impulse_sign != 0 and impulse_sign == body_sign

    atr = atr14(rows)
    wave_energy_atr = abs(impulse) / atr
    wave_confirmed = wave_energy_atr >= 1.0

    evidence_confirmed = price_body_coherent and (volume_confirmed or wave_confirmed)
    if evidence_confirmed and impulse_sign > 0:
        state = "LONG"
    elif evidence_confirmed and impulse_sign < 0:
        state = "SHORT"
    else:
        state = "NEUTRAL"

    candle_range = max(cur["high"] - cur["low"], 0.0)
    body_efficiency = abs(body) / candle_range if candle_range > 0 else 0.0

    return {
        "state": state,
        "completed_ts_ms": int(cur["ts"]),
        "close": cur["close"],
        "previous_close": prev["close"],
        "return_1bar_pct": (cur["close"] / prev["close"] - 1.0) * 100.0 if prev["close"] else 0.0,
        "volume": vol,
        "volume_ma5_prior": ma5,
        "volume_ma10_prior": ma10,
        "volume_ratio_ma5": vol / ma5 if ma5 else None,
        "volume_ratio_ma10": vol / ma10 if ma10 else None,
        "volume_confirmed": volume_confirmed,
        "atr14": atr,
        "wave_energy_atr": wave_energy_atr,
        "wave_confirmed": wave_confirmed,
        "body_efficiency": body_efficiency,
        "price_body_coherent": price_body_coherent,
        "evidence_confirmed": evidence_confirmed,
    }


def combine_states(d1: dict[str, Any], h4: dict[str, Any]) -> str:
    # R2.3 conservative candidate: 1D and 4H must independently agree.
    # STRONG_* is intentionally forbidden in shadow validation.
    if d1["state"] == h4["state"] and d1["state"] in {"LONG", "SHORT"}:
        return str(d1["state"])
    return "NEUTRAL"


def replay_tf(rows: list[dict[str, float]]) -> dict[str, Any]:
    signals = hits = longs = shorts = 0
    signed_forward_returns: list[float] = []
    for i in range(MIN_BARS - 1, len(rows) - 1):
        rec = classify_tf(rows[: i + 1])
        state = rec["state"]
        if state not in {"LONG", "SHORT"}:
            continue
        signals += 1
        longs += int(state == "LONG")
        shorts += int(state == "SHORT")
        fwd = rows[i + 1]["close"] / rows[i]["close"] - 1.0
        signed = fwd if state == "LONG" else -fwd
        signed_forward_returns.append(signed)
        if signed > 0:
            hits += 1
    return {
        "signals": signals,
        "hits": hits,
        "hit_rate_pct": round(100.0 * hits / signals, 2) if signals else None,
        "avg_signed_forward_return_pct": round(100.0 * mean(signed_forward_returns), 4) if signed_forward_returns else None,
        "long_signals": longs,
        "short_signals": shorts,
        "observations": max(0, len(rows) - MIN_BARS),
    }


def build_candidate(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    d1_rows = fetch_completed_ohlcv("1D", now)
    h4_rows = fetch_completed_ohlcv("4H", now)
    d1 = classify_tf(d1_rows)
    h4 = classify_tf(h4_rows)
    state = combine_states(d1, h4)
    feature_ts_ms = max(
        d1["completed_ts_ms"] + INTERVAL_MS["1D"],
        h4["completed_ts_ms"] + INTERVAL_MS["4H"],
    )
    return {
        "schema_version": "1.1",
        "engine_id": "BTC_TREND_V26_VOLUME_WAVE_R23",
        "status": "SHADOW_CANDIDATE",
        "asof_utc": iso(now),
        "feature": {
            "availability": "CURRENT",
            "state": state,
            "feature_timestamp": iso(datetime.fromtimestamp(feature_ts_ms / 1000, tz=timezone.utc)),
            "source_timestamps": {
                "bitget_1d_completed_open_ms": str(d1["completed_ts_ms"]),
                "bitget_4h_completed_open_ms": str(h4["completed_ts_ms"]),
            },
            "lineage": {
                "engine": "BTC_TREND_V26_VOLUME_WAVE_R23",
                "source": "Bitget USDT Futures BTCUSDT",
                "completed_candle_only": True,
                "strong_states_allowed": False,
                "rule": "each TF requires coherent close impulse/body plus either volume >= prior MA5&MA10 or abs(close-prev_close) >= 1 ATR14; final direction requires 1D+4H agreement",
            },
            "details": {"1D": d1, "4H": h4},
        },
        "replay_diagnostics": {
            "1D": replay_tf(d1_rows),
            "4H": replay_tf(h4_rows),
        },
        "production_approved": False,
        "official_state_write_allowed": False,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="V2.6 Volume/Wave R2.3 shadow classifier")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    try:
        out = build_candidate()
    except Exception as exc:
        out = {
            "schema_version": "1.1",
            "engine_id": "BTC_TREND_V26_VOLUME_WAVE_R23",
            "status": "VALIDATION_FAIL",
            "error": f"{type(exc).__name__}: {exc}",
            "production_approved": False,
            "official_state_write_allowed": False,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("status") != "VALIDATION_FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
