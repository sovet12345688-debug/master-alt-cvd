from __future__ import annotations

import json
from pathlib import Path

CONTRACT = Path(__file__).with_name("feature_contract.json")

EXPECTED_TREND = [
    "STRUCTURE_1D",
    "STRUCTURE_4H",
    "STRUCTURE_1W",
    "VOLUME_WAVE",
    "EMA_MA_STACK",
    "SPOT_ETF_FLOW",
    "DERIVATIVES_STATE",
    "VALUE_OVEREXTENSION",
    "LIQUIDITY_MACRO",
]

ALLOWED_STATE_TYPES = {"directional_5", "evidence_5"}
ALLOWED_FALLBACKS = {
    "N_A_SOURCE_MISSING",
    "N_A_STALE",
    "N_A_VALIDATION_FAIL",
    "N_A_THRESHOLD_UNAPPROVED",
    "N_A_INSUFFICIENT_HISTORY",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    data = json.loads(CONTRACT.read_text(encoding="utf-8"))

    require(data["contract_id"] == "BTC_TREND_V26_FEATURE_CONTRACT_R1", "contract_id drift")
    require(data["production_version"] == "V2.6", "production_version drift")
    require(data["research_version"] == "V3.0", "research_version drift")
    require(data["status"] == "R1_FEATURE_SCHEMA_LOCKED_RESEARCH_ONLY", "R1 status drift")

    promotion = data["promotion_policy"]
    require(promotion["production_use_allowed"] is False, "R1 must not be production-usable")
    require(promotion["silent_promotion_forbidden"] is True, "silent promotion must be forbidden")
    require(promotion["requires_oos_validation"] is True, "OOS validation must be required")
    require(promotion["requires_shadow_mode"] is True, "shadow mode must be required")
    require(promotion["requires_explicit_acceptance"] is True, "explicit acceptance must be required")

    rules = data["global_rules"]
    require(rules["completed_candle_only_for_structure_and_confirmation"] is True, "completed-candle lock missing")
    require(rules["missing_is_zero"] is False, "missing must never equal zero")
    require(rules["cross_venue_repair_forbidden"] is True, "cross-venue repair must be forbidden")
    require(rules["chat_memory_backfill_forbidden"] is True, "chat-memory backfill must be forbidden")
    require(rules["last_good_substitution_forbidden_for_current_score"] is True, "last-good current-score substitution forbidden")
    require(rules["stale_source_forbidden_for_current_score"] is True, "stale source current-score use forbidden")
    require(rules["double_count_same_raw_fact_within_one_composite"] is False, "double-count guard drift")

    states = data["standard_states"]
    require(states["directional_5"] == ["STRONG_SHORT", "SHORT", "NEUTRAL", "LONG", "STRONG_LONG"], "directional state vocabulary drift")
    require(states["evidence_5"] == ["NONE", "WEAK", "MODERATE", "STRONG", "VERY_STRONG"], "evidence state vocabulary drift")

    groups = data["feature_groups"]
    require(groups["TREND_DIRECTION"]["feature_ids"] == EXPECTED_TREND, "TREND feature ownership/order drift")

    features = data["features"]
    owned = []
    for group_name, group in groups.items():
        for feature_id in group["feature_ids"]:
            require(feature_id in features, f"{group_name}: missing feature {feature_id}")
            owned.append(feature_id)
    require(len(owned) == len(set(owned)), "feature is owned by more than one score family")
    require(set(owned) == set(features), "unowned or unregistered feature exists")

    for feature_id, feature in features.items():
        require(feature["state_type"] in ALLOWED_STATE_TYPES, f"{feature_id}: invalid state_type")
        require(isinstance(feature.get("inputs"), list) and feature["inputs"], f"{feature_id}: inputs required")
        require(feature.get("fallback") in ALLOWED_FALLBACKS, f"{feature_id}: invalid fallback")
        require("threshold_status" in feature, f"{feature_id}: threshold_status required")

    deriv = features["DERIVATIVES_STATE"]
    require(deriv["hard_rules"]["exact_window_required"] is True, "derivatives exact-window lock missing")
    require(deriv["hard_rules"]["cross_venue_repair_forbidden"] is True, "derivatives cross-venue repair forbidden")

    top_dist = features["BLOWOFF_DISTRIBUTION"]
    require(top_dist["research_policy"] == "DO_NOT_MIRROR_BOTTOM_THRESHOLDS", "TOP must not mirror BOTTOM thresholds")

    output = data["output_contract"]
    require(output["score_fields_forbidden_in_feature_output"] is True, "feature layer must not emit scores")
    require(output["path"] == "btc_trend_v26/output/latest_features.json", "feature output path drift")

    guards = data["validation_guards"]
    for key in [
        "duplicate_raw_fact_same_composite",
        "future_candle_use",
        "incomplete_candle_as_confirmation",
        "stale_source_as_current",
        "cross_venue_fill",
        "research_threshold_silent_promotion",
        "missing_as_zero",
        "other_master_decision_input",
    ]:
        require(guards.get(key) == "FAIL", f"guard {key} must FAIL closed")

    r1 = data["r1_lock"]
    require("numeric thresholds" in r1["not_locked_yet"], "numeric thresholds must remain explicitly unlocked")
    require("pivot/swing detection parameters" in r1["not_locked_yet"], "pivot parameters must remain explicitly unlocked")

    print(f"PASS: {data['contract_id']} | features={len(features)} | groups={len(groups)}")


if __name__ == "__main__":
    main()
