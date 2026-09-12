from __future__ import annotations

import json
import numpy as np
import pandas as pd

import run_v26_v03 as v03

v02 = v03.v02
core = v03.core
D = ("UP30_FIRST", "DN20_FIRST")


def acc(y, p):
    m = y.isin(D) & p.isin(D)
    return None if not m.any() else round(float((y[m] == p[m]).mean()) * 100, 2)


def bal_acc(y, p):
    m = y.isin(D) & p.isin(D)
    if not m.any():
        return None
    vals = []
    for label in D:
        z = m & (y == label)
        if z.any():
            vals.append(float((p[z] == label).mean()))
    return None if len(vals) < 2 else round(float(np.mean(vals)) * 100, 2)


def recalls(y, p):
    out = {}
    for label in D:
        z = y == label
        out[label] = None if not z.any() else round(float((p[z] == label).mean()) * 100, 2)
    return out


def pred_mix(p):
    z = p[p.isin(D)]
    if z.empty:
        return {"directional": 0, "up_pct": None, "down_pct": None}
    return {
        "directional": int(len(z)),
        "up_pct": round(float((z == D[0]).mean()) * 100, 2),
        "down_pct": round(float((z == D[1]).mean()) * 100, 2),
    }


def episode_prior(features, events, cfg, qt):
    cutoff = qt - pd.Timedelta(days=max(cfg["outcome_horizons_days"]))
    price = features.loc[:cutoff, "price"].dropna()
    if price.empty:
        return {D[0]: 0.5, D[1]: 0.5, "episodes": 0}
    eps = v02.episode_ids(price, float(cfg.get("episode_reversal_pct", 0.2)), int(cfg.get("episode_max_days", 365)))
    x = pd.DataFrame({"episode_id": eps}).join(events[["fp_up30_vs_dn20"]], how="left")
    x = x[x["fp_up30_vs_dn20"].isin(D)]
    if x.empty:
        return {D[0]: 0.5, D[1]: 0.5, "episodes": 0}
    rep = x.groupby("episode_id", sort=False).tail(1)
    c = rep["fp_up30_vs_dn20"].value_counts()
    up, dn = int(c.get(D[0], 0)), int(c.get(D[1], 0))
    n = up + dn
    return {D[0]: (up + 0.5) / (n + 1), D[1]: (dn + 0.5) / (n + 1), "episodes": n}


def vote(analogs, events, prior, cfg):
    if analogs.empty:
        return {"pred": None, "support": {}, "confidence_score": 0, "confidence_grade": "LOW"}
    labels = events.reindex(analogs.index)["fp_up30_vs_dn20"]
    m = labels.isin(D)
    a, labels = analogs.loc[m].copy(), labels.loc[m]
    if a.empty:
        return {"pred": None, "support": {}, "confidence_score": 0, "confidence_grade": "LOW"}

    c = cfg.get("v032", {})
    alpha = float(c.get("class_prior_exponent", 0.5))
    power = float(c.get("similarity_weight_power", 2.0))
    abstain = float(c.get("abstain_support_margin", 0.10))
    w = np.power(pd.to_numeric(a["similarity"], errors="coerce").clip(0, 100) / 100.0, power)
    raw, adj, mean_sim = {}, {}, {}
    for label in D:
        q = labels == label
        raw[label] = float(w[q].sum())
        adj[label] = raw[label] / max(float(prior.get(label, 0.5)), 1e-6) ** alpha
        mean_sim[label] = float(a.loc[q, "similarity"].mean()) if q.any() else None
    total = adj[D[0]] + adj[D[1]]
    if total <= 0:
        return {"pred": None, "support": {}, "confidence_score": 0, "confidence_grade": "LOW"}
    up = adj[D[0]] / total
    dn = adj[D[1]] / total
    margin = abs(up - dn)
    pred = "ABSTAIN" if margin < abstain else (D[0] if up > dn else D[1])
    opp = D[1] if pred == D[0] else D[0]
    gap = None if pred not in D or mean_sim.get(pred) is None or mean_sim.get(opp) is None else float(mean_sim[pred] - mean_sim[opp])
    years = len(set(a.index.year))
    episodes = int(a["episode_id"].nunique()) if "episode_id" in a else int(len(a))
    medsim = float(pd.to_numeric(a["similarity"], errors="coerce").median())
    maj = max(up, dn)
    score = int(round(100 * (
        0.45 * np.clip((maj - 0.5) / 0.5, 0, 1)
        + 0.20 * np.clip((abs(gap) if gap is not None else 0.0) / 8.0, 0, 1)
        + 0.15 * min(1.0, years / 8.0)
        + 0.10 * min(1.0, len(a) / 12.0)
        + 0.10 * np.clip((medsim - 65.0) / 25.0, 0, 1)
    )))
    high = pred in D and score >= 75 and maj >= 0.72 and gap is not None and gap >= 1.0 and len(a) >= 10 and years >= 5 and episodes >= 8
    medium = pred in D and score >= 55 and maj >= 0.60 and len(a) >= 8 and years >= 4
    return {
        "pred": pred,
        "support": {"up_pct": round(up * 100, 2), "down_pct": round(dn * 100, 2), "margin_pct": round(margin * 100, 2)},
        "training_prior": {"up_pct": round(float(prior[D[0]]) * 100, 2), "down_pct": round(float(prior[D[1]]) * 100, 2), "episodes": int(prior.get("episodes", 0))},
        "counter_similarity_gap": None if gap is None else round(gap, 2),
        "directional_case_count": int(len(a)),
        "distinct_years": int(years),
        "episode_count": int(episodes),
        "median_similarity": round(medsim, 2),
        "confidence_score": score,
        "confidence_grade": "HIGH" if high else ("MEDIUM" if medium else "LOW"),
    }


def price_predict(features, events, cfg, qt):
    pf = v02.price_only_frame(features)
    pcfg = v02.price_only_cfg(cfg)
    scores = v02.similarity_scores_fast(pf.loc[:qt], qt, pcfg, required_horizon=365)
    analogs = v02.select_episode_analogs(scores, pf.loc[:qt], pcfg)
    out = vote(analogs, events, episode_prior(features, events, cfg, qt), cfg)
    out["query_date"] = qt.date().isoformat()
    out["analog_dates"] = [t.date().isoformat() for t in analogs.index]
    return out


def domain_predict(features, events, cfg, qt, domain):
    pf = v02.price_only_frame(features)
    pcfg = v02.price_only_cfg(cfg)
    pool_cfg = json.loads(json.dumps(pcfg))
    pool_cfg["analog_top_k"] = int(cfg.get("v032", {}).get("conditional_price_pool_k", 36))
    ps = v02.similarity_scores_fast(pf.loc[:qt], qt, pool_cfg, required_horizon=365)
    pool = v02.select_episode_analogs(ps, pf.loc[:qt], pool_cfg)
    if pool.empty:
        return {"pred": None, "usable": False}
    dcfg = v03.standalone_domain_cfg(cfg, domain)
    ds = v02.similarity_scores_fast(features.loc[:qt], qt, dcfg, required_horizon=365)
    common = pool.index.intersection(ds.index)
    if len(common) < int(cfg.get("v032", {}).get("conditional_min_pool", 12)):
        return {"pred": None, "usable": False}
    r = ds.loc[common].sort_values("similarity", ascending=False).head(int(cfg["analog_top_k"])).copy()
    r["episode_id"] = pool.reindex(r.index)["episode_id"]
    out = vote(r, events, episode_prior(features, events, cfg, qt), cfg)
    out["usable"] = bool(
        out.get("pred") in D
        and int(out.get("directional_case_count") or 0) >= 8
        and float(out.get("support", {}).get("margin_pct") or 0) >= 15.0
        and out.get("confidence_grade") in ("MEDIUM", "HIGH")
    )
    return out


def walk_forward(features, events, cfg, old_wf):
    eps = v02.episode_ids(features["price"].dropna(), float(cfg.get("episode_reversal_pct", 0.2)), int(cfg.get("episode_max_days", 365)))
    rows = []
    for qt, old in old_wf.iterrows():
        actual = old.get("actual")
        try:
            p = price_predict(features, events, cfg, qt)
            on = domain_predict(features, events, cfg, qt, "onchain")
            ma = domain_predict(features, events, cfg, qt, "macro")
        except Exception as e:
            rows.append({"time": qt, "actual": actual, "error": str(e)})
            continue
        pred = p.get("pred")
        status = "BASE_ABSTAIN" if pred not in D else "NO_CONFIRMATION"
        agree, conflict = [], []
        if pred in D:
            for name, z in (("onchain", on), ("macro", ma)):
                if z.get("usable") and z.get("pred") in D:
                    (agree if z.get("pred") == pred else conflict).append(name)
            if agree and conflict:
                status = "MIXED_CONFLICT"
            elif conflict:
                status = "CONFLICT"
            elif len(agree) == 2:
                status = "CONFIRMED_BOTH"
            elif len(agree) == 1:
                status = "CONFIRMED_ONE"
        rows.append({
            "time": qt, "pred": pred, "actual": actual,
            "correct": pred == actual if pred in D and actual in D else np.nan,
            "confidence": p.get("confidence_score"), "confidence_grade": p.get("confidence_grade"),
            "support_up_pct": p.get("support", {}).get("up_pct"), "support_down_pct": p.get("support", {}).get("down_pct"),
            "support_margin_pct": p.get("support", {}).get("margin_pct"), "counter_similarity_gap": p.get("counter_similarity_gap"),
            "training_prior_up_pct": p.get("training_prior", {}).get("up_pct"), "training_prior_down_pct": p.get("training_prior", {}).get("down_pct"),
            "test_episode_id": int(eps.loc[qt]) if qt in eps.index and pd.notna(eps.loc[qt]) else np.nan,
            "confirmation_status": status,
            "onchain_pred": on.get("pred"), "onchain_usable": bool(on.get("usable")),
            "macro_pred": ma.get("pred"), "macro_usable": bool(ma.get("usable")),
            "old_price_core_pred": old.get("pred"), "old_price_core_correct": old.get("correct"),
        })
    return pd.DataFrame(rows).set_index("time") if rows else pd.DataFrame()


def independent_rows(wf):
    if wf.empty:
        return wf
    x = wf.reset_index().sort_values("time")
    x = x[x["actual"].isin(D) & x["test_episode_id"].notna()]
    return x.groupby("test_episode_id", as_index=False).first().set_index("time")


def stats(frame, col):
    if frame.empty:
        return {}
    y, p = frame["actual"], frame[col]
    return {
        "rows": int(len(frame)), "coverage_pct": round(float(p.isin(D).mean()) * 100, 2),
        "accuracy": acc(y, p), "balanced_accuracy": bal_acc(y, p),
        "class_recalls": recalls(y, p), "prediction_mix": pred_mix(p),
    }


def naive_prior_pred(frame):
    return pd.Series([D[0] if float(r.get("training_prior_up_pct") or 50) >= float(r.get("training_prior_down_pct") or 50) else D[1] for _, r in frame.iterrows()], index=frame.index)


def confidence_audit(indep, cfg):
    ev = indep[indep["pred"].isin(D) & indep["correct"].notna()]
    overall = acc(ev["actual"], ev["pred"]) if len(ev) else None
    by = {}
    for g in ("LOW", "MEDIUM", "HIGH"):
        z = ev[ev["confidence_grade"] == g]
        by[g] = {"count": int(len(z)), "accuracy": acc(z["actual"], z["pred"]) if len(z) else None, "coverage_pct": round(len(z) / len(ev) * 100, 2) if len(ev) else None, "prediction_mix": pred_mix(z["pred"]) if len(z) else {}}
    r = cfg.get("v032", {}).get("confidence_acceptance", {})
    h, m, l = by["HIGH"], by["MEDIUM"], by["LOW"]
    gain = None if overall is None or h["accuracy"] is None else round(h["accuracy"] - overall, 2)
    hg = bool(h["count"] >= int(r.get("min_high_cases", 8)) and gain is not None and gain >= float(r.get("min_high_gain_pp", 7)) and (h["coverage_pct"] or 100) <= float(r.get("max_high_coverage_pct", 35)))
    tol = float(r.get("monotonic_tolerance_pp", 3))
    mono = True
    if h["accuracy"] is not None and m["accuracy"] is not None: mono &= h["accuracy"] + tol >= m["accuracy"]
    if m["accuracy"] is not None and l["accuracy"] is not None: mono &= m["accuracy"] + tol >= l["accuracy"]
    return {"pass": bool(hg and mono and h["count"] < len(ev)), "overall_accuracy": overall, "by_grade": by, "high_accuracy_gain_pp": gain, "gates": {"high_gate": hg, "monotonic_gate": bool(mono), "nondegenerate_gate": h["count"] < len(ev)}}


def domain_audit(indep, domain, baseline, cfg):
    ev = indep[indep["pred"].isin(D) & indep["actual"].isin(D)]
    p, u = f"{domain}_pred", f"{domain}_usable"
    z = ev[ev[u].fillna(False) & ev[p].isin(D)]
    agree, conflict = z[z[p] == z["pred"]], z[z[p] != z["pred"]]
    aa = acc(agree["actual"], agree["pred"]) if len(agree) else None
    ca = acc(conflict["actual"], conflict["pred"]) if len(conflict) else None
    rg = cfg.get("v032", {}).get("domain_acceptance", {})
    again = None if baseline is None or aa is None else round(aa - baseline, 2)
    cgap = None if baseline is None or ca is None else round(ca - baseline, 2)
    ag = bool(len(agree) >= int(rg.get("min_agree_cases", 10)) and again is not None and again >= float(rg.get("min_agree_gain_pp", 3)))
    cg = bool(len(conflict) >= int(rg.get("min_conflict_cases", 8)) and cgap is not None and cgap <= float(rg.get("max_conflict_gap_pp", -3)))
    return {"pass": bool(ag and cg), "usable_count": int(len(z)), "agree_count": int(len(agree)), "agree_accuracy": aa, "agree_gain_pp": again, "conflict_count": int(len(conflict)), "conflict_accuracy": ca, "conflict_gap_pp": cgap, "gates": {"agree_gate": ag, "conflict_gate": cg}}


def old_failure(old_wf, old_cwf):
    ev = old_wf[old_wf["actual"].isin(D)]
    naive = ev.assign(naive="UP30_FIRST")
    old = stats(ev, "pred")
    n = stats(naive, "naive")
    high = ev[ev["confidence_grade"] == "HIGH"]
    def rel(domain):
        p, u = f"{domain}_pred", f"{domain}_usable"
        z = old_cwf[old_cwf["actual"].isin(D) & old_cwf[u].fillna(False) & old_cwf[p].isin(D)]
        a, c = z[z[p] == z["base_pred"]], z[z[p] != z["base_pred"]]
        return {"agree_count": int(len(a)), "agree_accuracy": round(float(a["base_correct"].mean()) * 100, 2) if len(a) else None, "conflict_count": int(len(c)), "conflict_accuracy": round(float(c["base_correct"].mean()) * 100, 2) if len(c) else None}
    return {"price_core": old, "naive_always_up": n, "price_core_minus_naive_accuracy_pp": None if old.get("accuracy") is None else round(old["accuracy"] - n["accuracy"], 2), "high_confidence": {"count": int(len(high)), "accuracy": acc(high["actual"], high["pred"]) if len(high) else None, "prediction_mix": pred_mix(high["pred"]) if len(high) else {}}, "onchain": rel("onchain"), "macro": rel("macro"), "root_causes": ["Price core was nearly an always-UP classifier, so raw accuracy hid near-random balanced accuracy.", "Monthly 365-day first-passage labels overlap heavily; acceptance must use independent test episodes.", "Raw analog consensus was treated as confidence even when HIGH was not more accurate.", "On-chain/macro usability was based on standalone confidence rather than incremental utility conditional on similar price structure."]}


def validate(indep, old_wf, cfg):
    cand = stats(indep, "pred")
    old = stats(indep.join(old_wf[["pred"]].rename(columns={"pred": "old_pred"}), how="left"), "old_pred")
    naive = stats(indep.assign(naive_pred=naive_prior_pred(indep)), "naive_pred")
    rg = cfg.get("v032", {}).get("core_acceptance", {})
    bg = None if cand.get("balanced_accuracy") is None or old.get("balanced_accuracy") is None else round(cand["balanced_accuracy"] - old["balanced_accuracy"], 2)
    ag = None if cand.get("accuracy") is None or naive.get("accuracy") is None else round(cand["accuracy"] - naive["accuracy"], 2)
    rr, mx = cand.get("class_recalls", {}), cand.get("prediction_mix", {})
    minority = min(float(mx.get("up_pct") or 0), float(mx.get("down_pct") or 0))
    gates = {
        "independent_episode_gate": len(indep) >= int(rg.get("min_independent_episodes", 30)),
        "coverage_gate": float(cand.get("coverage_pct") or 0) >= float(rg.get("min_coverage_pct", 60)),
        "balanced_accuracy_gate": cand.get("balanced_accuracy") is not None and cand["balanced_accuracy"] >= float(rg.get("min_balanced_accuracy", 55)),
        "balanced_gain_vs_old_gate": bg is not None and bg >= float(rg.get("min_balanced_gain_vs_old_pp", 3)),
        "accuracy_vs_naive_gate": ag is not None and ag >= float(rg.get("min_accuracy_gap_vs_naive_pp", -2)),
        "both_class_recall_gate": all(rr.get(x) is not None and rr[x] >= float(rg.get("min_each_class_recall_pct", 35)) for x in D),
        "nondegenerate_prediction_gate": minority >= float(rg.get("min_minority_prediction_pct", 10)),
    }
    core_pass = all(gates.values())
    conf = confidence_audit(indep, cfg)
    on = domain_audit(indep, "onchain", cand.get("accuracy"), cfg)
    ma = domain_audit(indep, "macro", cand.get("accuracy"), cfg)
    dp = bool(on["pass"] or ma["pass"])
    if core_pass and conf["pass"] and dp:
        readiness, nxt = "PASS", "BUILD_MODERN_DERIVATIVES_LAYER_B"
    elif core_pass:
        readiness, nxt = "PARTIAL_CORE_ONLY", "FIX_CONFIDENCE_OR_CONFIRMATION_BEFORE_LAYER_B"
    else:
        readiness, nxt = "FAIL", "REVISE_PRICE_CORE_BEFORE_LAYER_B"
    return {"independent_episode_rows": int(len(indep)), "candidate": cand, "old_price_core": old, "naive_training_majority": naive, "candidate_balanced_gain_vs_old_pp": bg, "candidate_accuracy_gap_vs_naive_pp": ag, "core_gates": gates, "core_pass": core_pass, "confidence_calibration": conf, "conditional_confirmation": {"onchain": on, "macro": ma, "any_domain_pass": dp}, "master_readiness": readiness, "next_step": nxt, "master_integration": "FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL"}


def main_v032():
    v03.main_v03()
    cfg, out, data = core.load_config(), core.OUT, core.DATA
    features = pd.read_csv(data / "historical_features_daily.csv", parse_dates=["time"]).set_index("time")
    events = pd.read_csv(data / "event_registry.csv", parse_dates=["time"]).set_index("time")
    old_wf = pd.read_csv(out / "walk_forward_price_core.csv", parse_dates=["time"]).set_index("time")
    old_cwf = pd.read_csv(out / "walk_forward_confirmation.csv", parse_dates=["time"]).set_index("time")
    wf = walk_forward(features, events, cfg, old_wf)
    wf.to_csv(out / "walk_forward_v032.csv")
    indep = independent_rows(wf)
    indep.to_csv(out / "episode_independent_v032.csv")
    qt = features.dropna(subset=["price"]).index.max()
    current = {"engine": "BTC_HISTORICAL_REGIME_OUTCOME_V0_3_2", "schema_version": "0.3.2", "architecture": "DEBIASED_PRICE_CORE_WITH_EPISODE_OOS_AND_PRICE_CONDITIONAL_CONFIRMATION", "query_date": qt.date().isoformat(), "price_core": price_predict(features, events, cfg, qt), "conditional_confirmation": {"onchain": domain_predict(features, events, cfg, qt, "onchain"), "macro": domain_predict(features, events, cfg, qt, "macro")}, "warning": "Support shares are model evidence scores, not calibrated probabilities."}
    (out / "current_v032.json").write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    failure = old_failure(old_wf, old_cwf)
    (out / "v031_failure_analysis.json").write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
    result = validate(indep, old_wf, cfg)
    final = {"engine": "BTC_HISTORICAL_REGIME_OUTCOME_V0_3_2", "schema_version": "0.3.2", "architecture": "DEBIASED_PRICE_CORE_WITH_EPISODE_OOS_AND_PRICE_CONDITIONAL_CONFIRMATION", "failure_analysis_v031": failure, "validation": result, "design_freeze": {"class_prior_adjustment": "inverse-sqrt independent-episode prior", "similarity_weight": "squared price similarity", "abstention": "support margin below configured threshold", "acceptance_basis": "first walk-forward anchor per independent point-in-time episode", "confirmation": "on-chain/macro rerank only within a price-similar independent-episode pool", "note": "No MASTER score, entry gate, schedule, or live-plan code is modified by this build."}}
    (out / "v032_validation_summary.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(final, ensure_ascii=False))


if __name__ == "__main__":
    main_v032()
