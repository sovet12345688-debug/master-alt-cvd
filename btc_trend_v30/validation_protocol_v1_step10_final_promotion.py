from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path("btc_trend_v30/output/validation_v1")
OUT = ROOT / "step10_final_promotion"
OUT.mkdir(parents=True, exist_ok=True)


def load(rel: str):
    with open(ROOT / rel / "audit.json", encoding="utf-8") as f:
        return json.load(f)

s1 = load("data_integrity")
s2 = load("step2_full_daily_scoring")
s3 = load("step3_episode_dedupe")
s4 = load("step4_outcome_labels")
s5 = load("step5_walk_forward_oos")
s6 = load("step6_state_machine_replay")
s7 = load("step7_metrics")
s8 = load("step8_robustness")
s9 = load("step9_exact_v26_h2h")

checks = {
    "step1_data_integrity": s1.get("data_integrity") == "PASS",
    "step2_full_rolling_and_pit": s2.get("step2") == "PASS" and bool(s2.get("point_in_time_prefix_invariance", {}).get("pass")),
    "step3_episode_dedupe": s3.get("step3") == "PASS",
    "step4_truth_labels": s4.get("step4") == "PASS",
    "step5_walk_forward_oos": s5.get("step5") == "PASS",
    "step6_state_machine_replay": s6.get("step6") == "PASS",
    "step7_metric_computation": s7.get("step7") == "PASS",
    "step8_robustness_battery": s8.get("step8") == "PASS",
    "step9_exact_v26_audit": s9.get("step9_audit") == "PASS",
}

long_exp = s7["direction"]["long"]["mean_unit_R"]
short_exp = s7["direction"]["short"]["mean_unit_R"]
capture_stop = s7["raw_all_leg"]["capture_to_stop_loss_ratio"]
mcr90 = s7["mcr"]["medium_90d_truth_positive"]["mean"]
mcr365 = s7["mcr"]["long_365d_truth_positive"]["mean"]
score_mono = s7["score_monotonicity"]["all"]["verdict"]
robustness = s8.get("robustness_performance_gate")
cycle_gate = s8.get("cycle", {}).get("cycle_gate")
exact_h2h = s9.get("exact_h2h_status")
portfolio_mdd = s7.get("preliminary_performance_gates", {}).get("portfolio_mdd")
full_live_hard_gate = bool(s6.get("frozen_replay_rules", {}).get("full_live_hard_gate_replay"))
external_risk = s6.get("frozen_replay_rules", {}).get("external_severe_event_risk")

promotion_gates = {
    "DATA_INTEGRITY": {"status": "PASS" if checks["step1_data_integrity"] else "FAIL", "value": s1.get("data_integrity")},
    "POINT_IN_TIME": {"status": "PASS" if checks["step2_full_rolling_and_pit"] else "FAIL", "value": s2.get("point_in_time_prefix_invariance", {}).get("pass")},
    "WALK_FORWARD_OOS": {"status": "PASS" if checks["step5_walk_forward_oos"] else "FAIL", "value": s5.get("oos_episodes")},
    "STATE_MACHINE_REPLAY": {"status": "PASS" if checks["step6_state_machine_replay"] else "FAIL", "value": s6.get("episode_counts")},
    "LONG_EXPECTANCY_POSITIVE": {"status": "PASS" if long_exp > 0 else "FAIL", "value": long_exp},
    "SHORT_EXPECTANCY_POSITIVE": {"status": "PASS" if short_exp > 0 else "FAIL", "value": short_exp},
    "CAPTURE_TO_STOP_GT_1": {"status": "PASS" if capture_stop > 1 else "FAIL", "value": capture_stop},
    "ROBUSTNESS": {"status": "PASS" if robustness == "PASS" else "FAIL", "value": robustness},
    "CYCLE_INDEPENDENCE": {"status": "PASS" if cycle_gate == "PASS" else "FAIL", "value": cycle_gate},
    "MCR_90D_GE_20PCT": {"status": "PASS" if mcr90 >= 0.20 else "FAIL", "value": mcr90},
    "MCR_365D_GE_20PCT": {"status": "PASS" if mcr365 >= 0.20 else "FAIL", "value": mcr365},
    "SCORE_MONOTONICITY": {"status": "PASS" if score_mono == "PASS" else ("FAIL" if score_mono == "FAIL" else "UNRESOLVED"), "value": score_mono},
    "FALSE_START_CONTROL": {"status": "UNRESOLVED", "value": {"confirmed_false_start_90d": s7["false_start"]["confirmed_false_starts_90d_no_medium_target"], "reason": "NO_PREDECLARED_ACCEPTANCE_THRESHOLD"}},
    "MISSED_TREND_IMPROVEMENT_VS_V2_6": {"status": "UNRESOLVED", "value": {"medium_missed_rate": s7["missed_trend"]["medium_missed_rate"], "long_missed_rate": s7["missed_trend"]["long_missed_rate"], "reason": exact_h2h}},
    "RISK_GOVERNOR_FULL_REPLAY": {"status": "PASS" if full_live_hard_gate and external_risk not in (None, "N/A_NOT_RECONSTRUCTED") and not str(portfolio_mdd).startswith("N/A") else "UNRESOLVED", "value": {"full_live_hard_gate_replay": full_live_hard_gate, "external_severe_event_risk": external_risk, "portfolio_mdd": portfolio_mdd}},
    "EXACT_V2_6_H2H": {"status": "PASS" if exact_h2h == "READY_EXACT_REPLAY" and s9.get("performance_head_to_head_allowed") else "UNRESOLVED", "value": exact_h2h},
}

hard_failures = [k for k, v in promotion_gates.items() if v["status"] == "FAIL"]
unresolved_blockers = [k for k, v in promotion_gates.items() if v["status"] == "UNRESOLVED"]
all_protocol_steps_pass = all(checks.values())

final_decision = "PASS" if all_protocol_steps_pass and not hard_failures and not unresolved_blockers else "HOLD"

reason_codes = []
if mcr90 < 0.20:
    reason_codes.append("MCR_90D_BELOW_20PCT")
if mcr365 < 0.20:
    reason_codes.append("MCR_365D_BELOW_20PCT")
if score_mono != "PASS":
    reason_codes.append("SCORE_MONOTONICITY_NOT_ESTABLISHED")
reason_codes.extend([
    "FALSE_START_ACCEPTANCE_THRESHOLD_NOT_PREDECLARED",
    "MISSED_TREND_IMPROVEMENT_VS_V2_6_UNRESOLVED",
    "FULL_RISK_GOVERNOR_AND_PORTFOLIO_RISK_NOT_REPLAYED",
])
if exact_h2h != "READY_EXACT_REPLAY":
    reason_codes.append("EXACT_V2_6_PERFORMANCE_H2H_LINK_NA")

report = {
    "status": "RESEARCH_VALIDATION_COMPLETE",
    "protocol": "VALIDATION_PROTOCOL_V1_0_FINAL_LOCK",
    "step": "10_FINAL_PROMOTION_DECISION",
    "generated_utc": datetime.now(timezone.utc).isoformat(),
    "r1_2_protocol_steps": checks,
    "step10_audit": "PASS" if all_protocol_steps_pass else "FAIL",
    "promotion_decision": final_decision,
    "promotion_rule": "PASS only if every predeclared required gate is PASS; FAIL or UNRESOLVED => HOLD. No retuning or proxy substitution allowed.",
    "promotion_gates": promotion_gates,
    "hard_failures": hard_failures,
    "unresolved_blockers": unresolved_blockers,
    "reason_codes": reason_codes,
    "key_reference_metrics": {
        "oos_episodes": s5.get("oos_episodes"),
        "first_1h_entries": s6.get("episode_counts", {}).get("oos_1h_entries"),
        "raw_all_leg_mean_R": s7["raw_all_leg"]["mean_unit_R"],
        "long_mean_R": long_exp,
        "short_mean_R": short_exp,
        "capture_to_stop_loss_ratio": capture_stop,
        "mcr90_mean": mcr90,
        "mcr365_mean": mcr365,
        "confirmed_false_start_90d": s7["false_start"]["confirmed_false_starts_90d_no_medium_target"],
        "medium_missed_rate": s7["missed_trend"]["medium_missed_rate"],
        "long_missed_rate": s7["missed_trend"]["long_missed_rate"],
        "robustness": robustness,
        "cycle_gate": cycle_gate,
        "exact_v2_6_h2h": exact_h2h,
    },
    "model_action": "DO_NOT_PROMOTE_R1_2_TO_PRODUCTION" if final_decision == "HOLD" else "ELIGIBLE_FOR_PRODUCTION_PROMOTION",
    "research_action": "FREEZE_R1_2_RESULTS_NO_RETUNE_FROM_OOS; DESIGN_NEXT_VERSION_AS_NEW_BASELINE" if final_decision == "HOLD" else "FREEZE_PROMOTED_BASELINE",
    "probability": "확률 산출보류",
    "v2_6_modified": False,
    "next_step": "R1_3_INDEPENDENT_REDESIGN_AND_NEW_UNTOUCHED_OOS" if final_decision == "HOLD" else "PRODUCTION_MIGRATION_WITH_RISK_GOVERNOR",
}

with open(OUT / "audit.json", "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

summary = [
    "# MASTER BTC TREND V3.0 R1.2 — Step10 Final Promotion",
    "",
    f"- Step10 audit: **{report['step10_audit']}**",
    f"- Promotion decision: **{final_decision}**",
    f"- Hard failures: {', '.join(hard_failures) if hard_failures else 'none'}",
    f"- Unresolved blockers: {', '.join(unresolved_blockers) if unresolved_blockers else 'none'}",
    f"- Action: **{report['model_action']}**",
]
with open(OUT / "SUMMARY.md", "w", encoding="utf-8") as f:
    f.write("\n".join(summary) + "\n")

print(json.dumps(report, ensure_ascii=False, indent=2))
