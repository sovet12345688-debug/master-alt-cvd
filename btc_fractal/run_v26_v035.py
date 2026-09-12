from __future__ import annotations

import json
import numpy as np
import pandas as pd

import run_v26_v034 as v034

v033 = v034.v033
v032 = v034.v032
v02 = v034.v02
core = v034.core
D = v034.D
REGIMES = v034.REGIMES
BULLISH_REGIMES = v034.BULLISH_REGIMES
BEARISH_REGIMES = v034.BEARISH_REGIMES
REGIME_KO = v034.REGIME_KO


REGIME_SUBSPACES = {
    "PANIC_BOTTOM": {
        "state": ["dist_sma20", "dist_sma50", "dist_sma200", "rsi14", "dd_from_365d_high", "rebound_from_365d_low"],
        "path": ["ret_7d", "ret_30d", "ret_90d", "vol_30d", "vol_90d", "path_eff_30d", "path_eff_90d", "sma50_slope30"],
        "weights": (0.68, 0.32),
    },
    "BOTTOM": {
        "state": ["dist_sma20", "dist_sma50", "dist_sma200", "rsi14", "dd_from_365d_high", "rebound_from_365d_low"],
        "path": ["ret_30d", "ret_90d", "ret_180d", "vol_30d", "vol_90d", "path_eff_30d", "path_eff_90d", "sma50_slope30"],
        "weights": (0.65, 0.35),
    },
    "UP_IGNITION": {
        "state": ["dist_sma20", "dist_sma50", "dist_sma200", "rsi14", "dd_from_365d_high", "rebound_from_365d_low"],
        "path": ["ret_7d", "ret_30d", "ret_90d", "vol_30d", "path_eff_30d", "path_eff_90d", "sma50_slope30"],
        "weights": (0.35, 0.65),
    },
    "UPTREND": {
        "state": ["dist_sma20", "dist_sma50", "dist_sma200", "rsi14", "dd_from_365d_high", "rebound_from_365d_low"],
        "path": ["ret_30d", "ret_90d", "ret_180d", "ret_365d", "vol_90d", "path_eff_90d", "path_eff_180d", "sma50_slope30", "sma200_slope30"],
        "weights": (0.45, 0.55),
    },
    "TOP": {
        "state": ["dist_sma20", "dist_sma50", "dist_sma200", "rsi14", "dd_from_365d_high", "rebound_from_365d_low"],
        "path": ["ret_30d", "ret_90d", "ret_180d", "vol_30d", "vol_90d", "path_eff_30d", "path_eff_90d", "sma50_slope30"],
        "weights": (0.62, 0.38),
    },
    "DOWN_IGNITION": {
        "state": ["dist_sma20", "dist_sma50", "dist_sma200", "rsi14", "dd_from_365d_high", "rebound_from_365d_low"],
        "path": ["ret_7d", "ret_30d", "ret_90d", "vol_30d", "path_eff_30d", "path_eff_90d", "sma50_slope30"],
        "weights": (0.35, 0.65),
    },
}


def _regime_similarity(features: pd.DataFrame, cfg: dict, qt: pd.Timestamp, regime: str) -> pd.Series:
    spec = REGIME_SUBSPACES[regime]
    cols = [c for c in spec["state"] + spec["path"] if c in features.columns]
    sub = features[["price"] + cols].copy()
    state_w, path_w = spec["weights"]
    rcfg = json.loads(json.dumps(cfg))
    rcfg["feature_group_weights"] = {
        "price_state": float(state_w),
        "price_path": float(path_w),
        "onchain_state": 0.0,
        "onchain_path": 0.0,
        "macro": 0.0,
    }
    rcfg["min_complete_features"] = max(4, min(int(cfg.get("min_complete_features", 8)), max(4, len(cols) // 2)))
    scores = v02.similarity_scores_fast(sub.loc[:qt], qt, rcfg, required_horizon=365)
    return pd.to_numeric(scores["similarity"], errors="coerce") if not scores.empty else pd.Series(dtype=float)


def regime_specific_episode_reps(features: pd.DataFrame, events: pd.DataFrame, cfg: dict, qt: pd.Timestamp) -> pd.DataFrame:
    # V0.3.4 establishes one globally unique representative date per episode. V0.3.5 keeps that
    # anti-duplication set but remeasures each representative in a regime-specific price subspace.
    base = v034._episode_representatives(features, events, cfg, qt)
    if base.empty:
        return base
    pieces = []
    for regime in REGIMES:
        z = base[base["regime"] == regime].copy()
        if z.empty:
            continue
        sim = _regime_similarity(features, cfg, qt, regime)
        z["similarity"] = sim.reindex(z.index)
        z = z[z["similarity"].notna()].copy()
        if not z.empty:
            pieces.append(z)
    return pd.concat(pieces).sort_index() if pieces else pd.DataFrame()


def regime_evidence_v035(reps: pd.DataFrame, regime: str, cfg: dict) -> dict:
    c = cfg.get("v035", {})
    k = int(c.get("top_k_per_regime", 5))
    min_n = int(c.get("min_regime_representatives", 4))
    r = reps[reps["regime"] == regime].sort_values("similarity", ascending=False).copy()
    if len(r) < min_n:
        return {"regime": regime, "label_ko": REGIME_KO[regime], "score": None, "status": "INSUFFICIENT_HISTORY", "representatives": int(len(r)), "analogs": []}

    top = r.head(min(k, len(r))).copy()
    sims = pd.to_numeric(r["similarity"], errors="coerce").dropna()
    ts = pd.to_numeric(top["similarity"], errors="coerce").dropna()
    raw = 0.50 * float(ts.mean()) + 0.30 * float(ts.median()) + 0.20 * float(ts.iloc[0])
    med = float(sims.median())
    iqr = v034._safe_iqr(sims)
    z = 0.0 if not np.isfinite(iqr) else (raw - med) / iqr
    normalized = float(np.clip(50.0 + 15.0 * z, 0, 100))
    absolute = float(np.clip((raw - 50.0) / 40.0 * 100.0, 0, 100))

    years = int(len(set(top.index.year)))
    support = min(1.0, len(r) / max(float(c.get("full_support_representatives", 10)), 1.0))
    diversity = min(1.0, years / max(float(c.get("full_support_years", 5)), 1.0))
    quality = 0.78 + 0.12 * support + 0.10 * diversity
    w_abs = float(c.get("score_weights", {}).get("absolute", 0.45))
    w_norm = float(c.get("score_weights", {}).get("normalized", 0.55))
    score = int(round(np.clip((w_abs * absolute + w_norm * normalized) * quality, 0, 100)))

    return {
        "regime": regime,
        "label_ko": REGIME_KO[regime],
        "score": score,
        "status": "OK",
        "representatives": int(len(r)),
        "distinct_years_top": years,
        "raw_top_similarity": round(raw, 2),
        "class_median_similarity": round(med, 2),
        "class_normalized_z": round(float(z), 3),
        "absolute_component": round(absolute, 2),
        "normalized_component": round(normalized, 2),
        "analogs": [
            {"date": t.date().isoformat(), "similarity": round(float(row["similarity"]), 2), "episode_id": int(row["episode_id"])}
            for t, row in top.iterrows()
        ],
    }


def regime_predict_v035(features: pd.DataFrame, events: pd.DataFrame, cfg: dict, qt: pd.Timestamp) -> dict:
    reps = regime_specific_episode_reps(features, events, cfg, qt)
    if reps.empty:
        return {"pred": "ABSTAIN", "query_date": qt.date().isoformat(), "reason": "NO_ELIGIBLE_HISTORY"}
    evidence = {r: regime_evidence_v035(reps, r, cfg) for r in REGIMES}
    valid = [x for x in evidence.values() if x.get("score") is not None]
    valid.sort(key=lambda x: x["score"], reverse=True)
    if not valid:
        return {"pred": "ABSTAIN", "query_date": qt.date().isoformat(), "reason": "NO_VALID_REGIME_SCORE"}

    top1 = valid[0]
    top2 = valid[1] if len(valid) > 1 else None
    margin = float(top1["score"] - (top2["score"] if top2 else 0))
    top_dir = D[0] if top1["regime"] in BULLISH_REGIMES else D[1]
    min_margin = float(cfg.get("v035", {}).get("direction_min_regime_margin", 3.0))
    pred = top_dir if margin >= min_margin else "ABSTAIN"

    # Direction balance is derived from the strongest bullish and bearish regime, equal cardinality.
    bull_vals = sorted([float(evidence[r]["score"]) for r in BULLISH_REGIMES if evidence[r].get("score") is not None], reverse=True)
    bear_vals = sorted([float(evidence[r]["score"]) for r in BEARISH_REGIMES if evidence[r].get("score") is not None], reverse=True)
    bull = bull_vals[0] if bull_vals else 0.0
    bear = bear_vals[0] if bear_vals else 0.0
    temp = max(float(cfg.get("v035", {}).get("direction_balance_temperature", 8.0)), 1e-6)
    long_share = 100.0 / (1.0 + np.exp(-(bull - bear) / temp))

    confidence = int(round(np.clip(
        0.55 * min(100.0, margin / 15.0 * 100.0)
        + 0.25 * min(100.0, top1["score"])
        + 0.10 * min(100.0, len(reps) / 50.0 * 100.0)
        + 0.10 * min(100.0, len(set(reps.index.year)) / 8.0 * 100.0),
        0, 100,
    )))
    grade = "HIGH" if confidence >= 75 and margin >= 7 else ("MEDIUM" if confidence >= 55 and margin >= min_margin else "LOW")
    analog_dates = [x["date"] for x in top1.get("analogs", [])]
    return {
        "pred": pred,
        "query_date": qt.date().isoformat(),
        "dominant_regime": top1["regime"],
        "dominant_regime_ko": top1["label_ko"],
        "dominant_regime_score": top1["score"],
        "second_regime": None if top2 is None else top2["regime"],
        "second_regime_score": None if top2 is None else top2["score"],
        "regime_margin": round(margin, 2),
        "regime_scores": evidence,
        "direction_evidence": {
            "bullish_best_regime_score": round(bull, 2),
            "bearish_best_regime_score": round(bear, 2),
            "long_share": round(float(long_share), 1),
            "short_share": round(float(100 - long_share), 1),
            "warning": "Evidence balance, not calibrated probability.",
        },
        "historical_outcomes_of_dominant_regime": v034._outcome_summary(events, analog_dates, cfg),
        "confidence_score": confidence,
        "confidence_grade": grade,
        "eligible_episode_representatives": int(len(reps)),
        "distinct_years": int(len(set(reps.index.year))),
        "warning": "Regime similarity scores and historical outcome rates are separate outputs; neither is a guaranteed future probability.",
    }


def walk_forward_v035(features: pd.DataFrame, events: pd.DataFrame, cfg: dict, anchor_wf: pd.DataFrame) -> pd.DataFrame:
    labels = v034.build_regime_labels(features, events)
    eps = v02.episode_ids(features["price"].dropna(), float(cfg.get("episode_reversal_pct", 0.20)), int(cfg.get("episode_max_days", 365)))
    rows = []
    for qt, base in anchor_wf.iterrows():
        actual = base.get("actual")
        try:
            p = regime_predict_v035(features, events, cfg, qt)
        except Exception as e:
            rows.append({"time": qt, "actual": actual, "error": str(e)})
            continue
        ranked = sorted([(r, z.get("score")) for r, z in p.get("regime_scores", {}).items() if z.get("score") is not None], key=lambda x: x[1], reverse=True)
        actual_regime = labels.get(qt, "UNLABELED")
        top2 = [x[0] for x in ranked[:2]]
        pred = p.get("pred")
        rows.append({
            "time": qt,
            "pred": pred,
            "actual": actual,
            "correct": pred == actual if pred in D and actual in D else np.nan,
            "confidence": p.get("confidence_score"),
            "confidence_grade": p.get("confidence_grade"),
            "dominant_regime": p.get("dominant_regime"),
            "dominant_regime_score": p.get("dominant_regime_score"),
            "second_regime": p.get("second_regime"),
            "second_regime_score": p.get("second_regime_score"),
            "actual_regime": actual_regime,
            "regime_exact_correct": p.get("dominant_regime") == actual_regime if actual_regime in REGIMES else np.nan,
            "regime_top2_correct": actual_regime in top2 if actual_regime in REGIMES else np.nan,
            "long_share": p.get("direction_evidence", {}).get("long_share"),
            "test_episode_id": int(eps.loc[qt]) if qt in eps.index and pd.notna(eps.loc[qt]) else np.nan,
        })
    return pd.DataFrame(rows).set_index("time") if rows else pd.DataFrame()


def regime_metrics(indep: pd.DataFrame) -> dict:
    z = indep[indep["actual_regime"].isin(REGIMES)].copy()
    if z.empty:
        return {"rows": 0}
    recalls1, recalls2 = [], []
    per = {}
    for r in REGIMES:
        a = z[z["actual_regime"] == r]
        r1 = None if not len(a) else float((a["dominant_regime"] == r).mean()) * 100
        r2 = None if not len(a) else float(a["regime_top2_correct"].mean()) * 100
        if r1 is not None:
            recalls1.append(r1)
        if r2 is not None:
            recalls2.append(r2)
        per[r] = {"actual_count": int(len(a)), "top1_recall_pct": None if r1 is None else round(r1, 2), "top2_recall_pct": None if r2 is None else round(r2, 2)}
    exact = float(z["regime_exact_correct"].mean()) * 100
    top2 = float(z["regime_top2_correct"].mean()) * 100
    actual_counts = z["actual_regime"].value_counts()
    common = list(actual_counts.index[:2])
    naive1 = float(actual_counts.iloc[0] / len(z) * 100)
    naive2 = float(actual_counts.iloc[:2].sum() / len(z) * 100)
    return {
        "rows": int(len(z)),
        "exact_accuracy": round(exact, 2),
        "top2_accuracy": round(top2, 2),
        "balanced_top1_recall": round(float(np.mean(recalls1)), 2),
        "balanced_top2_recall": round(float(np.mean(recalls2)), 2),
        "naive_most_common_exact": round(naive1, 2),
        "naive_two_most_common_top2": round(naive2, 2),
        "exact_gain_vs_naive_pp": round(exact - naive1, 2),
        "top2_gain_vs_naive_pp": round(top2 - naive2, 2),
        "naive_common_regimes": common,
        "actual_mix": {str(k): int(v) for k, v in actual_counts.to_dict().items()},
        "predicted_mix": {str(k): int(v) for k, v in z["dominant_regime"].value_counts().to_dict().items()},
        "per_regime": per,
    }


def validate_v035(indep: pd.DataFrame, v034_wf: pd.DataFrame, cfg: dict) -> dict:
    direction = v032.stats(indep, "pred")
    old_aligned = indep[["actual"]].join(v034_wf[["pred"]].rename(columns={"pred": "v034_pred"}), how="left")
    old = v032.stats(old_aligned, "v034_pred")
    regime = regime_metrics(indep)
    c = cfg.get("v035", {}).get("acceptance", {})
    rr = direction.get("class_recalls", {})
    mx = direction.get("prediction_mix", {})
    minority = min(float(mx.get("up_pct") or 0), float(mx.get("down_pct") or 0))
    direction_gates = {
        "coverage_gate": float(direction.get("coverage_pct") or 0) >= float(c.get("min_direction_coverage_pct", 45)),
        "balanced_accuracy_gate": direction.get("balanced_accuracy") is not None and direction["balanced_accuracy"] >= float(c.get("min_direction_balanced_accuracy", 55)),
        "both_class_recall_gate": all(rr.get(x) is not None and rr[x] >= float(c.get("min_each_direction_recall_pct", 35)) for x in D),
        "nondegenerate_gate": minority >= float(c.get("min_minority_direction_pct", 20)),
    }
    regime_gates = {
        "independent_episode_gate": len(indep) >= int(c.get("min_independent_episodes", 30)),
        "exact_accuracy_gate": regime.get("exact_accuracy", 0) >= float(c.get("min_regime_exact_accuracy", 40)),
        "exact_gain_vs_naive_gate": regime.get("exact_gain_vs_naive_pp", -999) >= float(c.get("min_exact_gain_vs_naive_pp", 3)),
        "balanced_top1_gate": regime.get("balanced_top1_recall", 0) >= float(c.get("min_balanced_top1_recall", 30)),
        "balanced_top2_gate": regime.get("balanced_top2_recall", 0) >= float(c.get("min_balanced_top2_recall", 50)),
    }
    regime_pass = bool(all(regime_gates.values()))
    direction_pass = bool(all(direction_gates.values()))
    if regime_pass and direction_pass:
        stage = "FULL_PASS"
        next_step = "RETEST_ONCHAIN_MACRO_CONFIRMATION"
    elif regime_pass:
        stage = "REGIME_CORE_PASS_DIRECTION_PARTIAL"
        next_step = "KEEP_REGIME_ENGINE_READ_ONLY_AND_IMPROVE_DIRECTION_LAYER_SEPARATELY"
    else:
        stage = "FAIL"
        next_step = "IMPROVE_REGIME_SUBSPACES_WITHOUT_LOWERING_GATES"
    return {
        "independent_episode_rows": int(len(indep)),
        "regime_core": regime,
        "regime_gates": regime_gates,
        "regime_core_pass": regime_pass,
        "direction_layer": direction,
        "v034_direction_benchmark": old,
        "direction_gates": direction_gates,
        "direction_pass": direction_pass,
        "v035_stage_gate": stage,
        "next_step": next_step,
        "master_readiness": "NOT_READY_PENDING_FULL_SEQUENCE_AND_EXPLICIT_USER_APPROVAL",
        "master_integration": "FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL",
    }


def main_v035() -> None:
    v034.main_v034()
    cfg, out, data = core.load_config(), core.OUT, core.DATA
    features = pd.read_csv(data / "historical_features_daily.csv", parse_dates=["time"]).set_index("time")
    events = pd.read_csv(data / "event_registry.csv", parse_dates=["time"]).set_index("time")
    anchor_wf = pd.read_csv(out / "walk_forward_price_core.csv", parse_dates=["time"]).set_index("time")
    v034_wf = pd.read_csv(out / "walk_forward_v034.csv", parse_dates=["time"]).set_index("time")

    wf = walk_forward_v035(features, events, cfg, anchor_wf)
    wf.to_csv(out / "walk_forward_v035.csv")
    indep = v032.independent_rows(wf)
    indep.to_csv(out / "episode_independent_v035.csv")

    qt = features.dropna(subset=["price"]).index.max()
    current_pred = regime_predict_v035(features, events, cfg, qt)
    current = {
        "engine": "BTC_HISTORICAL_REGIME_OUTCOME_V0_3_5",
        "schema_version": "0.3.5",
        "architecture": "REGIME_SPECIFIC_SUBSPACES_PLUS_HISTORICAL_OUTCOMES",
        "query_date": qt.date().isoformat(),
        "stage1_regime_similarity": current_pred,
        "stage2_historical_outcomes": current_pred.get("historical_outcomes_of_dominant_regime", {}),
        "master_integration": "FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL",
    }
    (out / "current_v035.json").write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")

    validation = validate_v035(indep, v034_wf, cfg)
    final = {
        "engine": "BTC_HISTORICAL_REGIME_OUTCOME_V0_3_5",
        "schema_version": "0.3.5",
        "architecture": "REGIME_SPECIFIC_SUBSPACES_PLUS_HISTORICAL_OUTCOMES",
        "design_freeze": {
            "regime_subspaces": "bottom/top emphasize location-state; ignition emphasizes short path/turn; uptrend emphasizes medium-long path",
            "score": "45% absolute similarity + 55% within-regime normalized similarity, then support/diversity quality adjustment",
            "direction": "secondary layer maps dominant regime to UP/DOWN only when top-regime margin is sufficient",
            "validation": "regime usefulness is judged separately from future-direction prediction using class-balanced regime recalls and naive-frequency baselines",
            "anti_overfit": "no acceptance gate lowering, no current-outcome fitting, same PIT independent episodes",
            "note": "The goal is to maximize validated discrimination and calibration, not inflate displayed scores. MASTER BTC TREND remains untouched.",
        },
        "validation": validation,
    }
    (out / "v035_validation_summary.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(final, ensure_ascii=False))


if __name__ == "__main__":
    main_v035()
