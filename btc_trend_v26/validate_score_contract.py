from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

EXPECTED_PRODUCTION_WEIGHT_SPEC = "1D22|4H10|1W8|volume+wave15|EMA/MA10|spot+BTC ETF10|derivatives8|overextension/value7|liquidity+macro10"
EXPECTED_STATES = {
    "STRONG_SHORT": -1.0,
    "SHORT": -0.5,
    "NEUTRAL": 0.0,
    "LONG": 0.5,
    "STRONG_LONG": 1.0,
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    errors: list[str] = []
    score = load(HERE / "score_contract.json")
    feature = load(HERE / "feature_contract.json")
    machine = load(ROOT / "state" / "master_btc_trend_v2_6_contract.json")

    if score.get("production_version") != "V2.6":
        errors.append("score contract production_version must be V2.6")
    if score.get("promotion_policy", {}).get("production_use_allowed") is not False:
        errors.append("R2 must remain research-only")
    if score.get("directional_state_values") != EXPECTED_STATES:
        errors.append("directional state mapping drift")

    weights = score.get("trend_weights", {})
    if sum(weights.values()) != 100:
        errors.append("trend weights must sum to 100")

    trend_group = feature.get("feature_groups", {}).get("TREND_DIRECTION", {})
    feature_ids = trend_group.get("feature_ids", [])
    if list(weights.keys()) != feature_ids:
        errors.append("score-contract trend feature order/IDs must exactly match feature contract")

    features = feature.get("features", {})
    for fid in feature_ids:
        spec = features.get(fid)
        if not isinstance(spec, dict):
            errors.append(f"feature missing from feature contract: {fid}")
            continue
        if spec.get("state_type") != "directional_5":
            errors.append(f"{fid}: TREND feature must use directional_5")

    production_spec = machine.get("core_scores", {}).get("TREND_LONG_SHORT")
    if production_spec != EXPECTED_PRODUCTION_WEIGHT_SPEC:
        errors.append("production TREND_LONG_SHORT weight spec drift")

    direction_ratio = machine.get("direction_ratio", {})
    if direction_ratio.get("sum") != 100:
        errors.append("production direction ratio must sum to 100")
    if direction_ratio.get("dominant_gap_points_min") != score.get("dominance", {}).get("dominant_gap_points_min"):
        errors.append("dominance gap mismatch with production contract")
    if direction_ratio.get("calibrated_probability") is not False:
        errors.append("production ratio must remain non-probabilistic")

    coverage = machine.get("coverage", {})
    if coverage.get("missing_weighted_fields") != "EXCLUDE_AND_RENORMALIZE_VALID_WEIGHT":
        errors.append("production missing-weight policy drift")
    if coverage.get("strong_confirmation_min") != score.get("coverage", {}).get("strong_confirmation_min"):
        errors.append("coverage strong-confirmation threshold mismatch")

    if score.get("availability_policy", {}).get("missing_is_zero") is not False:
        errors.append("missing_is_zero must be false")
    if score.get("anti_fabrication", {}).get("reuse_previous_score_when_current_unavailable") is not False:
        errors.append("previous-score substitution must be forbidden")

    if errors:
        print("R2 SCORE CONTRACT VALIDATION: FAIL")
        for err in errors:
            print(f"- {err}")
        return 1

    print("R2 SCORE CONTRACT VALIDATION: PASS")
    print(f"- trend features: {len(feature_ids)}")
    print(f"- total weight: {sum(weights.values())}")
    print("- production use: FORBIDDEN (research/shadow only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
