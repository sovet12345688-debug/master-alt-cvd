from __future__ import annotations

import json
import numpy as np
import pandas as pd

import run_v26_v032 as v032

v03 = v032.v03
v02 = v032.v02
core = v032.core
D = v032.D


def _class_balanced_episode_pool(features: pd.DataFrame, events: pd.DataFrame, cfg: dict, qt: pd.Timestamp) -> dict:
    """Build one globally unique representative per historical episode, then split by outcome class.

    Candidate dates are already cut at qt-365d by similarity_scores_fast, so their 365d outcomes
    are known at qt. Episode IDs are point-in-time price-path labels and do not use future data.
    """
    pf = v02.price_only_frame(features)
    pcfg = v02.price_only_cfg(cfg)
    scores = v02.similarity_scores_fast(pf.loc[:qt], qt, pcfg, required_horizon=365)
    if scores.empty:
        return {D[0]: pd.DataFrame(), D[1]: pd.DataFrame(), "all": pd.DataFrame()}

    eps = v02.episode_ids(
        pf.loc[:qt, "price"].dropna(),
        float(cfg.get("episode_reversal_pct", 0.20)),
        int(cfg.get("episode_max_days", 365)),
    )
    x = scores.copy()
    x["episode_id"] = eps.reindex(x.index)
    x["outcome"] = events.reindex(x.index)["fp_up30_vs_dn20"]
    x = x[x["episode_id"].notna() & x["outcome"].isin(D)].copy()
    if x.empty:
        return {D[0]: pd.DataFrame(), D[1]: pd.DataFrame(), "all": pd.DataFrame()}

    # Critical anti-duplication rule: one episode can represent only one class, using its single
    # most-similar historical anchor. This prevents the same broad regime from appearing on both sides.
    x = x.sort_values("similarity", ascending=False)
    reps = x.groupby("episode_id", sort=False).head(1).sort_values("similarity", ascending=False)
    return {
        D[0]: reps[reps["outcome"] == D[0]].copy(),
        D[1]: reps[reps["outcome"] == D[1]].copy(),
        "all": reps,
    }


def _class_metrics(frame: pd.DataFrame) -> dict:
    s = pd.to_numeric(frame["similarity"], errors="coerce").dropna().sort_values(ascending=False)
    if s.empty:
        return {"count": 0, "mean": None, "median": None, "top1": None, "score": None}
    mean = float(s.mean())
    median = float(s.median())
    top1 = float(s.iloc[0])
    # Predeclared robust aggregate: broad similarity matters more than one best match.
    score = 0.50 * mean + 0.30 * median + 0.20 * top1
    return {
        "count": int(len(s)),
        "mean": round(mean, 2),
        "median": round(median, 2),
        "top1": round(top1, 2),
        "score": round(score, 2),
    }


def class_balanced_predict(features: pd.DataFrame, events: pd.DataFrame, cfg: dict, qt: pd.Timestamp) -> dict:
    c = cfg.get("v033", {})
    k_target = int(c.get("balanced_per_class_k", 5))
    min_k = int(c.get("min_per_class", k_target))
    min_gap = float(c.get("min_class_score_gap", 0.75))
    min_pair_wins = int(c.get("min_pair_wins", 3))
    min_metric_wins = int(c.get("min_metric_wins", 2))

    pool = _class_balanced_episode_pool(features, events, cfg, qt)
    available_up, available_dn = len(pool[D[0]]), len(pool[D[1]])
    k = min(k_target, available_up, available_dn)
    if k < min_k:
        return {
            "pred": "ABSTAIN",
            "reason": "INSUFFICIENT_BALANCED_CLASS_EPISODES",
            "query_date": qt.date().isoformat(),
            "available": {"up": int(available_up), "down": int(available_dn)},
            "k_each": int(k),
            "confidence_score": 0,
            "confidence_grade": "LOW",
        }

    up = pool[D[0]].head(k).copy()
    dn = pool[D[1]].head(k).copy()
    um, dm = _class_metrics(up), _class_metrics(dn)
    gap = float(um["score"] - dm["score"])
    winner = D[0] if gap > 0 else D[1]

    us = pd.to_numeric(up["similarity"], errors="coerce").to_numpy(dtype=float)
    ds = pd.to_numeric(dn["similarity"], errors="coerce").to_numpy(dtype=float)
    up_pair_wins = int(np.sum(us > ds))
    dn_pair_wins = int(np.sum(ds > us))
    pair_wins = up_pair_wins if winner == D[0] else dn_pair_wins

    metric_pairs = [(um["mean"], dm["mean"]), (um["median"], dm["median"]), (um["top1"], dm["top1"])]
    up_metric_wins = sum(float(a) > float(b) for a, b in metric_pairs)
    dn_metric_wins = sum(float(b) > float(a) for a, b in metric_pairs)
    metric_wins = up_metric_wins if winner == D[0] else dn_metric_wins

    directional = abs(gap) >= min_gap and pair_wins >= min_pair_wins and metric_wins >= min_metric_wins
    pred = winner if directional else "ABSTAIN"

    all_dates = pd.concat([up, dn]).sort_index()
    distinct_years = int(len(set(all_dates.index.year)))
    episodes = int(all_dates["episode_id"].nunique())
    median_all = float(pd.to_numeric(all_dates["similarity"], errors="coerce").median())

    # Confidence is intentionally based on head-to-head separation, not class counts.
    abs_gap = abs(gap)
    gap_component = np.clip(abs_gap / 5.0, 0.0, 1.0)
    pair_component = np.clip((pair_wins - k / 2.0) / max(k / 2.0, 1.0), 0.0, 1.0)
    metric_component = metric_wins / 3.0
    diversity_component = min(1.0, distinct_years / 8.0)
    similarity_component = np.clip((median_all - 65.0) / 25.0, 0.0, 1.0)
    confidence_score = int(round(100.0 * (
        0.40 * gap_component
        + 0.25 * pair_component
        + 0.15 * metric_component
        + 0.10 * diversity_component
        + 0.10 * similarity_component
    )))

    conf_cfg = c.get("confidence_grade", {})
    med_gap = float(conf_cfg.get("medium_min_gap", 1.5))
    high_gap = float(conf_cfg.get("high_min_gap", 3.0))
    med_wins = int(conf_cfg.get("medium_min_pair_wins", 4))
    high_wins = int(conf_cfg.get("high_min_pair_wins", 5))
    med_score = int(conf_cfg.get("medium_min_score", 55))
    high_score = int(conf_cfg.get("high_min_score", 75))
    high_years = int(conf_cfg.get("high_min_distinct_years", 6))

    high = bool(pred in D and abs_gap >= high_gap and pair_wins >= high_wins and confidence_score >= high_score and distinct_years >= high_years)
    medium = bool(pred in D and abs_gap >= med_gap and pair_wins >= med_wins and confidence_score >= med_score)
    grade = "HIGH" if high else ("MEDIUM" if medium else "LOW")

    def analog_rows(frame: pd.DataFrame) -> list[dict]:
        return [
            {
                "date": t.date().isoformat(),
                "similarity": round(float(r["similarity"]), 2),
                "episode_id": int(r["episode_id"]),
            }
            for t, r in frame.iterrows()
        ]

    return {
        "pred": pred,
        "query_date": qt.date().isoformat(),
        "k_each": int(k),
        "class_similarity": {
            "up": um,
            "down": dm,
            "gap_up_minus_down": round(gap, 2),
        },
        "head_to_head": {
            "up_pair_wins": up_pair_wins,
            "down_pair_wins": dn_pair_wins,
            "up_metric_wins": int(up_metric_wins),
            "down_metric_wins": int(dn_metric_wins),
            "required_pair_wins": min_pair_wins,
            "required_metric_wins": min_metric_wins,
            "required_abs_score_gap": min_gap,
        },
        "available_episode_representatives": {"up": int(available_up), "down": int(available_dn)},
        "distinct_years": distinct_years,
        "episode_count": episodes,
        "median_similarity_all": round(median_all, 2),
        "confidence_score": confidence_score,
        "confidence_grade": grade,
        "up_fractals": analog_rows(up),
        "down_counter_fractals": analog_rows(dn),
        "warning": "Class similarity scores are historical-pattern evidence, not calibrated probabilities.",
    }


def walk_forward_v033(features: pd.DataFrame, events: pd.DataFrame, cfg: dict, anchor_wf: pd.DataFrame) -> pd.DataFrame:
    eps = v02.episode_ids(
        features["price"].dropna(),
        float(cfg.get("episode_reversal_pct", 0.20)),
        int(cfg.get("episode_max_days", 365)),
    )
    rows = []
    for qt, base in anchor_wf.iterrows():
        actual = base.get("actual")
        try:
            p = class_balanced_predict(features, events, cfg, qt)
        except Exception as e:
            rows.append({"time": qt, "actual": actual, "error": str(e)})
            continue
        pred = p.get("pred")
        cs = p.get("class_similarity", {})
        hh = p.get("head_to_head", {})
        rows.append({
            "time": qt,
            "pred": pred,
            "actual": actual,
            "correct": pred == actual if pred in D and actual in D else np.nan,
            "confidence": p.get("confidence_score"),
            "confidence_grade": p.get("confidence_grade"),
            "up_similarity_score": cs.get("up", {}).get("score"),
            "down_similarity_score": cs.get("down", {}).get("score"),
            "similarity_gap_up_minus_down": cs.get("gap_up_minus_down"),
            "up_pair_wins": hh.get("up_pair_wins"),
            "down_pair_wins": hh.get("down_pair_wins"),
            "k_each": p.get("k_each"),
            "test_episode_id": int(eps.loc[qt]) if qt in eps.index and pd.notna(eps.loc[qt]) else np.nan,
        })
    return pd.DataFrame(rows).set_index("time") if rows else pd.DataFrame()


def confidence_audit(indep: pd.DataFrame, cfg: dict) -> dict:
    ev = indep[indep["pred"].isin(D) & indep["correct"].notna()].copy()
    overall = v032.acc(ev["actual"], ev["pred"]) if len(ev) else None
    by = {}
    for g in ("LOW", "MEDIUM", "HIGH"):
        z = ev[ev["confidence_grade"] == g]
        by[g] = {
            "count": int(len(z)),
            "accuracy": v032.acc(z["actual"], z["pred"]) if len(z) else None,
            "coverage_pct": round(len(z) / len(ev) * 100, 2) if len(ev) else None,
            "prediction_mix": v032.pred_mix(z["pred"]) if len(z) else {},
        }
    r = cfg.get("v033", {}).get("confidence_acceptance", {})
    h, m, l = by["HIGH"], by["MEDIUM"], by["LOW"]
    gain = None if overall is None or h["accuracy"] is None else round(h["accuracy"] - overall, 2)
    high_gate = bool(
        h["count"] >= int(r.get("min_high_cases", 5))
        and gain is not None
        and gain >= float(r.get("min_high_gain_pp", 7.0))
        and (h["coverage_pct"] or 100.0) <= float(r.get("max_high_coverage_pct", 30.0))
    )
    tol = float(r.get("monotonic_tolerance_pp", 3.0))
    monotonic = True
    if h["accuracy"] is not None and m["accuracy"] is not None:
        monotonic &= h["accuracy"] + tol >= m["accuracy"]
    if m["accuracy"] is not None and l["accuracy"] is not None:
        monotonic &= m["accuracy"] + tol >= l["accuracy"]
    return {
        "pass": bool(high_gate and monotonic and h["count"] < len(ev)),
        "overall_accuracy": overall,
        "by_grade": by,
        "high_accuracy_gain_pp": gain,
        "gates": {
            "high_gate": high_gate,
            "monotonic_gate": bool(monotonic),
            "nondegenerate_gate": h["count"] < len(ev),
        },
    }


def validate_v033(indep: pd.DataFrame, old_v032: pd.DataFrame, cfg: dict) -> dict:
    cand = v032.stats(indep, "pred")
    old_aligned = indep[["actual"]].join(old_v032[["pred"]].rename(columns={"pred": "old_pred"}), how="left")
    old = v032.stats(old_aligned, "old_pred")
    always_up = v032.stats(indep.assign(always_up=D[0]), "always_up")

    rg = cfg.get("v033", {}).get("core_acceptance", {})
    bg = None if cand.get("balanced_accuracy") is None or old.get("balanced_accuracy") is None else round(cand["balanced_accuracy"] - old["balanced_accuracy"], 2)
    ag = None if cand.get("accuracy") is None or always_up.get("accuracy") is None else round(cand["accuracy"] - always_up["accuracy"], 2)
    rr = cand.get("class_recalls", {})
    mx = cand.get("prediction_mix", {})
    minority = min(float(mx.get("up_pct") or 0), float(mx.get("down_pct") or 0))
    gates = {
        "independent_episode_gate": len(indep) >= int(rg.get("min_independent_episodes", 30)),
        "coverage_gate": float(cand.get("coverage_pct") or 0) >= float(rg.get("min_coverage_pct", 50.0)),
        "balanced_accuracy_gate": cand.get("balanced_accuracy") is not None and cand["balanced_accuracy"] >= float(rg.get("min_balanced_accuracy", 55.0)),
        "balanced_gain_vs_v032_gate": bg is not None and bg >= float(rg.get("min_balanced_gain_vs_v032_pp", 3.0)),
        "accuracy_vs_always_up_gate": ag is not None and ag >= float(rg.get("min_accuracy_gap_vs_always_up_pp", -2.0)),
        "both_class_recall_gate": all(rr.get(x) is not None and rr[x] >= float(rg.get("min_each_class_recall_pct", 35.0)) for x in D),
        "nondegenerate_prediction_gate": minority >= float(rg.get("min_minority_prediction_pct", 25.0)),
    }
    core_pass = bool(all(gates.values()))
    conf = confidence_audit(indep, cfg)
    if core_pass and conf["pass"]:
        stage, nxt = "PASS", "RETEST_PRICE_CONDITIONAL_ONCHAIN_MACRO_CONFIRMATION"
    elif core_pass:
        stage, nxt = "PARTIAL_CORE_ONLY", "CALIBRATE_CONFIDENCE_BEFORE_CONFIRMATION_RETEST"
    else:
        stage, nxt = "FAIL", "REVISE_CLASS_BALANCED_PRICE_CORE"
    return {
        "independent_episode_rows": int(len(indep)),
        "candidate": cand,
        "v032_same_episode_benchmark": old,
        "always_up_same_episode_benchmark": always_up,
        "candidate_balanced_gain_vs_v032_pp": bg,
        "candidate_accuracy_gap_vs_always_up_pp": ag,
        "core_gates": gates,
        "core_pass": core_pass,
        "confidence_calibration": conf,
        "v033_stage_gate": stage,
        "next_step": nxt,
        "master_readiness": "NOT_READY",
        "master_integration": "FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL",
    }


def main_v033() -> None:
    # Rebuild underlying point-in-time data and V0.3.2 benchmark on the same run.
    v032.main_v032()
    cfg, out, data = core.load_config(), core.OUT, core.DATA
    features = pd.read_csv(data / "historical_features_daily.csv", parse_dates=["time"]).set_index("time")
    events = pd.read_csv(data / "event_registry.csv", parse_dates=["time"]).set_index("time")
    anchor_wf = pd.read_csv(out / "walk_forward_price_core.csv", parse_dates=["time"]).set_index("time")
    old_v032 = pd.read_csv(out / "walk_forward_v032.csv", parse_dates=["time"]).set_index("time")

    wf = walk_forward_v033(features, events, cfg, anchor_wf)
    wf.to_csv(out / "walk_forward_v033.csv")
    indep = v032.independent_rows(wf)
    indep.to_csv(out / "episode_independent_v033.csv")

    qt = features.dropna(subset=["price"]).index.max()
    current = {
        "engine": "BTC_HISTORICAL_REGIME_OUTCOME_V0_3_3",
        "schema_version": "0.3.3",
        "architecture": "CLASS_BALANCED_FRACTAL_VS_COUNTER_FRACTAL",
        "query_date": qt.date().isoformat(),
        "comparison": class_balanced_predict(features, events, cfg, qt),
        "master_integration": "FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL",
    }
    (out / "current_v033.json").write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")

    validation = validate_v033(indep, old_v032, cfg)
    final = {
        "engine": "BTC_HISTORICAL_REGIME_OUTCOME_V0_3_3",
        "schema_version": "0.3.3",
        "architecture": "CLASS_BALANCED_FRACTAL_VS_COUNTER_FRACTAL",
        "design_freeze": {
            "candidate_rule": "only history known by query_time; one representative per point-in-time episode globally",
            "class_balance": "equal top-K UP30_FIRST and DN20_FIRST episode representatives",
            "aggregate_similarity": "50% mean + 30% median + 20% top1 within each class",
            "decision": "absolute class-score gap + paired-rank wins + mean/median/top1 majority; otherwise ABSTAIN",
            "counter_fractal": "opposite class top-K always retained",
            "acceptance_basis": "first evaluated walk-forward anchor per independent point-in-time test episode",
            "note": "No MASTER BTC TREND score, gate, schedule, or live-plan code is modified.",
        },
        "validation": validation,
    }
    (out / "v033_validation_summary.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(final, ensure_ascii=False))


if __name__ == "__main__":
    main_v033()
