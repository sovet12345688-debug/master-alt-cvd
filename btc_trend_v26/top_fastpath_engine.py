from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from feature_engine_r22 import build_live_features_r22

HERE = Path(__file__).resolve().parent
CONTRACT_PATH = HERE / "top_fastpath_contract.json"


class TopFastpathError(RuntimeError):
    pass


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_contract() -> dict[str, Any]:
    obj = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise TopFastpathError("contract root must be object")
    return obj


def map_directional_to_evidence(state: str, mapping: dict[str, str]) -> str:
    if state not in mapping:
        raise TopFastpathError(f"unmapped directional state: {state}")
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


def compute_top(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    c = load_contract()
    trend = build_live_features_r22(now)
    tfeatures = trend.get("features") if isinstance(trend, dict) else None
    if not isinstance(tfeatures, dict):
        raise TopFastpathError("trend feature payload unavailable")

    components: dict[str, dict[str, Any]] = {}
    struct_map = c["structure_mapping"]
    etf_map = c["spot_etf_mapping"]

    pairs = (
        ("TOP_STRUCTURE_1D", "STRUCTURE_1D"),
        ("TOP_STRUCTURE_4H", "STRUCTURE_4H"),
        ("TOP_STRUCTURE_1W", "STRUCTURE_1W"),
    )
    for out_id, in_id in pairs:
        rec = tfeatures.get(in_id)
        if isinstance(rec, dict) and rec.get("availability") == "CURRENT":
            ev = map_directional_to_evidence(str(rec.get("state")), struct_map)
            components[out_id] = {
                "availability": "CURRENT",
                "state": ev,
                "source_state": rec.get("state"),
                "feature_timestamp": rec.get("feature_timestamp"),
                "lineage": rec.get("lineage"),
            }
        else:
            components[out_id] = {"availability": "N_A_SOURCE_MISSING", "reason": f"{in_id}_UNAVAILABLE"}

    etf = tfeatures.get("SPOT_ETF_FLOW")
    if isinstance(etf, dict) and etf.get("availability") == "CURRENT":
        ev = map_directional_to_evidence(str(etf.get("state")), etf_map)
        components["TOP_SPOT_ETF"] = {
            "availability": "CURRENT",
            "state": ev,
            "source_state": etf.get("state"),
            "feature_timestamp": etf.get("feature_timestamp"),
            "lineage": etf.get("lineage"),
        }
    else:
        components["TOP_SPOT_ETF"] = {"availability": "N_A_SOURCE_MISSING", "reason": "SPOT_ETF_FLOW_UNAVAILABLE"}

    weights = {k: Decimal(str(v)) for k, v in c["weights"].items()}
    values = {k: Decimal(str(v)) for k, v in c["evidence_values"].items()}
    valid_weight = Decimal("0")
    weighted = Decimal("0")
    used: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []

    for fid, w in weights.items():
        rec = components.get(fid, {})
        if rec.get("availability") == "CURRENT":
            st = str(rec.get("state"))
            if st not in values:
                raise TopFastpathError(f"invalid evidence state {fid}={st}")
            valid_weight += w
            weighted += w * values[st]
            used.append({
                "feature_id": fid,
                "weight": int(w),
                "state": st,
                "contribution": float(w * values[st]),
            })
        else:
            missing.append({
                "feature_id": fid,
                "weight": int(w),
                "reason": rec.get("reason", rec.get("availability")),
            })

    coverage = valid_weight
    min_cov = Decimal(str(c["coverage_policy"]["display_shadow_min_pct"]))
    if valid_weight <= 0 or coverage < min_cov:
        return {
            "schema_version": "1.0",
            "engine_id": "BTC_TREND_V26_TOP_FASTPATH_V1",
            "status": "TOP_UNAVAILABLE",
            "asof_utc": iso(now),
            "coverage_pct": float(coverage),
            "top_score": None,
            "band": None,
            "components": components,
            "used": used,
            "missing": missing,
            "research_only": True,
            "production_eligible": False,
            "official_state_write_allowed": False,
            "entry_gate_effect": 0,
        }

    raw_score = weighted / valid_weight * Decimal("100")
    score = round_half_up(raw_score)

    one_d = tfeatures.get("STRUCTURE_1D") if isinstance(tfeatures.get("STRUCTURE_1D"), dict) else {}
    intact_bull = (
        one_d.get("availability") == "CURRENT"
        and one_d.get("state") in set(c["top_cap"]["intact_1d_states"])
    )
    confirmed_turn_down = (
        one_d.get("availability") == "CURRENT"
        and one_d.get("state") in {"SHORT", "STRONG_SHORT"}
    )
    cap_applied = False
    if intact_bull and score > int(c["top_cap"]["max_while_intact"]):
        score = int(c["top_cap"]["max_while_intact"])
        cap_applied = True

    return {
        "schema_version": "1.0",
        "engine_id": "BTC_TREND_V26_TOP_FASTPATH_V1",
        "status": "OK_SHADOW",
        "asof_utc": iso(now),
        "coverage_pct": float(coverage),
        "coverage_grade": "C" if coverage < 70 else ("B" if coverage < 85 else "A"),
        "top_score": score,
        "band": band(score),
        "confirmed_1d_turn_down": confirmed_turn_down,
        "intact_1d_bull_structure": intact_bull,
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
        "bottom_effect": 0,
        "accumulation_effect": 0,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="MASTER BTC TREND V2.6 TOP fast-path shadow engine")
    p.add_argument("--output", type=Path, default=HERE / "output" / "latest_top_fastpath.json")
    args = p.parse_args()
    try:
        out = compute_top()
        rc = 0
    except Exception as exc:
        out = {
            "schema_version": "1.0",
            "engine_id": "BTC_TREND_V26_TOP_FASTPATH_V1",
            "status": "VALIDATION_FAIL",
            "error": f"{type(exc).__name__}: {exc}",
            "research_only": True,
            "production_eligible": False,
            "official_state_write_allowed": False,
            "entry_gate_effect": 0,
        }
        rc = 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
