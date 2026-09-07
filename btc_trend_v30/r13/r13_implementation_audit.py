from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CFG_PATH = ROOT / "r13_frozen_config.json"
MANIFEST_PATH = ROOT / "r13_freeze_manifest.json"
ENGINE_PATH = ROOT / "r13_engine.py"
TEST_PATH = ROOT / "test_r13_contract.py"

EXPECTED_SHA = "b417282c66bec49c1c5de672f8317635508eba1dd05f538074a1508f14217fa9"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def flatten(obj: Any, prefix: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            out.extend(flatten(v, p))
    elif isinstance(obj, list):
        if not obj:
            out.append(prefix)
        else:
            for i, v in enumerate(obj):
                out.extend(flatten(v, f"{prefix}[{i}]"))
    else:
        out.append(prefix)
    return out


def classify(path: str) -> str | None:
    runtime_prefixes = (
        "engine_stage_policy",
        "entry_routes",
        "four_hour_add",
        "hard_execution_gates",
        "risk_state",
        "state_machine",
    )
    validation_prefixes = ("predeclared_validation_gates",)
    governance_prefixes = (
        "design_intent",
        "oos_policy",
        "unchanged_from_r1_2",
        "freeze_timestamp_kst",
        "model",
        "parent_r1_2_commit",
        "status",
    )
    if path.startswith(runtime_prefixes):
        return "RUNTIME_EXECUTION"
    if path.startswith(validation_prefixes):
        return "VALIDATION_GOVERNANCE"
    if path.startswith(governance_prefixes):
        return "FREEZE_GOVERNANCE"
    return None


def main() -> None:
    cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    engine_src = ENGINE_PATH.read_text(encoding="utf-8")
    test_src = TEST_PATH.read_text(encoding="utf-8")
    ast.parse(engine_src)
    ast.parse(test_src)

    leaves = flatten(cfg)
    classifications = {p: classify(p) for p in leaves}
    unmapped = sorted(p for p, c in classifications.items() if c is None)
    by_class: dict[str, list[str]] = {}
    for p, c in classifications.items():
        if c is not None:
            by_class.setdefault(c, []).append(p)

    weights = cfg["entry_routes"]["IGNITION"]["ignition_quality_score"]
    runtime_contract_checks = {
        "stage_policy_lr": cfg["engine_stage_policy"]["LR"] == "Stage>=1 execution-eligible",
        "stage_policy_sr": cfg["engine_stage_policy"]["SR"] == "Stage>=1 execution-eligible",
        "stage_policy_lc": cfg["engine_stage_policy"]["LC"] == "Stage1 WATCH_ONLY; Stage>=2 execution-eligible",
        "stage_policy_sc": cfg["engine_stage_policy"]["SC"] == "Stage1 WATCH_ONLY; Stage>=2 execution-eligible",
        "retest_risk_matrix": cfg["entry_routes"]["RETEST"]["allowed_risk_states"] == ["OPEN", "CAUTION"],
        "ignition_risk_matrix": cfg["entry_routes"]["IGNITION"]["allowed_risk_states"] == ["OPEN"],
        "retest_zone_touch": cfg["entry_routes"]["RETEST"]["zone_touch_required"] is True,
        "ignition_zone_touch_not_required": cfg["entry_routes"]["IGNITION"]["zone_touch_required"] is False,
        "retest_reaction_65": cfg["entry_routes"]["RETEST"]["reaction_score_min"] == 65,
        "ignition_quality_70": cfg["entry_routes"]["IGNITION"]["ignition_quality_min"] == 70,
        "retest_reasons_2": cfg["entry_routes"]["RETEST"]["independent_reasons_min"] == 2,
        "ignition_reasons_3": cfg["entry_routes"]["IGNITION"]["independent_reasons_min"] == 3,
        "retest_nonchase_1_5": cfg["entry_routes"]["RETEST"]["nonchase_daily_atr_max"] == 1.5,
        "ignition_nonchase_0_75": cfg["entry_routes"]["IGNITION"]["nonchase_daily_atr_max"] == 0.75,
        "ignition_persistence_0_25_h1_atr": cfg["entry_routes"]["IGNITION"]["breakout_persistence_buffer_h1_atr"] == 0.25,
        "ignition_weights_sum_100": sum(weights.values()) == 100,
        "fixed_rr_retest_3": cfg["entry_routes"]["RETEST"]["fixed_rr"] == 3.0,
        "fixed_rr_ignition_3": cfg["entry_routes"]["IGNITION"]["fixed_rr"] == 3.0,
        "max_stop_retest_15pct": cfg["entry_routes"]["RETEST"]["max_stop_distance_pct"] == 0.15,
        "max_stop_ignition_15pct": cfg["entry_routes"]["IGNITION"]["max_stop_distance_pct"] == 0.15,
        "four_hour_not_auto": cfg["four_hour_add"]["automatic_add"] is False,
        "four_hour_independent": cfg["four_hour_add"]["independent_confirmation_required"] is True,
        "one_day_no_third_leg": cfg["four_hour_add"]["third_allocation_leg_on_1d"] is False,
        "closed_candle_only": cfg["hard_execution_gates"]["closed_candle_only"] is True,
        "nonchase_required": cfg["hard_execution_gates"]["nonchase_required"] is True,
        "persistence_required": cfg["hard_execution_gates"]["persistence_required"] is True,
        "rr_min_3": cfg["hard_execution_gates"]["rr_min"] == 3.0,
        "same_bar_ambiguous": cfg["hard_execution_gates"]["same_bar_tp_sl"] == "AMBIGUOUS",
        "structural_stop_required": cfg["hard_execution_gates"]["structural_stop_required"] is True,
        "legacy_flags_not_binary_blockers": cfg["risk_state"]["legacy_flags_are_not_binary_blockers"] is True,
    }

    validation_checks = {
        "mcr90_locked_20pct": cfg["predeclared_validation_gates"]["mcr_90d_mean_min"] == 0.2,
        "mcr365_locked_20pct": cfg["predeclared_validation_gates"]["mcr_365d_mean_min"] == 0.2,
        "long_expectancy_positive": cfg["predeclared_validation_gates"]["long_expectancy_gt"] == 0.0,
        "short_expectancy_positive": cfg["predeclared_validation_gates"]["short_expectancy_gt"] == 0.0,
        "capture_stop_gt_1": cfg["predeclared_validation_gates"]["capture_to_stop_loss_ratio_gt"] == 1.0,
        "false_start_max_50pct": cfg["predeclared_validation_gates"]["false_start_control"]["confirmed_false_start_rate_max"] == 0.5,
        "score_bin_min_n_10": cfg["predeclared_validation_gates"]["score_monotonicity"]["minimum_bin_n"] == 10,
        "risk_governor_required": cfg["predeclared_validation_gates"]["risk_governor_full_replay"] == "Required for production promotion",
    }

    governance_checks = {
        "config_sha_exact": sha256(CFG_PATH) == EXPECTED_SHA == manifest["canonical_config_sha256"],
        "freeze_locked": manifest["freeze_status"] == "FINAL_FREEZE_PRE_OOS",
        "oos_firewall_locked": manifest["oos_firewall"] == "LOCKED",
        "oos_runs_before_freeze_zero": manifest["oos_runs_before_freeze"] == 0,
        "new_forward_oos_start_exact": cfg["oos_policy"]["new_untouched_forward_oos_start_utc"] == "2026-09-05T00:00:00Z",
        "r12_diagnostic_only": cfg["oos_policy"]["r12_oos_results_are_diagnostic_only"] is True,
        "v2_6_untouched": manifest["v2_6_modified"] is False,
        "model_exact": cfg["model"] == "MASTER_BTC_TREND_V3_R1_3",
        "status_exact": cfg["status"] == "RESEARCH_BASELINE_FROZEN_PRE_OOS",
        "no_unmapped_config_leaf": len(unmapped) == 0,
        "engine_has_no_network_import": "import requests" not in engine_src and "urllib" not in engine_src,
        "engine_has_no_historical_loader": "data.binance" not in engine_src and "read_csv" not in engine_src,
    }

    all_checks = {**runtime_contract_checks, **validation_checks, **governance_checks}
    ok = all(all_checks.values())
    report = {
        "model": cfg["model"],
        "implementation_audit": "PASS" if ok else "FAIL",
        "canonical_config_sha256": sha256(CFG_PATH),
        "config_leaf_count": len(leaves),
        "config_mapping": {k: len(v) for k, v in sorted(by_class.items())},
        "unmapped_config_leaves": unmapped,
        "runtime_contract_checks": runtime_contract_checks,
        "validation_gate_checks": validation_checks,
        "governance_checks": governance_checks,
        "implementation_inheritance": {
            "RETEST reaction/persistence/safety": "R1.2 Step6 semantics inherited; no new R1.3 OOS-selected threshold",
            "IGNITION directional candle": "R1.2 reaction directional candle semantics: long close>open & close_loc>=0.60; short mirrored <=0.40",
            "IGNITION taker directional": "R1.2 reaction sign semantics: long >=0; short <=0",
            "IGNITION daily transition": "R1.2 up_transition/down_transition",
            "route_tie_break": "If causal zone is touched, evaluate RETEST only; a failed retest is not relabeled as IGNITION on the same event.",
        },
        "oos_replay_performed": False,
        "historical_performance_read": False,
        "v2_6_modified": False,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not ok:
        raise SystemExit("R1.3 implementation audit failed")


if __name__ == "__main__":
    main()
