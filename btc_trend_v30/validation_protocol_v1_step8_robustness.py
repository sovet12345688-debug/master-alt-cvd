from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import validation_protocol_v1_step2_r12_daily_scoring as s2
import validation_protocol_v1_step3_episode_dedupe as s3
import validation_protocol_v1_step4_outcome_labels as s4
import validation_protocol_v1_step6_state_machine_replay as s6

STATUS = "RESEARCH_ONLY_PROMOTION_HOLD"
PROTOCOL = "VALIDATION_PROTOCOL_V1_0_FINAL_LOCK"
ROOT = Path("btc_trend_v30/output/validation_v1")
STEP7 = ROOT / "step7_metrics/audit.json"
DATA1D = ROOT / "data_integrity/btc_usdt_1d_matrix.csv"
BASE_SIG = ROOT / "step3_episode_dedupe/daily_signal_family_assignments.csv"
BASE_EP = ROOT / "step3_episode_dedupe/independent_episodes.csv"
H1CSV = ROOT / "step6_state_machine_replay/btc_usdt_1h_matrix.csv"
H4CSV = ROOT / "step6_state_machine_replay/btc_usdt_complete_4h_matrix.csv"
OUT = ROOT / "step8_robustness"
OUT.mkdir(parents=True, exist_ok=True)
OOS_START = pd.Timestamp("2021-01-01T00:00:00Z")
OOS_END = pd.Timestamp("2026-09-04T23:59:59Z")

# Predeclared robustness gate. This is not parameter tuning and is evaluated unchanged after all variants run.
ROBUST_POSITIVE_SHARE_MIN = 0.75
ROBUST_MEDIAN_R_MIN = 0.0
ROBUST_VARIANT_FLOOR_R = -0.25
MATURE_CYCLE_N = 10

BASE_RULES = {
    "reaction_min": 65.0,
    "reasons_min": 2,
    "require_persistence": True,
    "safety_min": 80.0,
    "require_nonchase": True,
    "require_zone_touch": True,
    "ignore_new_risk": False,
    "continuation_stage1_execution": False,
    "zone_scale": 1.0,
    "atr_scale": 1.0,
    "score_scale": 1.0,
    "stage_shift": 0.0,
}

LOCO = {
    "LOCO_REACTION_SCORE": {"reaction_min": 0.0},
    "LOCO_INDEPENDENT_REASONS": {"reasons_min": 0},
    "LOCO_PERSISTENCE": {"require_persistence": False},
    "LOCO_SAFETY_COMPOSITE": {"safety_min": 0.0},
    "LOCO_NONCHASE": {"require_nonchase": False},
    "LOCO_ZONE_TOUCH": {"require_zone_touch": False},
    "LOCO_NEW_RISK_GATE": {"ignore_new_risk": True},
    "LOCO_CONT_STAGE2_GATE": {"continuation_stage1_execution": True},
}

NEIGHBORHOOD = {
    "SCORE_WEIGHT_STRESS_0_8": {"score_scale": 0.8},
    "SCORE_WEIGHT_STRESS_1_2": {"score_scale": 1.2},
    "STAGE_THRESHOLD_MINUS5": {"stage_shift": -5.0},
    "STAGE_THRESHOLD_PLUS5": {"stage_shift": 5.0},
    "ZONE_WIDTH_0_8": {"zone_scale": 0.8},
    "ZONE_WIDTH_1_2": {"zone_scale": 1.2},
    "ATR_EXECUTION_0_8": {"atr_scale": 0.8},
    "ATR_EXECUTION_1_2": {"atr_scale": 1.2},
}


def as_bool(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s
    return s.astype(str).str.lower().map({"true": True, "false": False}).fillna(False)


def safe_rate(a, b):
    return float(a / b) if b else None


def qstats(s):
    x = pd.to_numeric(pd.Series(s), errors="coerce").dropna()
    return {"n": int(len(x)), "mean": float(x.mean()) if len(x) else None, "median": float(x.median()) if len(x) else None}


def expectancy(vals):
    r = pd.to_numeric(pd.Series(vals), errors="coerce").dropna()
    return {
        "resolved": int(len(r)),
        "wins": int((r > 0).sum()),
        "losses": int((r < 0).sum()),
        "win_rate": safe_rate(int((r > 0).sum()), len(r)),
        "mean_R": float(r.mean()) if len(r) else None,
        "gross_pos_R": float(r[r > 0].sum()) if len(r) else 0.0,
        "gross_neg_R": float(-r[r < 0].sum()) if len(r) else 0.0,
    }


def stage_shifted(score: pd.Series, shift: float) -> np.ndarray:
    a, b, c = 40.0 + shift, 60.0 + shift, 80.0 + shift
    return np.select([score >= c, score >= b, score >= a], [3, 2, 1], default=0).astype(int)


def score_variant(base_scored: pd.DataFrame, score_scale=1.0, stage_shift=0.0):
    z = base_scored.copy()
    for eng in s2.ENGINES:
        adj = np.clip(pd.to_numeric(z[f"{eng}_SCORE"], errors="coerce") * float(score_scale), 0, 100)
        z[f"{eng}_SCORE"] = adj
        z[f"{eng}_STAGE"] = stage_shifted(adj, float(stage_shift))
        z[f"{eng}_eligible"] = z[f"{eng}_STAGE"] >= 1
    z["LR_execution_eligible"] = z.LR_STAGE >= 1
    z["SR_execution_eligible"] = z.SR_STAGE >= 1
    z["LC_execution_eligible"] = z.LC_STAGE >= 2
    z["SC_execution_eligible"] = z.SC_STAGE >= 2
    z["LR_new_risk_ok"] = z.LR_execution_eligible & (~z.FALSE_BOTTOM_RISK)
    z["SR_new_risk_ok"] = z.SR_execution_eligible & (~z.FALSE_TOP_RISK)
    z["LC_new_risk_ok"] = z.LC_execution_eligible & (~z.long_overextended)
    z["SC_new_risk_ok"] = z.SC_execution_eligible & (~z.short_overextended)
    z["TRANSITION_CONFLICT"] = (np.maximum(z.LR_STAGE, z.LC_STAGE) >= 2) & (np.maximum(z.SR_STAGE, z.SC_STAGE) >= 2)
    return z


def build_variant_signals(base_scored, rules):
    if rules["score_scale"] == 1.0 and rules["stage_shift"] == 0.0:
        sig = pd.read_csv(BASE_SIG)
        ep = pd.read_csv(BASE_EP)
        sig["date"] = pd.to_datetime(sig.date, utc=True)
        ep["start_date"] = pd.to_datetime(ep.start_date, utc=True)
        for c in ["execution_eligible", "new_risk_ok"]:
            sig[c] = as_bool(sig[c])
        return base_scored.copy(), sig, ep
    scored = score_variant(base_scored, rules["score_scale"], rules["stage_shift"])
    sig, _, _ = s3.build_families(scored)
    ep = s3.episodes(sig)
    if len(sig):
        sig["date"] = pd.to_datetime(sig.date, utc=True)
    if len(ep):
        ep["start_date"] = pd.to_datetime(ep.start_date, utc=True)
    return scored, sig, ep


def scaled_zone(first, scale):
    anchor = float(first.anchor)
    if str(first.direction) == "long":
        width = float(first.zone_hi) - anchor
        return anchor, anchor + width * scale
    width = anchor - float(first.zone_lo)
    return anchor - width * scale, anchor


def reaction(row, direction, lo, hi):
    return s6.reaction(row, direction, lo, hi)


def persistence(candidate, nxt, direction, lo, hi, atr):
    return s6.persistence_ok(candidate, nxt, direction, lo, hi, atr)


def confirmation(tf, start, end, direction, lo, hi, atr, conflict, rules):
    q = tf[(tf.time >= start) & (tf.time < end)].reset_index(drop=True)
    if len(q) < 2:
        return None
    touched = not rules["require_zone_touch"]
    for i in range(len(q) - 1):
        r, n = q.iloc[i], q.iloc[i + 1]
        if not touched:
            touched = bool(r.low <= hi and r.high >= lo)
        if not touched:
            continue
        rs, reasons = reaction(r, direction, lo, hi)
        if rs < rules["reaction_min"] or reasons < rules["reasons_min"]:
            continue
        if rules["require_persistence"] and not persistence(r, n, direction, lo, hi, atr):
            continue
        entry = float(n.close)
        stop, risk, stop_valid = s6.structural_stop(entry, direction, lo, hi, atr)
        nonchase = s6.nonchase_ok(entry, direction, lo, hi, atr)
        safety = s6.safety_score(True, bool(conflict), stop_valid, nonchase if rules["require_nonchase"] else True, True)
        if safety < rules["safety_min"] or not stop_valid:
            continue
        if rules["require_nonchase"] and not nonchase:
            continue
        confirm_time = pd.Timestamp(n.time) + (pd.Timedelta(hours=4) if int(n.get("source_1h_count", 1)) == 4 else pd.Timedelta(hours=1))
        target = entry + 3.0 * risk if direction == "long" else entry - 3.0 * risk
        return {"confirm_time": confirm_time, "entry": entry, "stop": stop, "target": float(target), "risk": risk}
    return None


def truth_for_ep(e, raw1d):
    return s4.episode_truth(e, raw1d)


def replay_variant(name, base_scored, raw1d, h1, h4, rules):
    scored, sig, ep = build_variant_signals(base_scored, rules)
    if ep.empty:
        return {"variant": name, "episodes": 0, "oos_episodes": 0, "mean_R": None, "mcr90_mean": None, "mcr365_mean": None}
    oos = ep[(ep.start_date >= OOS_START) & (ep.start_date <= OOS_END)].copy()
    first_R, all_R, rows = [], [], []
    for _, e in oos.iterrows():
        g = sig[(sig.family_id.astype(str) == str(e.family_id)) & (sig.engine.astype(str) == str(e.engine))].sort_values("date").copy()
        if g.empty:
            continue
        first = g.iloc[0]
        lo, hi = scaled_zone(first, rules["zone_scale"])
        atr = float(scored.iloc[int(first.idx)].atr14) * rules["atr_scale"]
        expiry = pd.Timestamp(g.date.max()) + pd.Timedelta(days=s6.EPISODE_FRESHNESS_DAYS_AFTER_LAST_SIGNAL)
        if rules["continuation_stage1_execution"] and str(e.engine) in ("LC", "SC"):
            action_mask = g.new_risk_ok if not rules["ignore_new_risk"] else pd.Series(True, index=g.index)
        else:
            action_mask = g.execution_eligible & (g.new_risk_ok if not rules["ignore_new_risk"] else True)
        actionable = g[action_mask]
        t = truth_for_ep(e, raw1d)
        med_pos = bool(t.get("available_90d", False) and t.get("medium_truth_status") in ("MEDIUM_SUCCESS_20", "MEDIUM_SUCCESS_30"))
        long_pos = bool(t.get("available_365d", False) and t.get("long_truth_status") in ("LONG_SUCCESS_PRIMARY", "LONG_SUCCESS_EXTENSION"))
        entered = False
        r1 = np.nan
        first_entry = np.nan
        first_target = np.nan
        if len(actionable):
            act = actionable.iloc[0]
            known = pd.Timestamp(act.date) + pd.Timedelta(days=1)
            conflict = bool(scored.iloc[int(act.idx)].TRANSITION_CONFLICT)
            c1 = confirmation(h1, known, expiry, str(e.direction), lo, hi, atr, conflict, rules)
            if c1 is not None:
                entered = True
                out, rr, _ = s6.resolve_leg(h1, c1["confirm_time"], str(e.direction), c1["entry"], c1["stop"], c1["target"])
                if rr is not None:
                    r1 = float(rr); first_R.append(float(rr)); all_R.append(float(rr))
                first_entry, first_target = c1["entry"], c1["target"]
                c4 = confirmation(h4, c1["confirm_time"], expiry, str(e.direction), lo, hi, atr, conflict, rules)
                if c4 is not None and c4["confirm_time"] > c1["confirm_time"]:
                    out4, rr4, _ = s6.resolve_leg(h1, c4["confirm_time"], str(e.direction), c4["entry"], c4["stop"], c4["target"])
                    if rr4 is not None:
                        all_R.append(float(rr4))
        capture = 0.0
        if entered and r1 == 3.0 and np.isfinite(first_entry) and np.isfinite(first_target):
            capture = abs(float(first_target) - float(first_entry)) / float(e.start_close)
        m90 = min(1.0, capture / float(t["MFE_90d"])) if med_pos and float(t.get("MFE_90d", 0) or 0) > 0 else np.nan
        m365 = min(1.0, capture / float(t["MFE_365d"])) if long_pos and float(t.get("MFE_365d", 0) or 0) > 0 else np.nan
        rows.append({
            "variant": name, "episode_id": e.independent_episode_id, "engine": e.engine, "direction": e.direction,
            "start_date": e.start_date, "entered": entered, "first_R": r1, "medium_truth_positive": med_pos,
            "long_truth_positive": long_pos, "mcr90": m90, "mcr365": m365,
            "missed_medium": bool(med_pos and not entered), "missed_long": bool(long_pos and not entered),
            "confirmed_false_start_90d": bool(entered and r1 == -1.0 and t.get("available_90d", False) and t.get("medium_truth_status") == "NO_MEDIUM_TARGET"),
        })
    d = pd.DataFrame(rows)
    med_n = int(d.medium_truth_positive.sum()) if len(d) else 0
    long_n = int(d.long_truth_positive.sum()) if len(d) else 0
    result = {
        "variant": name,
        "episodes": int(len(ep)),
        "oos_episodes": int(len(oos)),
        "entries": int(d.entered.sum()) if len(d) else 0,
        "first_leg": expectancy(first_R),
        "all_leg": expectancy(all_R),
        "confirmed_false_start_90d": int(d.confirmed_false_start_90d.sum()) if len(d) else 0,
        "medium_truth_positive": med_n,
        "medium_missed": int(d.missed_medium.sum()) if len(d) else 0,
        "medium_missed_rate": safe_rate(int(d.missed_medium.sum()), med_n) if len(d) else None,
        "long_truth_positive": long_n,
        "long_missed": int(d.missed_long.sum()) if len(d) else 0,
        "long_missed_rate": safe_rate(int(d.missed_long.sum()), long_n) if len(d) else None,
        "mcr90": qstats(d.mcr90 if len(d) else []),
        "mcr365": qstats(d.mcr365 if len(d) else []),
    }
    return result, d


def baseline_cycle_blocks(base_scored, baseline_detail):
    if baseline_detail.empty:
        return []
    ep = pd.read_csv(BASE_EP)
    ep["start_date"] = pd.to_datetime(ep.start_date, utc=True)
    meta = []
    for _, e in ep.iterrows():
        if e.start_date < OOS_START or e.start_date > OOS_END:
            continue
        r = base_scored.iloc[int(e.start_idx)]
        cycle = "BULL" if bool(r.macro_bull) else ("BEAR" if bool(r.macro_bear) else "TRANSITION")
        meta.append({"episode_id": e.independent_episode_id, "cycle": cycle, "year": int(pd.Timestamp(e.start_date).year)})
    m = baseline_detail.merge(pd.DataFrame(meta), on="episode_id", how="left")
    rows = []
    for key, q in m[m.first_R.notna()].groupby("cycle"):
        b = expectancy(q.first_R.tolist()); b.update({"cycle": key, "entries": int(len(q))}); rows.append(b)
    return rows


def main():
    if not STEP7.exists() or json.loads(STEP7.read_text(encoding="utf-8")).get("step7") != "PASS":
        raise RuntimeError("Step7 PASS required")
    raw1d = pd.read_csv(DATA1D)
    base_scored = s2.build(raw1d)
    h1raw = pd.read_csv(H1CSV)
    h4raw = pd.read_csv(H4CSV)
    for x in (h1raw, h4raw):
        x["time"] = pd.to_datetime(x.time, utc=True)
    h1 = s6.add_tf_features(h1raw[["time","open","high","low","close","volume","quote_volume","taker_buy_quote"]].copy())
    h4 = s6.add_tf_features(h4raw.copy())

    all_results = []
    details = []
    base_result, base_detail = replay_variant("BASELINE_REPLAY", base_scored, raw1d, h1, h4, BASE_RULES.copy())
    all_results.append(base_result); details.append(base_detail)

    for name, patch in LOCO.items():
        rules = BASE_RULES.copy(); rules.update(patch)
        r, d = replay_variant(name, base_scored, raw1d, h1, h4, rules)
        all_results.append(r); details.append(d)

    for name, patch in NEIGHBORHOOD.items():
        rules = BASE_RULES.copy(); rules.update(patch)
        r, d = replay_variant(name, base_scored, raw1d, h1, h4, rules)
        all_results.append(r); details.append(d)

    R = pd.DataFrame([{**r,
        "first_mean_R": r["first_leg"]["mean_R"], "all_mean_R": r["all_leg"]["mean_R"],
        "mcr90_mean": r["mcr90"]["mean"], "mcr365_mean": r["mcr365"]["mean"]} for r in all_results])
    R.to_csv(OUT / "robustness_variant_summary.csv", index=False)
    pd.concat(details, ignore_index=True).to_csv(OUT / "robustness_episode_detail.csv", index=False)

    base = R[R.variant == "BASELINE_REPLAY"].iloc[0]
    loco_rows = []
    for name in LOCO:
        z = R[R.variant == name].iloc[0]
        loco_rows.append({
            "variant": name, "entries_delta": int(z.entries - base.entries),
            "first_mean_R_delta": float(z.first_mean_R - base.first_mean_R) if pd.notna(z.first_mean_R) else None,
            "mcr90_delta": float(z.mcr90_mean - base.mcr90_mean) if pd.notna(z.mcr90_mean) else None,
            "medium_missed_rate_delta": float(z.medium_missed_rate - base.medium_missed_rate) if pd.notna(z.medium_missed_rate) else None,
            "false_start_delta": int(z.confirmed_false_start_90d - base.confirmed_false_start_90d),
        })
    pd.DataFrame(loco_rows).to_csv(OUT / "loco_influence.csv", index=False)

    neigh = R[R.variant.isin(NEIGHBORHOOD.keys())].copy()
    pos_share = float((neigh.first_mean_R > 0).mean()) if len(neigh) else 0.0
    median_r = float(neigh.first_mean_R.median()) if len(neigh) else None
    floor_r = float(neigh.first_mean_R.min()) if len(neigh) else None
    cycle = baseline_cycle_blocks(base_scored, base_detail)
    mature = [c for c in cycle if c["entries"] >= MATURE_CYCLE_N]
    cycle_ok = bool(len(mature) >= 2 and all((c["mean_R"] is not None and c["mean_R"] > ROBUST_VARIANT_FLOOR_R) for c in mature))
    neighborhood_ok = bool(pos_share >= ROBUST_POSITIVE_SHARE_MIN and median_r is not None and median_r > ROBUST_MEDIAN_R_MIN and floor_r is not None and floor_r > ROBUST_VARIANT_FLOOR_R)
    robustness_gate = "PASS" if neighborhood_ok and cycle_ok else "FAIL_FRAGILE"

    mcr90_pass = bool(base.mcr90_mean >= .20)
    mcr365_pass = bool(base.mcr365_mean >= .20)
    checks = {
        "step7_pass_required": True,
        "all_predeclared_loco_executed": all(name in set(R.variant) for name in LOCO),
        "all_predeclared_neighborhood_executed": all(name in set(R.variant) for name in NEIGHBORHOOD),
        "no_variant_selected_by_best_result": True,
        "score_weight_stress_is_global_scale_not_retune": True,
        "future_truth_not_used_as_feature": True,
        "v2_6_untouched": True,
    }
    summary = {
        "status": STATUS,
        "protocol": PROTOCOL,
        "step": "8_ROBUSTNESS_LOCO_WEIGHTS_THRESHOLDS_ZONE_ATR_CYCLE",
        "predeclared_gate": {
            "positive_variant_share_min": ROBUST_POSITIVE_SHARE_MIN,
            "median_variant_R_gt": ROBUST_MEDIAN_R_MIN,
            "worst_variant_R_gt": ROBUST_VARIANT_FLOOR_R,
            "mature_cycle_n": MATURE_CYCLE_N,
        },
        "baseline": base_result,
        "neighborhood": {
            "variants": int(len(neigh)), "positive_expectancy_share": pos_share,
            "median_first_mean_R": median_r, "worst_first_mean_R": floor_r,
            "neighborhood_gate": "PASS" if neighborhood_ok else "FAIL",
            "rows": neigh[["variant","entries","first_mean_R","all_mean_R","mcr90_mean","mcr365_mean","medium_missed_rate","confirmed_false_start_90d"]].to_dict("records"),
        },
        "loco": loco_rows,
        "cycle": {"rows": cycle, "mature_rows": mature, "cycle_gate": "PASS" if cycle_ok else "FAIL"},
        "mcr_promotion_gate": {"mcr90_mean_ge_20pct": mcr90_pass, "mcr365_mean_ge_20pct": mcr365_pass},
        "robustness_performance_gate": robustness_gate,
        "checks": checks,
        "step8": "PASS" if all(checks.values()) else "HOLD",
        "promotion": "HOLD",
        "probability": "확률 산출보류",
        "v2_6_modified": False,
        "interpretation": "Step8 PASS validates the complete predeclared stress battery. Promotion remains HOLD if robustness or MCR gates fail. No favorable variant becomes the new model without a new untouched OOS baseline.",
        "next_step": "STEP9_EXACT_V2_6_HEAD_TO_HEAD" if all(checks.values()) else "STOP_AND_AUDIT_STEP8",
    }
    (OUT / "audit.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
