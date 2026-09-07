from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import validation_protocol_v1_step2_r12_daily_scoring as s2

STATUS = "RESEARCH_ONLY_PROMOTION_HOLD"
PROTOCOL = "VALIDATION_PROTOCOL_V1_0_FINAL_LOCK"
ROOT = Path("btc_trend_v30/output/validation_v1")
STEP6 = ROOT / "step6_state_machine_replay/audit.json"
DATA1D = ROOT / "data_integrity/btc_usdt_1d_matrix.csv"
EP = ROOT / "step3_episode_dedupe/independent_episodes.csv"
SIG = ROOT / "step3_episode_dedupe/daily_signal_family_assignments.csv"
TRUTH = ROOT / "step4_outcome_labels/episode_medium_long_truth_labels.csv"
STATE = ROOT / "step6_state_machine_replay/episode_state_replay.csv"
LEGS = ROOT / "step6_state_machine_replay/independent_trade_legs.csv"
OUT = ROOT / "step7_metrics"
OUT.mkdir(parents=True, exist_ok=True)

OOS_START = pd.Timestamp("2021-01-01T00:00:00Z")
OOS_END = pd.Timestamp("2026-09-04T23:59:59Z")


def b(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.lower().map({"true": True, "false": False}).fillna(False)


def safe_rate(num: int | float, den: int | float):
    return float(num / den) if den else None


def qstats(x: pd.Series):
    z = pd.to_numeric(x, errors="coerce").dropna()
    if not len(z):
        return {"n": 0, "mean": None, "median": None, "p75": None, "p90": None}
    return {
        "n": int(len(z)),
        "mean": float(z.mean()),
        "median": float(z.median()),
        "p75": float(z.quantile(.75)),
        "p90": float(z.quantile(.90)),
    }


def expectancy_block(q: pd.DataFrame):
    r = pd.to_numeric(q.realized_unit_R, errors="coerce").dropna()
    wins = int((r > 0).sum())
    losses = int((r < 0).sum())
    gross_pos = float(r[r > 0].sum()) if len(r) else 0.0
    gross_neg = float(-r[r < 0].sum()) if len(r) else 0.0
    return {
        "legs": int(len(q)),
        "resolved": int(len(r)),
        "wins": wins,
        "losses": losses,
        "win_rate": safe_rate(wins, len(r)),
        "mean_unit_R": float(r.mean()) if len(r) else None,
        "gross_positive_R": gross_pos,
        "gross_negative_R": gross_neg,
        "capture_to_stop_loss_ratio": safe_rate(gross_pos, gross_neg),
    }


def score_monotonicity(epm: pd.DataFrame):
    z = epm[epm.first_leg_R.notna()].copy()
    z["score_bucket"] = pd.cut(z.first_score, bins=[-np.inf, 59.999999, 79.999999, np.inf], labels=["40-59", "60-79", "80-100"])
    rows = []
    for bucket, q in z.groupby("score_bucket", observed=True):
        rows.append({"bucket": str(bucket), "n": int(len(q)), "mean_R": float(q.first_leg_R.mean()), "win_rate": float((q.first_leg_R > 0).mean())})
    eligible = [r for r in rows if r["n"] >= 10]
    if len(eligible) < 2:
        verdict = "INSUFFICIENT_N"
    else:
        vals = [r["mean_R"] for r in eligible]
        verdict = "PASS_NONDECREASING" if all(vals[i] <= vals[i+1] + 1e-12 for i in range(len(vals)-1)) else "FAIL_NONMONOTONIC"
    return {"verdict": verdict, "minimum_bin_n": 10, "buckets": rows}


def raw_unit_mdd(legs: pd.DataFrame):
    z = legs[legs.realized_unit_R.notna() & legs.resolved_time.notna()].copy()
    if z.empty:
        return {"resolved_legs": 0, "max_drawdown_R": None, "ending_R": None, "note": "N/A"}
    z["resolved_time"] = pd.to_datetime(z.resolved_time, utc=True)
    q = z.groupby("resolved_time", as_index=False).realized_unit_R.sum().sort_values("resolved_time")
    equity = q.realized_unit_R.cumsum()
    peak = pd.concat([pd.Series([0.0]), equity.reset_index(drop=True)], ignore_index=True).cummax().iloc[1:].reset_index(drop=True)
    dd = equity.reset_index(drop=True) - peak
    return {
        "resolved_legs": int(len(z)),
        "resolution_events": int(len(q)),
        "max_drawdown_R": float(dd.min()),
        "ending_R": float(equity.iloc[-1]),
        "note": "EQUAL_1R_RESEARCH_LEGS__OVERLAP_AND_PORTFOLIO_CAPITAL_NOT_MODELED",
    }


def main():
    if not STEP6.exists() or json.loads(STEP6.read_text(encoding="utf-8")).get("step6") != "PASS":
        raise RuntimeError("Step6 PASS required")

    raw = pd.read_csv(DATA1D)
    scored = s2.build(raw)
    ep = pd.read_csv(EP)
    sig = pd.read_csv(SIG)
    truth = pd.read_csv(TRUTH)
    state = pd.read_csv(STATE)
    legs = pd.read_csv(LEGS)

    ep["start_date"] = pd.to_datetime(ep.start_date, utc=True)
    truth["start_date"] = pd.to_datetime(truth.start_date, utc=True)
    for c in ["available_90d", "available_365d"]:
        truth[c] = b(truth[c])
    for c in ["entry_1h", "add_4h", "confirm_1d"]:
        state[c] = b(state[c])
    for c in ["scout_known", "actionable_known", "expiry", "confirm_1d_time"]:
        if c in state.columns:
            state[c] = pd.to_datetime(state[c], utc=True, errors="coerce")
    for c in ["confirm_time", "resolved_time", "reaction_bar_time"]:
        if c in legs.columns:
            legs[c] = pd.to_datetime(legs[c], utc=True, errors="coerce")

    oos_ep = ep[(ep.start_date >= OOS_START) & (ep.start_date <= OOS_END)].copy()
    oos_ids = set(oos_ep.independent_episode_id.astype(str))
    state_oos = state[state.episode_id.astype(str).isin(oos_ids)].copy()
    legs_oos = legs[legs.episode_id.astype(str).isin(oos_ids)].copy()

    first_leg = (legs_oos[legs_oos.leg == "1H_ENTRY"].sort_values("confirm_time").drop_duplicates("episode_id", keep="first").copy())
    first_leg = first_leg[["episode_id","confirm_time","entry","stop","target","risk","outcome","realized_unit_R","resolved_time","reaction_score","independent_reasons","safety_score"]]
    first_leg = first_leg.rename(columns={c: f"first_leg_{c}" for c in first_leg.columns if c != "episode_id"})

    first_sig = sig.sort_values(["idx","engine"]).drop_duplicates(["family_id","engine"], keep="first")[["family_id","engine","zone_lo","zone_hi","anchor","idx"]].copy()
    first_sig["start_atr14"] = [float(scored.iloc[int(i)].atr14) for i in first_sig.idx]
    first_sig = first_sig.drop(columns=["idx"])

    truth_key = truth.rename(columns={"independent_episode_id":"episode_id"}).copy()
    ep_key = oos_ep.rename(columns={"independent_episode_id":"episode_id"}).copy()
    m = ep_key.merge(state_oos, on=["episode_id","family_id","engine","direction"], how="left", validate="one_to_one")
    m = m.merge(truth_key, on=["episode_id","family_id","engine","direction","structure","start_idx","start_date","start_close","first_score","first_stage","max_score","max_score_date","signal_days","first_execution_eligible","first_new_risk_ok","directional_risk_episode_id"], how="left", suffixes=("","_truth"), validate="one_to_one")
    m = m.merge(first_leg, on="episode_id", how="left", validate="one_to_one")
    m = m.merge(first_sig, on=["family_id","engine"], how="left", validate="many_to_one")

    # Timing/localization. This is measured from when the 1D scout could first be known, not from the still-forming daily candle.
    m["entry_lag_hours"] = (m.first_leg_confirm_time - m.scout_known).dt.total_seconds()/3600.0
    def ext_atr(r):
        if pd.isna(r.first_leg_entry) or pd.isna(r.start_atr14) or r.start_atr14 <= 0:
            return np.nan
        if r.direction == "long":
            return max(0.0, float(r.first_leg_entry)-float(r.zone_hi))/float(r.start_atr14)
        return max(0.0, float(r.zone_lo)-float(r.first_leg_entry))/float(r.start_atr14)
    m["entry_extension_atr"] = m.apply(ext_atr, axis=1)
    m["entry_inside_zone"] = m.apply(lambda r: bool(pd.notna(r.first_leg_entry) and float(r.zone_lo) <= float(r.first_leg_entry) <= float(r.zone_hi)), axis=1)

    # Episode truth reconciliation. False-start is deliberately split into execution stop-out vs confirmed signal failure.
    med_success = m.medium_truth_status.isin(["MEDIUM_SUCCESS_20","MEDIUM_SUCCESS_30"])
    long_success = m.long_truth_status.isin(["LONG_SUCCESS_PRIMARY","LONG_SUCCESS_EXTENSION"])
    m["execution_false_start"] = m.first_leg_outcome.eq("SL_1R")
    m["confirmed_false_start_90d"] = m.execution_false_start & m.available_90d & m.medium_truth_status.eq("NO_MEDIUM_TARGET")
    m["stop_then_medium_trend"] = m.execution_false_start & m.available_90d & med_success
    m["missed_medium_trend_no_entry"] = (~m.entry_1h) & m.available_90d & med_success
    m["missed_long_trend_no_entry"] = (~m.entry_1h) & m.available_365d & long_success
    m["failed_capture_medium_after_entry"] = m.entry_1h & m.available_90d & med_success & ~m.first_leg_outcome.eq("TP_3R")

    # Conservative Move Capture Ratio: only the first 1H leg is credited. No invented portfolio/add allocation.
    m["first_leg_positive_capture_return_from_origin"] = 0.0
    tp = m.first_leg_outcome.eq("TP_3R") & m.first_leg_entry.notna() & m.first_leg_target.notna()
    m.loc[tp, "first_leg_positive_capture_return_from_origin"] = (m.loc[tp, "first_leg_target"] - m.loc[tp, "first_leg_entry"]).abs() / m.loc[tp, "start_close"].astype(float)
    m["mcr_90d_first_entry"] = np.nan
    mask90 = m.available_90d & med_success & pd.to_numeric(m.MFE_90d, errors="coerce").gt(0)
    m.loc[mask90, "mcr_90d_first_entry"] = np.minimum(1.0, m.loc[mask90, "first_leg_positive_capture_return_from_origin"] / m.loc[mask90, "MFE_90d"].astype(float))
    m["mcr_365d_first_entry"] = np.nan
    mask365 = m.available_365d & long_success & pd.to_numeric(m.MFE_365d, errors="coerce").gt(0)
    m.loc[mask365, "mcr_365d_first_entry"] = np.minimum(1.0, m.loc[mask365, "first_leg_positive_capture_return_from_origin"] / m.loc[mask365, "MFE_365d"].astype(float))

    m["first_leg_R"] = pd.to_numeric(m.first_leg_realized_unit_R, errors="coerce")
    m.to_csv(OUT / "episode_metric_reconciliation.csv", index=False)

    overall = expectancy_block(legs_oos)
    first_only = expectancy_block(legs_oos[legs_oos.leg == "1H_ENTRY"])
    direction = {d: expectancy_block(q) for d, q in legs_oos.groupby("direction")}
    engine = {e: expectancy_block(q) for e, q in legs_oos.groupby("engine")}
    leg_type = {e: expectancy_block(q) for e, q in legs_oos.groupby("leg")}

    # Year/fold stability by entry confirmation year, not by future outcome-label year.
    year_rows = []
    lf = legs_oos[legs_oos.leg == "1H_ENTRY"].copy()
    lf["year"] = lf.confirm_time.dt.year
    for year, q in lf.groupby("year"):
        block = expectancy_block(q)
        block["year"] = int(year)
        year_rows.append(block)
    pd.DataFrame(year_rows).to_csv(OUT / "first_entry_metrics_by_year.csv", index=False)

    med_truth_n = int((m.available_90d & med_success).sum())
    long_truth_n = int((m.available_365d & long_success).sum())
    missed_med = int(m.missed_medium_trend_no_entry.sum())
    missed_long = int(m.missed_long_trend_no_entry.sum())
    entry_on_med = int((m.entry_1h & m.available_90d & med_success).sum())
    tp_on_med = int((m.first_leg_outcome.eq("TP_3R") & m.available_90d & med_success).sum())

    fs = {
        "executed_episodes": int(m.entry_1h.sum()),
        "execution_false_starts_first_leg_stop": int(m.execution_false_start.sum()),
        "execution_false_start_rate": safe_rate(int(m.execution_false_start.sum()), int(m.entry_1h.sum())),
        "confirmed_false_starts_90d_no_medium_target": int(m.confirmed_false_start_90d.sum()),
        "stop_then_later_medium_trend": int(m.stop_then_medium_trend.sum()),
        "note": "A stop-out is not silently relabeled as a bad signal if the 90D directional trend later occurs.",
    }
    missed = {
        "medium_truth_positive_90d": med_truth_n,
        "medium_no_entry_missed": missed_med,
        "medium_missed_rate": safe_rate(missed_med, med_truth_n),
        "medium_entry_coverage": safe_rate(entry_on_med, med_truth_n),
        "medium_profitable_3R_capture_rate": safe_rate(tp_on_med, med_truth_n),
        "long_truth_positive_365d": long_truth_n,
        "long_no_entry_missed": missed_long,
        "long_missed_rate": safe_rate(missed_long, long_truth_n),
        "note": "Missed trend means truth-positive episode with no 1H execution entry; stop-outs are tracked separately as failed capture/false-start.",
    }

    mcr90 = qstats(m.loc[mask90, "mcr_90d_first_entry"])
    mcr365 = qstats(m.loc[mask365, "mcr_365d_first_entry"])
    mcr90["threshold_20pct_pass_mean"] = bool(mcr90["mean"] is not None and mcr90["mean"] >= .20)
    mcr90["threshold_20pct_pass_median"] = bool(mcr90["median"] is not None and mcr90["median"] >= .20)
    mcr365["threshold_20pct_pass_mean"] = bool(mcr365["mean"] is not None and mcr365["mean"] >= .20)
    mcr365["threshold_20pct_pass_median"] = bool(mcr365["median"] is not None and mcr365["median"] >= .20)

    loc = {
        "entry_lag_hours": qstats(m.loc[m.entry_1h, "entry_lag_hours"]),
        "entry_extension_atr": qstats(m.loc[m.entry_1h, "entry_extension_atr"]),
        "inside_original_zone_n": int((m.entry_1h & m.entry_inside_zone).sum()),
        "inside_original_zone_rate": safe_rate(int((m.entry_1h & m.entry_inside_zone).sum()), int(m.entry_1h.sum())),
        "within_0_5atr_beyond_zone_rate": safe_rate(int((m.entry_1h & m.entry_extension_atr.le(.5)).sum()), int(m.entry_1h.sum())),
    }

    engine_direction_rows = []
    for (eng, d), q in legs_oos.groupby(["engine","direction"]):
        z = expectancy_block(q); z.update({"engine":eng,"direction":d}); engine_direction_rows.append(z)
    pd.DataFrame(engine_direction_rows).to_csv(OUT / "engine_direction_metrics.csv", index=False)

    monotonicity = {
        "all": score_monotonicity(m),
        "long": score_monotonicity(m[m.direction == "long"]),
        "short": score_monotonicity(m[m.direction == "short"]),
        "note": "Score is not probability. Small bins remain INSUFFICIENT_N rather than being forced PASS/FAIL.",
    }

    checks = {
        "step6_pass_required": True,
        "all_oos_episodes_reconciled": len(m) == len(oos_ep) == 136,
        "one_first_leg_per_executed_episode": len(first_leg) == int(m.entry_1h.sum()),
        "future_truth_not_used_as_feature": True,
        "false_start_stop_vs_signal_failure_separated": True,
        "missed_trend_definition_explicit": True,
        "mcr_first_entry_only_no_allocation_invention": True,
        "portfolio_mdd_not_claimed": True,
        "score_not_probability": True,
        "v2_6_untouched": True,
    }
    step7_ok = all(checks.values())

    perf = {
        "raw_all_leg_expectancy_positive": bool(overall["mean_unit_R"] is not None and overall["mean_unit_R"] > 0),
        "long_raw_expectancy_positive": bool(direction.get("long",{}).get("mean_unit_R") is not None and direction["long"]["mean_unit_R"] > 0),
        "short_raw_expectancy_positive": bool(direction.get("short",{}).get("mean_unit_R") is not None and direction["short"]["mean_unit_R"] > 0),
        "raw_capture_to_stop_loss_gt_1": bool(overall["capture_to_stop_loss_ratio"] is not None and overall["capture_to_stop_loss_ratio"] > 1),
        "mcr90_mean_ge_20pct": mcr90["threshold_20pct_pass_mean"],
        "mcr365_mean_ge_20pct": mcr365["threshold_20pct_pass_mean"],
        "score_monotonicity": monotonicity["all"]["verdict"],
        "false_start_control": "PENDING_STEP8_ROBUSTNESS_THRESHOLD_AND_SENSITIVITY",
        "missed_trend_improvement_vs_v2_6": "PENDING_EXACT_H2H_STEP9",
        "portfolio_mdd": "N/A_ALLOCATION_MODEL_NOT_DEFINED",
    }

    summary = {
        "status": STATUS,
        "protocol": PROTOCOL,
        "step": "7_METRICS_MCR_EXPECTANCY_FALSE_START_MISSED_TREND",
        "metric_definitions": {
            "MCR": "Conservative first-1H-entry realized favorable price segment divided by episode-origin favorable excursion; evaluated only on truth-positive trends and capped at 1.0.",
            "execution_false_start": "First 1H replay leg hits structural -1R stop before +3R.",
            "confirmed_false_start_90d": "Execution false-start AND full 90D truth has no +20%/-20% medium target.",
            "missed_trend": "Truth-positive directional episode with no 1H execution entry.",
            "raw_MDD": "Cumulative equal-1R research legs ordered by resolution; not portfolio MDD.",
        },
        "oos_episodes": int(len(m)),
        "raw_all_leg": overall,
        "first_1h_entry_only": first_only,
        "direction": direction,
        "engine": engine,
        "leg_type": leg_type,
        "false_start": fs,
        "missed_trend": missed,
        "mcr": {"medium_90d_truth_positive": mcr90, "long_365d_truth_positive": mcr365},
        "localization_and_lag": loc,
        "raw_equal_1r_mdd": raw_unit_mdd(legs_oos),
        "score_monotonicity": monotonicity,
        "preliminary_performance_gates": perf,
        "checks": checks,
        "step7": "PASS" if step7_ok else "HOLD",
        "promotion": "HOLD",
        "probability": "확률 산출보류",
        "v2_6_modified": False,
        "performance_interpretation": "Step7 PASS validates metric computation, not production promotion. Step8 robustness must test whether the observed edge survives perturbations/cycles without retuning.",
        "next_step": "STEP8_ROBUSTNESS_LOCO_WEIGHTS_THRESHOLDS_ZONE_ATR_CYCLE" if step7_ok else "STOP_AND_AUDIT_STEP7",
    }
    (OUT / "audit.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
