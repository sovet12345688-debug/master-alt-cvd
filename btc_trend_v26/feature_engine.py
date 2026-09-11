from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

HERE = Path(__file__).resolve().parent
CONTRACT_PATH = HERE / "feature_engine_contract.json"
RAW_BASE = "https://raw.githubusercontent.com/sovet12345688-debug/master-alt-cvd/main"
BITGET_CANDLES = "https://api.bitget.com/api/v3/market/candles"
BITGET_HISTORY_CANDLES = "https://api.bitget.com/api/v3/market/history-candles"
INTERVAL_MS = {"4H": 4 * 60 * 60 * 1000, "1D": 24 * 60 * 60 * 1000}
DAY_MS = 24 * 60 * 60 * 1000
TREND_IDS = [
    "STRUCTURE_1D", "STRUCTURE_4H", "STRUCTURE_1W", "VOLUME_WAVE",
    "EMA_MA_STACK", "SPOT_ETF_FLOW", "DERIVATIVES_STATE",
    "VALUE_OVEREXTENSION", "LIQUIDITY_MACRO",
]


class FeatureEngineError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def load_json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise FeatureEngineError(f"JSON root must be object: {path}")
    return obj


def get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    r = requests.get(url, params=params, timeout=20, headers={"User-Agent": "btc-trend-v26-r21-shadow/1.2"})
    r.raise_for_status()
    return r.json()


def remote_main(path: str) -> dict[str, Any]:
    obj = get_json(f"{RAW_BASE}/{path}")
    if not isinstance(obj, dict):
        raise FeatureEngineError(f"remote JSON root not object: {path}")
    return obj


def sma(values: list[float], n: int) -> float:
    if len(values) < n:
        raise FeatureEngineError(f"need {n} bars, have {len(values)}")
    return sum(values[-n:]) / n


def ema(values: list[float], n: int) -> float:
    if len(values) < n:
        raise FeatureEngineError(f"need {n} bars, have {len(values)}")
    alpha = 2.0 / (n + 1.0)
    out = values[0]
    for x in values[1:]:
        out = alpha * x + (1.0 - alpha) * out
    return out


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
        raise FeatureEngineError(f"Bitget candles error: {payload!r}")
    rows = payload.get("data") or []
    if not isinstance(rows, list):
        raise FeatureEngineError(f"unexpected Bitget candle payload: {interval}")
    return rows


def _parse_completed(rows: list[list[Any]], interval: str, now_ms: int) -> dict[int, tuple[float, int]]:
    duration = INTERVAL_MS[interval]
    parsed: dict[int, tuple[float, int]] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 5:
            continue
        try:
            open_ms = int(row[0])
            close_boundary_ms = open_ms + duration
            close = float(row[4])
        except (TypeError, ValueError):
            continue
        if close_boundary_ms > now_ms or not math.isfinite(close):
            continue
        parsed[open_ms] = (close, close_boundary_ms)
    return parsed


def fetch_completed_closes(interval: str, now: datetime, minimum: int = 205) -> tuple[list[float], str]:
    if interval not in INTERVAL_MS:
        raise FeatureEngineError(f"unsupported interval: {interval}")
    now_ms = int(now.timestamp() * 1000)
    all_rows: dict[int, tuple[float, int]] = {}

    # Recent endpoint. Bitget exposes the recent access window; for 1D this alone is
    # intentionally insufficient for MA200, so history is appended below.
    recent = _bitget_rows(BITGET_CANDLES, interval, limit="1000")
    all_rows.update(_parse_completed(recent, interval, now_ms))

    # Walk backwards in <=90-day history windows until the indicator warm-up is met.
    # The history endpoint returns up to 100 rows and does not require a cursor.
    loops = 0
    while len(all_rows) < minimum and loops < 8:
        loops += 1
        if all_rows:
            earliest_open = min(all_rows)
            end_ms = earliest_open - 1
        else:
            end_ms = now_ms - 1
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

    ordered = sorted(all_rows.items(), key=lambda x: x[0])
    if len(ordered) < minimum:
        raise FeatureEngineError(f"{interval}: only {len(ordered)} completed Bitget bars after history pagination")
    ordered = ordered[-max(minimum, 260):]
    closes = [x[1][0] for x in ordered]
    latest_close_boundary = ordered[-1][1][1]
    return closes, iso(datetime.fromtimestamp(latest_close_boundary / 1000, tz=timezone.utc))


def stack_bits(closes: list[float]) -> dict[str, Any]:
    close = closes[-1]
    e20 = ema(closes, 20)
    m50 = sma(closes, 50)
    m200 = sma(closes, 200)
    bull = [close > e20, e20 > m50, m50 > m200]
    bear = [close < e20, e20 < m50, m50 < m200]
    return {
        "close": close, "ema20": e20, "ma50": m50, "ma200": m200,
        "bull_count": sum(bull), "bear_count": sum(bear),
        "full_bull": all(bull), "full_bear": all(bear),
    }


def classify_ema_stack(d1: dict[str, Any], h4: dict[str, Any]) -> str:
    if d1["full_bull"] and h4["full_bull"]:
        return "STRONG_LONG"
    if d1["full_bear"] and h4["full_bear"]:
        return "STRONG_SHORT"
    bulls = int(d1["bull_count"]) + int(h4["bull_count"])
    bears = int(d1["bear_count"]) + int(h4["bear_count"])
    if bulls >= 4 and not d1["full_bear"]:
        return "LONG"
    if bears >= 4 and not d1["full_bull"]:
        return "SHORT"
    return "NEUTRAL"


def classify_etf(asset: dict[str, Any]) -> str:
    vals = [asset.get("flow_1d_usd_m"), asset.get("flow_3d_usd_m"), asset.get("flow_7d_usd_m")]
    if any(v is None for v in vals):
        raise FeatureEngineError("BTC ETF 1D/3D/7D flow missing")
    nums = [float(v) for v in vals]
    if all(v > 0 for v in nums):
        return "LONG"
    if all(v < 0 for v in nums):
        return "SHORT"
    return "NEUTRAL"


def classify_derivatives(raw_state: str, cvd: float, buy: float, sell: float) -> str:
    base_state = raw_state.split("|", 1)[0]
    mapping = {
        "PRICE_UP_OI_UP": "LONG",
        "PRICE_UP_OI_UP_LONG_BUILD": "LONG",
        "PRICE_UP_OI_DOWN_SQUEEZE_RISK": "LONG",
        "PRICE_DOWN_OI_UP_BEARISH_BUILD": "SHORT",
        "DELEVERAGING": "SHORT",
        "PRICE_DOWN_OI_DOWN_DELEVERAGING": "SHORT",
        "MIXED": "NEUTRAL",
    }
    base = mapping.get(base_state)
    if base is None:
        raise FeatureEngineError(f"unsupported derivatives state: {raw_state}")
    micro = "NEUTRAL"
    if cvd > 0 and buy > sell:
        micro = "LONG"
    elif cvd < 0 and sell > buy:
        micro = "SHORT"
    if base in {"LONG", "SHORT"} and micro in {"LONG", "SHORT"} and base != micro:
        return "NEUTRAL"
    return base


def current_record(state: str, timestamp: str, source_timestamps: dict[str, str], lineage: dict[str, Any], details: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "availability": "CURRENT",
        "state": state,
        "feature_timestamp": timestamp,
        "source_timestamps": source_timestamps,
        "lineage": lineage,
    }
    if details:
        out["details"] = details
    return out


def na_record(reason: str, note: str) -> dict[str, Any]:
    return {"availability": reason, "note": note}


def age_minutes(ts: str, now: datetime) -> float:
    return max(0.0, (now - parse_iso(ts)).total_seconds() / 60.0)


def build_live_features(now: datetime | None = None) -> dict[str, Any]:
    now = now or utc_now()
    contract = load_json(CONTRACT_PATH)
    features: dict[str, Any] = {}
    for fid, reason in contract["forced_na_until_validation"].items():
        features[fid] = na_record(reason, "R2.1 intentionally fail-closed until classifier/threshold validation")

    try:
        d1_closes, d1_ts = fetch_completed_closes("1D", now)
        h4_closes, h4_ts = fetch_completed_closes("4H", now)
        d1 = stack_bits(d1_closes)
        h4 = stack_bits(h4_closes)
        state = classify_ema_stack(d1, h4)
        ft = max(parse_iso(d1_ts), parse_iso(h4_ts))
        features["EMA_MA_STACK"] = current_record(
            state, iso(ft), {"bitget_1d_completed": d1_ts, "bitget_4h_completed": h4_ts},
            {"engine": "R2.1", "source": "Bitget USDT Futures BTCUSDT", "completed_candle_only": True, "cross_venue_fallback": False, "history_pagination": True},
            {"1D": d1, "4H": h4},
        )
    except Exception as exc:
        features["EMA_MA_STACK"] = na_record("N_A_SOURCE_MISSING", f"EMA/MA runtime unavailable: {type(exc).__name__}: {str(exc)[:160]}")

    try:
        etf = remote_main("market_vault/output/latest_etf_flows.json")
        generated = str(etf["generated_at_utc"])
        if age_minutes(generated, now) > 24 * 60:
            raise FeatureEngineError("ETF artifact stale")
        btc = next(x for x in etf.get("assets", []) if x.get("asset") == "BTC")
        state = classify_etf(btc)
        features["SPOT_ETF_FLOW"] = current_record(
            state, generated,
            {"artifact_generated_at": generated, "latest_trading_date": str(btc.get("latest_trading_date"))},
            {"engine": "R2.1", "source": "market_vault/output/latest_etf_flows.json@main", "strong_band_used": False},
            {k: btc.get(k) for k in ("flow_1d_usd_m", "flow_3d_usd_m", "flow_7d_usd_m")},
        )
    except Exception as exc:
        features["SPOT_ETF_FLOW"] = na_record("N_A_SOURCE_MISSING", f"ETF runtime unavailable: {type(exc).__name__}: {str(exc)[:120]}")

    try:
        summary = remote_main("derivatives/output/latest_summary.json")
        micro = remote_main("derivatives/output/latest_microstructure.json")
        health = remote_main("derivatives/state/collector_state.json")
        s_ts = str(summary["snapshot_time_utc"])
        m_ts = str(micro["generated_at_utc"])
        if age_minutes(s_ts, now) > 90 or age_minutes(m_ts, now) > 90:
            raise FeatureEngineError("derivatives source stale")
        if health.get("current_collection_status") != "OK":
            raise FeatureEngineError("derivatives health not OK")
        s_btc = next(x for x in summary.get("assets", []) if x.get("symbol") == "BTCUSDT")
        m_btc = next(x for x in micro.get("assets", []) if x.get("symbol") == "BTCUSDT")
        if s_btc.get("status") != "OK" or m_btc.get("status") != "OK":
            raise FeatureEngineError("BTC derivatives record not OK")
        cvd = float(m_btc["cvd_notional_usdt"])
        buy = float(m_btc["taker_buy_ratio"])
        sell = float(m_btc["taker_sell_ratio"])
        raw_state = str(s_btc["derivatives_state"])
        state = classify_derivatives(raw_state, cvd, buy, sell)
        features["DERIVATIVES_STATE"] = current_record(
            state, max(s_ts, m_ts),
            {"summary": s_ts, "microstructure": m_ts, "health": str(health.get("current_health_checked_at_utc"))},
            {"engine": "R2.1", "venue": str(s_btc.get("venue")), "raw_derivatives_state": raw_state, "cross_venue_repair": False},
            {"cvd_1h_usdt": cvd, "taker_buy_ratio": buy, "taker_sell_ratio": sell, "oi_24h_available": s_btc.get("oi_change_24h_pct") is not None},
        )
    except Exception as exc:
        features["DERIVATIVES_STATE"] = na_record("N_A_SOURCE_MISSING", f"Derivatives runtime unavailable: {type(exc).__name__}: {str(exc)[:120]}")

    for fid in TREND_IDS:
        if fid not in features:
            features[fid] = na_record("N_A_VALIDATION_FAIL", "feature engine failed to emit required feature")

    current_count = sum(1 for x in features.values() if x.get("availability") == "CURRENT")
    return {
        "schema_version": "1.2",
        "engine_id": "BTC_TREND_V26_FEATURE_ENGINE_R21",
        "status": "OK_SHADOW" if current_count else "FEATURES_UNAVAILABLE",
        "asof_utc": iso(now),
        "features": features,
        "current_feature_count": current_count,
        "production_eligible": False,
        "official_state_write_allowed": False,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="MASTER BTC TREND V2.6 R2.1 shadow feature engine")
    p.add_argument("--output", type=Path, default=HERE / "output" / "latest_features.json")
    args = p.parse_args()
    try:
        payload = build_live_features()
    except Exception as exc:
        payload = {
            "schema_version": "1.2",
            "engine_id": "BTC_TREND_V26_FEATURE_ENGINE_R21",
            "status": "VALIDATION_FAIL",
            "error": f"{type(exc).__name__}: {exc}",
            "production_eligible": False,
            "official_state_write_allowed": False,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") != "VALIDATION_FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
