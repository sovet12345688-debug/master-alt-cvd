from __future__ import annotations

import json
import numpy as np
import pandas as pd

import run_v26_v041 as v041

v040 = v041.v040
D = v040.D


def _acc(x: pd.Series) -> float | None:
    z = x.dropna()
    return None if z.empty else round(float(z.astype(bool).mean()) * 100.0, 2)


def same_era_validate(wf: pd.DataFrame, deriv: pd.DataFrame, cfg: dict) -> dict:
    """Revalidate Layer B only where historical derivatives data actually existed.

    Two denominators are reported deliberately:
    1) calendar-era rows: independent OOS rows inside the derivatives history window;
    2) feature-eligible rows: calendar-era rows with enough PIT-safe derivative features to ask the signal engine.

    Signal coverage is judged against the feature-eligible denominator. Rows that have enough source features but
    fail price-conditioned class-history or score-gap requirements remain in the denominator as legitimate abstains.
    No scoring thresholds or acceptance gates are lowered.
    """
    c = cfg.get("v040", {}).get("acceptance", {})
    minf = int(cfg.get("v040", {}).get("min_query_features", 6))
    ev = wf[wf["actual"].isin(D)].copy()
    if ev.empty or deriv.empty:
        return {"status": "NO_SAME_ERA_DATA", "same_era_status": "FAIL"}

    start = deriv.index.min().floor("D")
    end = deriv.index.max().floor("D")
    cal = ev[(ev.index.floor("D") >= start) & (ev.index.floor("D") <= end)].copy()
    qfc = pd.to_numeric(cal.get("query_feature_count"), errors="coerce")
    eligible = cal[qfc >= minf].copy()
    sig = eligible[eligible["deriv_pred"].isin(D)].copy()

    direct = v040.v032.stats(sig.rename(columns={"deriv_pred": "candidate_pred"}), "candidate_pred") if not sig.empty else {
        "rows": 0, "coverage_pct": 0.0, "accuracy": None, "balanced_accuracy": None,
        "class_recalls": {}, "prediction_mix": {},
    }
    signal_coverage = round(len(sig) / len(eligible) * 100.0, 2) if len(eligible) else 0.0
    direct["coverage_pct_of_feature_eligible"] = signal_coverage

    base_sig_acc = _acc(sig["base_correct"]) if not sig.empty else None
    confirm = sig[sig["confirmation_status"] == "CONFIRM"]
    conflict = sig[sig["confirmation_status"] == "CONFLICT"]
    confirm_acc = _acc(confirm["base_correct"])
    conflict_acc = _acc(conflict["base_correct"])
    confirm_gain = None if base_sig_acc is None or confirm_acc is None else round(confirm_acc - base_sig_acc, 2)
    conflict_gap = None if base_sig_acc is None or conflict_acc is None else round(conflict_acc - base_sig_acc, 2)

    recalls = direct.get("class_recalls", {})
    mix = direct.get("prediction_mix", {})
    minority = min(float(mix.get("up_pct") or 0), float(mix.get("down_pct") or 0))
    gates = {
        "same_era_sample_gate": len(eligible) >= int(c.get("min_modern_evaluable", 12)),
        "signal_case_gate": len(sig) >= int(c.get("min_signal_cases", 10)),
        "coverage_gate": signal_coverage >= float(c.get("min_signal_coverage_pct", 40)),
        "balanced_accuracy_gate": direct.get("balanced_accuracy") is not None and direct["balanced_accuracy"] >= float(c.get("min_balanced_accuracy", 55)),
        "both_recall_gate": all(recalls.get(x) is not None and recalls[x] >= float(c.get("min_each_recall_pct", 30)) for x in D),
        "nondegenerate_gate": minority >= float(c.get("min_minority_prediction_pct", 20)),
    }
    direct_pass = bool(all(gates.values()))
    confirm_utility = len(confirm) >= int(c.get("min_utility_cases", 6)) and confirm_gain is not None and confirm_gain >= float(c.get("min_confirm_gain_pp", 5))
    conflict_utility = len(conflict) >= int(c.get("min_utility_cases", 6)) and conflict_gap is not None and conflict_gap <= float(c.get("max_conflict_gap_pp", -5))
    utility_pass = bool(confirm_utility or conflict_utility)
    status = "VALIDATED_CONFIRMATION_LAYER" if direct_pass and utility_pass else ("DIRECT_SIGNAL_PASS_UTILITY_FAIL" if direct_pass else "SHADOW_ONLY_FAIL")

    status_counts = {str(k): int(v) for k, v in eligible["deriv_status"].value_counts(dropna=False).to_dict().items()}
    return {
        "derivatives_history_range": {"start": start.date().isoformat(), "end": end.date().isoformat()},
        "all_independent_evaluable_rows": int(len(ev)),
        "calendar_era_rows": int(len(cal)),
        "feature_eligible_rows": int(len(eligible)),
        "excluded_pre_derivatives_rows": int(len(ev) - len(cal)),
        "excluded_low_feature_rows_inside_era": int(len(cal) - len(eligible)),
        "feature_eligibility_rule": f"query_feature_count >= {minf}",
        "eligible_status_counts": status_counts,
        "signal_rows": int(len(sig)),
        "direct_derivatives_signal": direct,
        "same_era_gates": gates,
        "direct_pass": direct_pass,
        "base_accuracy_on_signal_rows": base_sig_acc,
        "confirmation": {"count": int(len(confirm)), "base_accuracy": confirm_acc, "gain_vs_signal_baseline_pp": confirm_gain},
        "conflict": {"count": int(len(conflict)), "base_accuracy": conflict_acc, "gap_vs_signal_baseline_pp": conflict_gap},
        "confirmation_utility_pass": bool(confirm_utility),
        "conflict_utility_pass": bool(conflict_utility),
        "utility_pass": utility_pass,
        "same_era_status": status,
        "locked_acceptance": c,
        "interpretation_guard": "Same-era denominator correction only. No score, threshold, signal rule or acceptance gate was changed.",
    }


def main_v042() -> None:
    v041.main_v041()
    cfg, out, data = v040.core.load_config(), v040.core.OUT, v040.core.DATA
    wf = pd.read_csv(out / "walk_forward_v040_layer_b.csv", parse_dates=["time"]).set_index("time")
    deriv = pd.read_csv(data / "layer_b_binance_derivatives_daily.csv", parse_dates=["day"]).set_index("day")
    validation = same_era_validate(wf, deriv, cfg)

    prev_cur = json.loads((out / "current_v041_layer_b.json").read_text(encoding="utf-8"))
    cur = dict(prev_cur)
    cur["engine"] = "BTC_HISTORICAL_REGIME_OUTCOME_V0_4_2"
    cur["schema_version"] = "0.4.2"
    cur["same_era_validation"] = {
        "status": validation.get("same_era_status"),
        "calendar_era_rows": validation.get("calendar_era_rows"),
        "feature_eligible_rows": validation.get("feature_eligible_rows"),
        "signal_rows": validation.get("signal_rows"),
        "signal_coverage_pct": validation.get("direct_derivatives_signal", {}).get("coverage_pct_of_feature_eligible"),
    }
    cur["master_integration"] = "FORBIDDEN_PENDING_FULL_SEQUENCE_AND_EXPLICIT_USER_APPROVAL"
    (out / "current_v042_layer_b.json").write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")

    final = {
        "engine": "BTC_HISTORICAL_REGIME_OUTCOME_V0_4_2",
        "schema_version": "0.4.2",
        "architecture": "V035_PRICE_REGIME_CORE_PLUS_DERIVATIVES_LAYER_B_SAME_ERA_REVALIDATION",
        "core_freeze": "V0.3.5 FULL_PASS core unchanged",
        "layer_b_source": "Binance USD-M Futures historical research data; live MASTER Bitget series remains separate",
        "validation": validation,
        "next_step": "BUILD_LAYER_C_ETF",
        "master_readiness": "NOT_READY_PENDING_LAYER_C_AND_EXPLICIT_APPROVAL",
        "master_integration": "FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL",
        "anti_overfit": "No gate lowering, no signal-rule tuning from outcomes, no future leakage, no N/A zero-fill, no cross-venue repair.",
    }
    (out / "v042_validation_summary.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(final, ensure_ascii=False))


if __name__ == "__main__":
    main_v042()
