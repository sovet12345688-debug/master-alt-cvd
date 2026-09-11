from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import requests

from feature_engine_r22 import build_live_features_r22

HERE = Path(__file__).resolve().parent
CONTRACT_PATH = HERE / "bottom_fastpath_contract.json"
BITGET_CANDLES = "https://api.bitget.com/api/v3/market/candles"
HOUR_MS = 60 * 60 * 1000


class BottomFastpathError(RuntimeError):
    pass


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_contract() -> dict[str, Any]:
    obj = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise BottomFastpathError("contract root must be object")
    return obj


def rsi14(closes: list[float]) -> list[float | None]:
    n = 14
    if len(closes) < n + 1:
        raise BottomFastpathError("insufficient closes for RSI14")
    gains: list[float] = []
    losses: list[float] = []
    out: list[float | None] = [None] * len(closes)
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
        if i == n:
            avg_gain = sum(gains[-n:]) / n
            avg_loss = sum(losses[-n:]) / n
        elif i > n:
            avg_gain = (avg_gain * (n - 1) + gains[-1]) / n
            avg_loss = (avg_loss * (n - 1) + losses[-1]) / n
        else:
            continue
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def volume_z(values: list[float], window: int = 20) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for i in range(window - 1, len(values)):
        w = values[i - window + 1:i + 1]
        mean = sum(w) / window
        var = sum((x - mean) ** 2 for x in w) / window
        sd = math.sqrt(var)
        out[i] = 0.0 if sd == 0 else (values[i] - mean) / sd
    return out


def fetch_completed_1h(now: datetime, minimum: int = 80) -> list[dict[str, float | int]]:
    params = {
        "category": "USDT-FUTURES",
        "symbol": "BTCUSDT",
        "interval": "1H",
        "type": "market",
        "limit": "200",
    }
    r = requests.get(BITGET_CANDLES, params=params, timeout=20, headers={"User-Agent": "btc-trend-v26-bottom-fastpath/1.0"})
    r.raise_for_status()
    payload = r.json()
    if not isinstance(payload, dict) or str(payload.get("code")) != "00000":
        raise BottomFastpathError(f"Bitget 1H candles error: {payload!r}")
    now_ms = int(now.timestamp() * 1000)
    rows: list[dict[str, float | int]] = []
    for row in payload.get("data") or []:
        if not isinstance(row, list) or len(row) < 6:
            continue
        try:
            t = int(row[0]); o = float(row[1]); h = float(row[2]); l = float(row[3]); c = float(row[4]); v = float(row[5])
        except (TypeError, ValueError):
            continue
        if t + HOUR_MS > now_ms:
            continue
        if not all(math.isfinite(x) for x in (o, h, l, c, v)):
            continue
        rows.append({"open_time_ms": t, "close_time_ms": t + HOUR_MS, "open": o, "high": h, "low": l, "close": c, "volume": v})
    rows.sort(key=lambda x: int(x["open_time_ms"]))
    if len(rows) < minimum:
        raise BottomFastpathError(f"only {len(rows)} completed 1H bars; need {minimum}")
    return rows


def capitulation_state(bars: list[dict[str, float | int]]) -> tuple[str, dict[str, Any]]:
    closes = [float(x["close"]) for x in bars]
    vols = [float(x["volume"]) for x in bars]
    rsi = rsi14(closes)
    vz = volume_z(vols, 20)
    start = max(0, len(bars) - 24)
    idxs = list(range(start, len(bars)))
    rsi_vals = [float(rsi[i]) for i in idxs if rsi[i] is not None]
    vz_vals = [float(vz[i]) for i in idxs if vz[i] is not None]
    ret12_vals = []
    wick_vals = []
    for i in idxs:
        if i >= 12 and closes[i - 12] != 0:
            ret12_vals.append(closes[i] / closes[i - 12] - 1.0)
        o = float(bars[i]["open"]); h = float(bars[i]["high"]); l = float(bars[i]["low"]); c = float(bars[i]["close"])
        rg = h - l
        if rg > 0:
            wick_vals.append((min(o, c) - l) / rg)
    if not rsi_vals or not vz_vals or not ret12_vals or not wick_vals:
        raise BottomFastpathError("capitulation inputs incomplete")
    facts = {
        "min_rsi24": min(rsi_vals),
        "min_ret12_24": min(ret12_vals),
        "max_volz24": max(vz_vals),
        "max_lower_wick24": max(wick_vals),
    }
    flags = {
        "RSI_MIN24_LE_35": facts["min_rsi24"] <= 35.0,
        "RET12_MIN24_LE_MINUS_3PCT": facts["min_ret12_24"] <= -0.03,
        "VOLZ_MAX24_GE_0_8": facts["max_volz24"] >= 0.8,
        "LOWER_WICK_MAX24_GE_50PCT": facts["max_lower_wick24"] >= 0.50,
    }
    count = sum(1 for x in flags.values() if x)
    state = {0: "NONE", 1: "WEAK", 2: "MODERATE", 3: "STRONG", 4: "VERY_STRONG"}[count]
    return state, {"facts": facts, "flags": flags, "flag_count": count}


def map_directional_to_evidence(state: str, mapping: dict[str, str]) -> str:
    if state not in mapping:
        raise BottomFastpathError(f"unmapped directional state: {state}")
    return str(mapping[state])


def band(score: int) -> str:
    if score >= 85:
        return "매우 강함"
    if score >= 75:
        return "강함"
    if score >= 60:
        return "유의미"
    if score >= 40:
        return "가능성 관찰"
    return "낮음"


def round_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def compute_bottom(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    c = load_contract()
    trend = build_live_features_r22(now)
    tfeatures = trend.get("features") if isinstance(trend, dict) else None
    if not isinstance(tfeatures, dict):
        raise BottomFastpathError("trend feature payload unavailable")

    components: dict[str, dict[str, Any]] = {}
    struct_map = c["structure_mapping"]
    etf_map = c["spot_etf_mapping"]

    for out_id, in_id in (("BOTTOM_STRUCTURE_1D", "STRUCTURE_1D"), ("BOTTOM_STRUCTURE_4H", "STRUCTURE_4H")):
        rec = tfeatures.get(in_id)
        if isinstance(rec, dict) and rec.get("availability") == "CURRENT":
            ev = map_directional_to_evidence(str(rec.get("state")), struct_map)
            components[out_id] = {"availability": "CURRENT", "state": ev, "source_state": rec.get("state"), "feature_timestamp": rec.get("feature_timestamp"), "lineage": rec.get("lineage")}
        else:
            components[out_id] = {"availability": "N_A_SOURCE_MISSING", "reason": f"{in_id}_UNAVAILABLE"}

    etf = tfeatures.get("SPOT_ETF_FLOW")
    if isinstance(etf, dict) and etf.get("availability") == "CURRENT":
        ev = map_directional_to_evidence(str(etf.get("state")), etf_map)
        components["BOTTOM_SPOT_ETF"] = {"availability": "CURRENT", "state": ev, "source_state": etf.get("state"), "feature_timestamp": etf.get("feature_timestamp"), "lineage": etf.get("lineage")}
    else:
        components["BOTTOM_SPOT_ETF"] = {"availability": "N_A_SOURCE_MISSING", "reason": "SPOT_ETF_FLOW_UNAVAILABLE"}

    try:
        bars = fetch_completed_1h(now)
        ev, details = capitulation_state(bars)
        ts = iso(datetime.fromtimestamp(int(bars[-1]["close_time_ms"]) / 1000, tz=timezone.utc))
        components["CAPITULATION"] = {"availability": "CURRENT", "state": ev, "feature_timestamp": ts, "details": details, "lineage": {"source": "Bitget USDT Futures BTCUSDT 1H", "completed_candle_only": True, "research_thresholds": "run_34481021338_directionally_stable_seeds"}}
    except Exception as exc:
        components["CAPITULATION"] = {"availability": "N_A_SOURCE_MISSING", "reason": f"{type(exc).__name__}: {str(exc)[:160]}"}

    weights = {k: Decimal(str(v)) for k, v in c["weights"].items()}
    values = {k: Decimal(str(v)) for k, v in c["evidence_values"].items()}
    valid_weight = Decimal("0")
    weighted = Decimal("0")
    used = []
    missing = []
    for fid, w in weights.items():
        rec = components.get(fid, {})
        if rec.get("availability") == "CURRENT":
            st = str(rec.get("state"))
            if st not in values:
                raise BottomFastpathError(f"invalid evidence state {fid}={st}")
            valid_weight += w
            weighted += w * values[st]
            used.append({"feature_id": fid, "weight": int(w), "state": st, "contribution": float(w * values[st])})
        else:
            missing.append({"feature_id": fid, "weight": int(w), "reason": rec.get("reason", rec.get("availability"))})

    coverage = valid_weight  # canonical full score weight is 100
    if valid_weight <= 0 or coverage < Decimal(str(c["coverage_policy"]["display_shadow_min_pct"])):
        return {"schema_version": "1.0", "engine_id": "BTC_TREND_V26_BOTTOM_FASTPATH_V1", "status": "BOTTOM_UNAVAILABLE", "asof_utc": iso(now), "coverage_pct": float(coverage), "bottom_score": None, "band": None, "components": components, "used": used, "missing": missing, "production_eligible": False, "official_state_write_allowed": False, "entry_gate_effect": 0}

    raw_score = weighted / valid_weight * Decimal("100")
    score = round_half_up(raw_score)
    one_d = tfeatures.get("STRUCTURE_1D") if isinstance(tfeatures.get("STRUCTURE_1D"), dict) else {}
    confirmed_turn = one_d.get("availability") == "CURRENT" and one_d.get("state") in set(c["bottom_cap"]["confirmed_1d_turn_states"])
    cap_applied = False
    if not confirmed_turn and score > int(c["bottom_cap"]["max_without_confirmed_1d_turn"]):
        score = int(c["bottom_cap"]["max_without_confirmed_1d_turn"])
        cap_applied = True

    return {
        "schema_version": "1.0",
        "engine_id": "BTC_TREND_V26_BOTTOM_FASTPATH_V1",
        "status": "OK_SHADOW",
        "asof_utc": iso(now),
        "coverage_pct": float(coverage),
        "coverage_grade": "C" if coverage < 70 else ("B" if coverage < 85 else "A"),
        "bottom_score": score,
        "band": band(score),
        "confirmed_1d_turn": confirmed_turn,
        "cap_59_applied": cap_applied,
        "strong_confirmation_coverage_met": coverage >= Decimal("70"),
        "components": components,
        "used": used,
        "missing": missing,
        "research_only": True,
        "production_eligible": False,
        "official_state_write_allowed": False,
        "entry_gate_effect": 0,
        "long_short_effect": 0,
        "live_plan_effect": 0,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="MASTER BTC TREND V2.6 BOTTOM fast-path shadow engine")
    p.add_argument("--output", type=Path, default=HERE / "output" / "latest_bottom_fastpath.json")
    args = p.parse_args()
    try:
        out = compute_bottom()
        rc = 0
    except Exception as exc:
        out = {"schema_version": "1.0", "engine_id": "BTC_TREND_V26_BOTTOM_FASTPATH_V1", "status": "VALIDATION_FAIL", "error": f"{type(exc).__name__}: {exc}", "production_eligible": False, "official_state_write_allowed": False, "entry_gate_effect": 0}
        rc = 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
