from __future__ import annotations

import json
import math
import numpy as np
import pandas as pd

import run_v26_v033 as v033

v032 = v033.v032
v02 = v033.v02
core = v033.core
D = v033.D

REGIMES = (
    "PANIC_BOTTOM",
    "BOTTOM",
    "UP_IGNITION",
    "UPTREND",
    "TOP",
    "DOWN_IGNITION",
)
BULLISH_REGIMES = ("PANIC_BOTTOM", "BOTTOM", "UP_IGNITION", "UPTREND")
BEARISH_REGIMES = ("TOP", "DOWN_IGNITION")

REGIME_KO = {
    "PANIC_BOTTOM": "공포·투매형",
    "BOTTOM": "바닥형",
    "UP_IGNITION": "상승초입형",
    "UPTREND": "상승 중후반형",
    "TOP": "고점형",
    "DOWN_IGNITION": "하락초입형",
}


def _s(frame: pd.DataFrame, col: str, default=np.nan) -> pd.Series:
    if col in frame.columns:
        return pd.to_numeric(frame[col], errors="coerce")
    return pd.Series(default, index=frame.index, dtype=float)


def build_regime_labels(features: pd.DataFrame, events: pd.DataFrame) -> pd.Series:
    """Historical archetype labels.

    Labels combine point-in-time state features with a forward outcome that is required to be fully
    known before a historical date can be used as an analog. In walk-forward use, candidate dates
    are already cut by 365 days, so these labels cannot leak future information into the query.
    """
    idx = features.index
    fp = events.reindex(idx).get("fp_up30_vs_dn20", pd.Series(index=idx, dtype=object))
    dd = _s(features, "dd_from_365d_high")
    reb = _s(features, "rebound_from_365d_low")
    r30 = _s(features, "ret_30d")
    r90 = _s(features, "ret_90d")
    d20 = _s(features, "dist_sma20")
    d200 = _s(features, "dist_sma200")
    slope50 = _s(features, "sma50_slope30")
    rsi = _s(features, "rsi14")

    out = pd.Series("UNLABELED", index=idx, dtype=object)
    up = fp == D[0]
    dn = fp == D[1]

    panic = up & (dd <= -0.30) & ((r30 <= -0.10) | (rsi <= 42) | (d200 <= -0.18))
    bottom = up & ~panic & (dd <= -0.18) & ((reb <= 0.45) | (d200 <= 0.02) | (r90 <= 0.0))
    ignition = up & ~panic & ~bottom & (r30.between(-0.08, 0.35, inclusive="both")) & (
        (slope50 >= -0.02) | (d20 >= -0.03) | (reb <= 0.90)
    )
    uptrend = up & ~panic & ~bottom & ~ignition

    top = dn & (dd >= -0.14) & ((r90 >= 0.12) | (rsi >= 60) | (d200 >= 0.10))
    down_ignition = dn & ~top

    out.loc[panic] = "PANIC_BOTTOM"
    out.loc[bottom] = "BOTTOM"
    out.loc[ignition] = "UP_IGNITION"
    out.loc[uptrend] = "UPTREND"
    out.loc[top] = "TOP"
    out.loc[down_ignition] = "DOWN_IGNITION"
    return out


def _episode_representatives(features: pd.DataFrame, events: pd.DataFrame, cfg: dict, qt: pd.Timestamp) -> pd.DataFrame:
    pf = v02.price_only_frame(features)
    pcfg = v02.price_only_cfg(cfg)
    scores = v02.similarity_scores_fast(pf.loc[:qt], qt, pcfg, required_horizon=365)
    if scores.empty:
        return pd.DataFrame()
    eps = v02.episode_ids(
        pf.loc[:qt, "price"].dropna(),
        float(cfg.get("episode_reversal_pct", 0.20)),
        int(cfg.get("episode_max_days", 365)),
    )
    labels = build_regime_labels(features.loc[:qt], events.loc[:qt] if not events.empty else events)
    x = scores.copy()
    x["episode_id"] = eps.reindex(x.index)
    x["regime"] = labels.reindex(x.index)
    x = x[x["episode_id"].notna() & x["regime"].isin(REGIMES)].copy()
    if x.empty:
        return x
    # one globally unique anchor per historical episode, chosen only by current-query similarity
    return x.sort_values("similarity", ascending=False).groupby("episode_id", sort=False).head(1)


def _safe_iqr(s: pd.Series) -> float:
    s = pd.to_numeric(s, errors="coerce").dropna()
    if len(s) < 4:
        return np.nan
    q1, q3 = float(s.quantile(0.25)), float(s.quantile(0.75))
    return max(q3 - q1, 1e-6)


def _enrichment_score(obs_share: float, base_share: float) -> tuple[float, float]:
    if base_share <= 0:
        return 50.0, 1.0
    ratio = max(obs_share / base_share, 1e-6)
    # 50 is neutral enrichment; log-ratio makes over/under-representation symmetric.
    score = 50.0 + 35.0 * math.tanh(math.log(ratio))
    return float(np.clip(score, 0, 100)), float(ratio)


def _regime_evidence(reps: pd.DataFrame, regime: str, cfg: dict) -> dict:
    c = cfg.get("v034", {})
    k = int(c.get("top_k_per_regime", 5))
    min_n = int(c.get("min_regime_representatives", 4))
    global_k = int(c.get("global_neighbor_k", 24))
    weights = c.get("score_weights", {"absolute": 0.40, "normalized": 0.40, "enrichment": 0.20})

    r = reps[reps["regime"] == regime].sort_values("similarity", ascending=False).copy()
    if len(r) < min_n:
        return {
            "regime": regime,
            "label_ko": REGIME_KO[regime],
            "score": None,
            "status": "INSUFFICIENT_HISTORY",
            "representatives": int(len(r)),
            "analogs": [],
        }
    top = r.head(min(k, len(r))).copy()
    sims = pd.to_numeric(r["similarity"], errors="coerce").dropna()
    ts = pd.to_numeric(top["similarity"], errors="coerce").dropna()
    raw = 0.50 * float(ts.mean()) + 0.30 * float(ts.median()) + 0.20 * float(ts.iloc[0])

    med = float(sims.median())
    iqr = _safe_iqr(sims)
    z = 0.0 if not np.isfinite(iqr) else (raw - med) / iqr
    # normalized within the class, so naturally tighter-looser regimes do not win on raw similarity alone
    normalized = float(np.clip(50.0 + 14.0 * z, 0, 100))
    absolute = float(np.clip((raw - 55.0) / 35.0 * 100.0, 0, 100))

    g = reps.sort_values("similarity", ascending=False).head(min(global_k, len(reps)))
    obs_share = float((g["regime"] == regime).mean()) if len(g) else 0.0
    base_share = float((reps["regime"] == regime).mean()) if len(reps) else 0.0
    enrichment, enrichment_ratio = _enrichment_score(obs_share, base_share)

    support = min(1.0, len(r) / max(float(c.get("full_support_representatives", 10)), 1.0))
    diversity = len(set(top.index.year))
    diversity_factor = min(1.0, diversity / max(float(c.get("full_support_years", 5)), 1.0))
    quality = 0.75 + 0.15 * support + 0.10 * diversity_factor
    base_score = (
        float(weights.get("absolute", 0.40)) * absolute
        + float(weights.get("normalized", 0.40)) * normalized
        + float(weights.get("enrichment", 0.20)) * enrichment
    )
    score = int(round(np.clip(base_score * quality, 0, 100)))

    analogs = [
        {
            "date": t.date().isoformat(),
            "similarity": round(float(row["similarity"]), 2),
            "episode_id": int(row["episode_id"]),
        }
        for t, row in top.iterrows()
    ]
    return {
        "regime": regime,
        "label_ko": REGIME_KO[regime],
        "score": score,
        "status": "OK",
        "representatives": int(len(r)),
        "distinct_years_top": int(diversity),
        "raw_top_similarity": round(raw, 2),
        "class_median_similarity": round(med, 2),
        "class_normalized_z": round(float(z), 3),
        "absolute_component": round(absolute, 2),
        "normalized_component": round(normalized, 2),
        "neighbor_enrichment_component": round(enrichment, 2),
        "neighbor_enrichment_ratio": round(enrichment_ratio, 3),
        "analogs": analogs,
    }


def _outcome_summary(events: pd.DataFrame, analog_dates: list[str], cfg: dict) -> dict:
    if not analog_dates:
        return {}
    idx = [pd.Timestamp(x, tz="UTC") if pd.Timestamp(x).tzinfo is None else pd.Timestamp(x) for x in analog_dates]
    ev = events.reindex(idx)
    out: dict[str, dict] = {}
    for h in [30, 90, 180, 365]:
        z: dict[str, object] = {"cases": int(ev[f"mfe_{h}d"].notna().sum()) if f"mfe_{h}d" in ev else 0}
        if f"mfe_{h}d" in ev:
            z["median_mfe_pct"] = round(float(pd.to_numeric(ev[f"mfe_{h}d"], errors="coerce").median()) * 100, 2)
        if f"mae_{h}d" in ev:
            z["median_mae_pct"] = round(float(pd.to_numeric(ev[f"mae_{h}d"], errors="coerce").median()) * 100, 2)
        for t in [30, 50, 100]:
            col = f"hit_up{t}_{h}d"
            if col in ev:
                z[f"up{t}_hit_pct"] = round(float(pd.to_numeric(ev[col], errors="coerce").notna().mean()) * 100, 1)
        for t in [20, 30, 50]:
            col = f"hit_dn{t}_{h}d"
            if col in ev:
                z[f"dn{t}_hit_pct"] = round(float(pd.to_numeric(ev[col], errors="coerce").notna().mean()) * 100, 1)
        out[f"{h}d"] = z
    fp = ev.get("fp_up30_vs_dn20", pd.Series(dtype=object))
    out["first_passage"] = {
        "up30_first": int((fp == D[0]).sum()),
        "dn20_first": int((fp == D[1]).sum()),
        "directional_cases": int(fp.isin(D).sum()),
    }
    return out


def regime_predict(features: pd.DataFrame, events: pd.DataFrame, cfg: dict, qt: pd.Timestamp) -> dict:
    reps = _episode_representatives(features, events, cfg, qt)
    if reps.empty:
        return {"pred": "ABSTAIN", "query_date": qt.date().isoformat(), "reason": "NO_ELIGIBLE_HISTORY"}

    evidence = {r: _regime_evidence(reps, r, cfg) for r in REGIMES}
    valid = [x for x in evidence.values() if x.get("score") is not None]
    valid.sort(key=lambda x: x["score"], reverse=True)
    if not valid:
        return {"pred": "ABSTAIN", "query_date": qt.date().isoformat(), "reason": "NO_VALID_REGIME_SCORE"}

    top1 = valid[0]
    top2 = valid[1] if len(valid) > 1 else None
    margin = float(top1["score"] - (top2["score"] if top2 else 0))

    def side_score(regimes: tuple[str, ...]) -> float:
        vals = sorted([float(evidence[r]["score"]) for r in regimes if evidence[r].get("score") is not None], reverse=True)
        if not vals:
            return 0.0
        if len(vals) == 1:
            return vals[0]
        return 0.70 * vals[0] + 0.30 * vals[1]

    bull = side_score(BULLISH_REGIMES)
    bear = side_score(BEARISH_REGIMES)
    side_gap = bull - bear
    min_gap = float(cfg.get("v034", {}).get("direction_min_score_gap", 4.0))
    pred = "ABSTAIN" if abs(side_gap) < min_gap else (D[0] if side_gap > 0 else D[1])
    # user-facing directional balance is evidence balance, not probability
    temp = max(float(cfg.get("v034", {}).get("direction_balance_temperature", 9.0)), 1e-6)
    long_share = 100.0 / (1.0 + math.exp(-(side_gap) / temp))

    conf_score = int(round(np.clip(
        0.50 * min(100.0, abs(side_gap) / 18.0 * 100.0)
        + 0.25 * min(100.0, margin / 15.0 * 100.0)
        + 0.15 * min(100.0, len(reps) / 50.0 * 100.0)
        + 0.10 * min(100.0, len(set(reps.index.year)) / 8.0 * 100.0),
        0,
        100,
    )))
    grade = "HIGH" if conf_score >= 75 and abs(side_gap) >= 10 and margin >= 5 else ("MEDIUM" if conf_score >= 55 and abs(side_gap) >= min_gap else "LOW")

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
        "regime_scores": {r: evidence[r] for r in REGIMES},
        "direction_evidence": {
            "bullish_score": round(bull, 2),
            "bearish_score": round(bear, 2),
            "gap_bull_minus_bear": round(side_gap, 2),
            "long_share": round(long_share, 1),
            "short_share": round(100.0 - long_share, 1),
            "warning": "LONG/SHORT shares are normalized evidence balance, not calibrated probabilities.",
        },
        "historical_outcomes_of_dominant_regime": _outcome_summary(events, analog_dates, cfg),
        "confidence_score": conf_score,
        "confidence_grade": grade,
        "eligible_episode_representatives": int(len(reps)),
        "distinct_years": int(len(set(reps.index.year))),
        "warning": "Regime scores measure historical-pattern similarity. Outcome rates describe selected historical cases and are not calibrated future probabilities.",
    }


def walk_forward_v034(features: pd.DataFrame, events: pd.DataFrame, cfg: dict, anchor_wf: pd.DataFrame) -> pd.DataFrame:
    labels = build_regime_labels(features, events)
    eps = v02.episode_ids(
        features["price"].dropna(),
        float(cfg.get("episode_reversal_pct", 0.20)),
        int(cfg.get("episode_max_days", 365)),
    )
    rows = []
    for qt, base in anchor_wf.iterrows():
        actual = base.get("actual")
        try:
            p = regime_predict(features, events, cfg, qt)
        except Exception as e:
            rows.append({"time": qt, "actual": actual, "error": str(e)})
            continue
        pred = p.get("pred")
        ranked = sorted(
            [(r, z.get("score")) for r, z in p.get("regime_scores", {}).items() if z.get("score") is not None],
            key=lambda x: x[1], reverse=True,
        )
        actual_regime = labels.get(qt, "UNLABELED")
        top2 = [x[0] for x in ranked[:2]]
        de = p.get("direction_evidence", {})
        rows.append({
            "time": qt,
            "pred": pred,
            "actual": actual,
            "correct": pred == actual if pred in D and actual in D else np.nan,
            "confidence": p.get("confidence_score"),
            "confidence_grade": p.get("confidence_grade"),
            "dominant_regime": p.get("dominant_regime"),
            "dominant_regime_score": p.get("dominant_regime_score"),
            "actual_regime": actual_regime,
            "regime_exact_correct": p.get("dominant_regime") == actual_regime if actual_regime in REGIMES else np.nan,
            "regime_top2_correct": actual_regime in top2 if actual_regime in REGIMES else np.nan,
            "bullish_score": de.get("bullish_score"),
            "bearish_score": de.get("bearish_score"),
            "long_share": de.get("long_share"),
            "test_episode_id": int(eps.loc[qt]) if qt in eps.index and pd.notna(eps.loc[qt]) else np.nan,
        })
    return pd.DataFrame(rows).set_index("time") if rows else pd.DataFrame()


def _regime_validation(indep: pd.DataFrame) -> dict:
    z = indep[indep["actual_regime"].isin(REGIMES)].copy()
    if z.empty:
        return {"rows": 0}
    exact = float(pd.to_numeric(z["regime_exact_correct"], errors="coerce").mean()) * 100
    top2 = float(pd.to_numeric(z["regime_top2_correct"], errors="coerce").mean()) * 100
    actual_counts = z["actual_regime"].value_counts().to_dict()
    pred_counts = z["dominant_regime"].value_counts().to_dict()
    per = {}
    for r in REGIMES:
        a = z[z["actual_regime"] == r]
        per[r] = {
            "actual_count": int(len(a)),
            "top1_recall_pct": None if not len(a) else round(float((a["dominant_regime"] == r).mean()) * 100, 2),
            "top2_recall_pct": None if not len(a) else round(float(a["regime_top2_correct"].mean()) * 100, 2),
        }
    return {
        "rows": int(len(z)),
        "exact_accuracy": round(exact, 2),
        "top2_accuracy": round(top2, 2),
        "actual_mix": {str(k): int(v) for k, v in actual_counts.items()},
        "predicted_mix": {str(k): int(v) for k, v in pred_counts.items()},
        "per_regime": per,
    }


def _confidence_audit(indep: pd.DataFrame) -> dict:
    ev = indep[indep["pred"].isin(D) & indep["correct"].notna()].copy()
    overall = v032.acc(ev["actual"], ev["pred"]) if len(ev) else None
    by = {}
    for g in ("LOW", "MEDIUM", "HIGH"):
        z = ev[ev["confidence_grade"] == g]
        by[g] = {
            "count": int(len(z)),
            "accuracy": v032.acc(z["actual"], z["pred"]) if len(z) else None,
            "balanced_accuracy": v032.bal_acc(z["actual"], z["pred"]) if len(z) else None,
        }
    return {"overall_accuracy": overall, "by_grade": by}


def validate_v034(indep: pd.DataFrame, v033_wf: pd.DataFrame, cfg: dict) -> dict:
    cand = v032.stats(indep, "pred")
    old_aligned = indep[["actual"]].join(v033_wf[["pred"]].rename(columns={"pred": "v033_pred"}), how="left")
    old = v032.stats(old_aligned, "v033_pred")
    always_up = v032.stats(indep.assign(always_up=D[0]), "always_up")
    regime = _regime_validation(indep)
    conf = _confidence_audit(indep)

    c = cfg.get("v034", {}).get("acceptance", {})
    rr = cand.get("class_recalls", {})
    mx = cand.get("prediction_mix", {})
    minority = min(float(mx.get("up_pct") or 0), float(mx.get("down_pct") or 0))
    bg = None if cand.get("balanced_accuracy") is None or old.get("balanced_accuracy") is None else round(cand["balanced_accuracy"] - old["balanced_accuracy"], 2)
    gates = {
        "independent_episode_gate": len(indep) >= int(c.get("min_independent_episodes", 30)),
        "coverage_gate": float(cand.get("coverage_pct") or 0) >= float(c.get("min_direction_coverage_pct", 50)),
        "balanced_accuracy_gate": cand.get("balanced_accuracy") is not None and cand["balanced_accuracy"] >= float(c.get("min_balanced_accuracy", 55)),
        "both_class_recall_gate": all(rr.get(x) is not None and rr[x] >= float(c.get("min_each_class_recall_pct", 35)) for x in D),
        "nondegenerate_prediction_gate": minority >= float(c.get("min_minority_prediction_pct", 20)),
        "regime_exact_gate": regime.get("exact_accuracy") is not None and regime.get("exact_accuracy", 0) >= float(c.get("min_regime_exact_accuracy", 24)),
        "regime_top2_gate": regime.get("top2_accuracy") is not None and regime.get("top2_accuracy", 0) >= float(c.get("min_regime_top2_accuracy", 45)),
    }
    stage = "PASS" if all(gates.values()) else "FAIL"
    return {
        "independent_episode_rows": int(len(indep)),
        "direction_candidate": cand,
        "v033_same_episode_benchmark": old,
        "always_up_same_episode_benchmark": always_up,
        "balanced_gain_vs_v033_pp": bg,
        "regime_classification": regime,
        "confidence_calibration": conf,
        "acceptance_gates": gates,
        "v034_stage_gate": stage,
        "next_step": "RETEST_CONFIRMATION_LAYER" if stage == "PASS" else "IMPROVE_REGIME_CORE_WITHOUT_LOWERING_GATES",
        "master_readiness": "NOT_READY",
        "master_integration": "FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL",
    }


def main_v034() -> None:
    # Rebuild the same PIT history and prior benchmark first; this keeps comparison conditions aligned.
    v033.main_v033()
    cfg, out, data = core.load_config(), core.OUT, core.DATA
    features = pd.read_csv(data / "historical_features_daily.csv", parse_dates=["time"]).set_index("time")
    events = pd.read_csv(data / "event_registry.csv", parse_dates=["time"]).set_index("time")
    anchor_wf = pd.read_csv(out / "walk_forward_price_core.csv", parse_dates=["time"]).set_index("time")
    v033_wf = pd.read_csv(out / "walk_forward_v033.csv", parse_dates=["time"]).set_index("time")

    wf = walk_forward_v034(features, events, cfg, anchor_wf)
    wf.to_csv(out / "walk_forward_v034.csv")
    indep = v032.independent_rows(wf)
    indep.to_csv(out / "episode_independent_v034.csv")

    labels = build_regime_labels(features, events)
    pd.DataFrame({"regime": labels}).to_csv(out / "historical_regime_labels_v034.csv")

    qt = features.dropna(subset=["price"]).index.max()
    current_pred = regime_predict(features, events, cfg, qt)
    current = {
        "engine": "BTC_HISTORICAL_REGIME_OUTCOME_V0_3_4",
        "schema_version": "0.3.4",
        "architecture": "TWO_STAGE_REGIME_SIMILARITY_PLUS_HISTORICAL_OUTCOMES",
        "query_date": qt.date().isoformat(),
        "stage1_regime_similarity": current_pred,
        "stage2_historical_outcomes": current_pred.get("historical_outcomes_of_dominant_regime", {}),
        "master_integration": "FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL",
    }
    (out / "current_v034.json").write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")

    validation = validate_v034(indep, v033_wf, cfg)
    final = {
        "engine": "BTC_HISTORICAL_REGIME_OUTCOME_V0_3_4",
        "schema_version": "0.3.4",
        "architecture": "TWO_STAGE_REGIME_SIMILARITY_PLUS_HISTORICAL_OUTCOMES",
        "design_freeze": {
            "stage1": "six purpose-aligned historical regimes: panic/bottom/up-ignition/uptrend/top/down-ignition",
            "score": "absolute similarity + within-regime normalization + nearest-neighbor enrichment, with support/diversity quality adjustment",
            "stage2": "forward historical outcomes are reported separately for the dominant regime's selected analog episodes",
            "direction": "bullish-regime evidence versus bearish-regime evidence; ABSTAIN allowed",
            "validation": "same independent point-in-time episodes; no gate lowering; balanced accuracy and both directional recalls remain mandatory",
            "anti_overfit": "no tuning against current outcome, no future leakage, N/A never zero-filled, one representative per historical episode",
            "note": "Score maximization means maximizing discrimination/robustness, not inflating displayed numbers. MASTER BTC TREND remains untouched.",
        },
        "validation": validation,
    }
    (out / "v034_validation_summary.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(final, ensure_ascii=False))


if __name__ == "__main__":
    main_v034()
