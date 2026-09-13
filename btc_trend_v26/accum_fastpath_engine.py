from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from feature_engine_r22 import build_live_features_r22

HERE = Path(__file__).resolve().parent
CONTRACT_PATH = HERE / "accum_fastpath_contract.json"


class AccumFastpathError(RuntimeError):
    pass


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_contract() -> dict[str, Any]:
    obj = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise AccumFastpathError("contract root must be object")
    return obj


def map_directional_to_evidence(state: str, mapping: dict[str, str]) -> str:
    if state not in mapping:
        raise AccumFastpathError(f"unmapped directional state: {state}")
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


def _component_from_directional(
    rec: Any,
    mapping: dict[str, str],
    source_id: str,
) -> dict[str, Any]:
    if isinstance(rec, dict) and rec.get("availability") == "CURRENT":
        ev = map_directional_to_evidence(str(rec.get("state")), mapping)
        return {
            "availability": "CURRENT",
            "state": ev,
            "source_state": rec.get("state"),
            "feature_timestamp": rec.get("feature_timestamp"),
            "lineage": rec.get("lineage"),
        }
    return {"availability": "N_A_SOURCE_MISSING", "reason": f"{source_id}_UNAVAILABLE"}


def compute_accumulation(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    c = load_contract()
    trend = build_live_features_r22(now)
    tfeatures = trend.get("features") if isinstance(trend, dict) else None
    if not isinstance(tfeatures, dict):
        raise AccumFastpathError("trend feature payload unavailable")

    components: dict[str, dict[str, Any]] = {
        "ACCUM_BASE_1D": _component_from_directional(
            tfeatures.get("STRUCTURE_1D"), c["base_1d_mapping"], "STRUCTURE_1D"
        ),
        "ACCUM_IMPROVEMENT_4H": _component_from_directional(
            tfeatures.get("STRUCTURE_4H"), c["improvement_4h_mapping"], "STRUCTURE_4H"
        ),
        "ACCUM_LOCATION_1W": _component_from_directional(
            tfeatures.get("STRUCTURE_1W"), c["location_1w_mapping"], "STRUCTURE_1W"
        ),
        "ACCUM_SPOT_ETF_FLOW": _component_from_directional(
            tfeatures.get("SPOT_ETF_FLOW"), c["spot_etf_mapping"], "SPOT_ETF_FLOW"
        ),
    }

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
                raise AccumFastpathError(f"invalid evidence state {fid}={st}")
            valid_weight += w
            contribution = w * values[st]
            weighted += contribution
            used.append({
                "feature_id": fid,
                "weight": int(w),
                "state": st,
                "source_state": rec.get("source_state"),
                "contribution": float(contribution),
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
            "engine_id": "BTC_TREND_V26_ACCUM_FASTPATH_V1",
            "status": "ACCUMULATION_UNAVAILABLE",
            "asof_utc": iso(now),
            "coverage_pct": float(coverage),
            "accumulation_score": None,
            "band": None,
            "components": components,
            "used": used,
            "missing": missing,
            "research_only": True,
            "production_eligible": False,
            "official_state_write_allowed": False,
            "entry_gate_effect": 0,
            "long_short_effect": 0,
            "trend_strength_effect": 0,
            "live_plan_effect": 0,
        }

    raw_score = weighted / valid_weight * Decimal("100")
    score = round_half_up(raw_score)

    one_d = tfeatures.get("STRUCTURE_1D") if isinstance(tfeatures.get("STRUCTURE_1D"), dict) else {}
    four_h = tfeatures.get("STRUCTURE_4H") if isinstance(tfeatures.get("STRUCTURE_4H"), dict) else {}
    rapid_rise_proxy = (
        one_d.get("availability") == "CURRENT" and one_d.get("state") == "STRONG_LONG"
    ) or (
        four_h.get("availability") == "CURRENT" and four_h.get("state") == "STRONG_LONG"
    )
    cap_applied = False
    cap_value = int(c["rapid_rise_cap"]["max_score"])
    if rapid_rise_proxy and score > cap_value:
        score = cap_value
        cap_applied = True

    return {
        "schema_version": "1.0",
        "engine_id": "BTC_TREND_V26_ACCUM_FASTPATH_V1",
        "status": "OK_SHADOW",
        "asof_utc": iso(now),
        "coverage_pct": float(coverage),
        "coverage_grade": "C" if coverage < 70 else ("B" if coverage < 85 else "A"),
        "accumulation_score": score,
        "band": band(score),
        "rapid_rise_proxy": bool(rapid_rise_proxy),
        "cap_64_applied": cap_applied,
        "strong_confirmation_coverage_met": coverage >= Decimal("70"),
        "components": components,
        "used": used,
        "missing": missing,
        "research_only": True,
        "production_eligible": False,
        "official_state_write_allowed": False,
        "entry_gate_effect": 0,
        "long_short_effect": 0,
        "trend_strength_effect": 0,
        "live_plan_effect": 0,
        "bottom_effect": 0,
        "top_effect": 0,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="MASTER BTC TREND V2.6 ACCUMULATION fast-path shadow engine")
    p.add_argument("--output", type=Path, default=HERE / "output" / "latest_accum_fastpath.json")
    args = p.parse_args()
    try:
        out = compute_accumulation()
        rc = 0
    except Exception as exc:
        out = {
            "schema_version": "1.0",
            "engine_id": "BTC_TREND_V26_ACCUM_FASTPATH_V1",
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
