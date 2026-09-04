from __future__ import annotations

import json
import math
import numpy as np
import pandas as pd

import run_v26_v035 as v035

v034 = v035.v034
v032 = v035.v032
v02 = v035.v02
core = v035.core


def turning_zone_labels(features: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Objective historical bottom/top zones, used only after their forward window is fully known.

    Bottom zone: already materially below trailing 365d high, then +30% occurs before -15% within 180d.
    Top zone: near trailing high / recently extended, then -20% occurs before +20% within 180d.
    These are evaluation/archetype labels, never features at the live query date.
    """
    c = cfg.get("v037", {})
    p = pd.to_numeric(features["price"], errors="coerce")
    dd = pd.to_numeric(features.get("dd_from_365d_high"), errors="coerce")
    r90 = pd.to_numeric(features.get("ret_90d"), errors="coerce")
    h = int(c.get("outcome_horizon_days", 180))
    bup = float(c.get("bottom_up_target", 0.30)); bdn = float(c.get("bottom_adverse_target", -0.15))
    tdn = float(c.get("top_down_target", -0.20)); tup = float(c.get("top_adverse_target", 0.20))
    bottom_dd = float(c.get("bottom_min_drawdown", -0.18))
    top_dd = float(c.get("top_max_drawdown_from_high", -0.12)); top_r90 = float(c.get("top_min_ret90", 0.25))

    bottom = pd.Series(False, index=features.index); top = pd.Series(False, index=features.index)
    up30_time = pd.Series(pd.NaT, index=features.index); dn20_time = pd.Series(pd.NaT, index=features.index)
    for i in range(len(p)):
        p0 = p.iloc[i]
        if not np.isfinite(p0) or i + 1 >= len(p):
            continue
        fut = p.iloc[i + 1:min(len(p), i + 1 + h)].dropna()
        if fut.empty:
            continue
        ret = fut / p0 - 1.0
        b_up_hits = np.flatnonzero(ret.to_numpy() >= bup); b_dn_hits = np.flatnonzero(ret.to_numpy() <= bdn)
        t_dn_hits = np.flatnonzero(ret.to_numpy() <= tdn); t_up_hits = np.flatnonzero(ret.to_numpy() >= tup)
        b_ok = len(b_up_hits) and (not len(b_dn_hits) or int(b_up_hits[0]) < int(b_dn_hits[0]))
        t_ok = len(t_dn_hits) and (not len(t_up_hits) or int(t_dn_hits[0]) < int(t_up_hits[0]))
        if len(b_up_hits): up30_time.iloc[i] = fut.index[int(b_up_hits[0])]
        if len(t_dn_hits): dn20_time.iloc[i] = fut.index[int(t_dn_hits[0])]
        if bool(b_ok) and np.isfinite(dd.iloc[i]) and dd.iloc[i] <= bottom_dd:
            bottom.iloc[i] = True
        if bool(t_ok) and ((np.isfinite(dd.iloc[i]) and dd.iloc[i] >= top_dd) or (np.isfinite(r90.iloc[i]) and r90.iloc[i] >= top_r90)):
            top.iloc[i] = True
    both = bottom & top
    bottom.loc[both] = False; top.loc[both] = False
    return pd.DataFrame({"bottom_zone": bottom, "top_zone": top, "up30_first_time": up30_time, "dn20_first_time": dn20_time})


def _cluster_anchors(zone: pd.Series, price: pd.Series, kind: str, gap_days: int) -> list[pd.Timestamp]:
    dates = list(zone.index[zone.fillna(False)])
    if not dates:
        return []
    clusters = [[dates[0]]]
    for t in dates[1:]:
        if (t - clusters[-1][-1]).days <= gap_days:
            clusters[-1].append(t)
        else:
            clusters.append([t])
    anchors = []
    for cl in clusters:
        s = price.reindex(cl).dropna()
        if s.empty:
            continue
        anchors.append(s.idxmin() if kind == "bottom" else s.idxmax())
    return anchors


def _specialist_cfg(cfg: dict, kind: str) -> dict:
    x = json.loads(json.dumps(cfg))
    spec = v035.REGIME_SUBSPACES["BOTTOM" if kind == "bottom" else "TOP"]
    cols = spec["state"] + spec["path"]
    # similarity_scores_fast discovers known columns through group definitions; zero non-price groups.
    x["feature_group_weights"] = {"price_state": float(spec["weights"][0]), "price_path": float(spec["weights"][1]), "onchain_state": 0.0, "onchain_path": 0.0, "macro": 0.0}
    x["min_complete_features"] = max(4, min(8, len(cols) // 2))
    return x


def _auc(y: pd.Series, score: pd.Series) -> float | None:
    z = pd.DataFrame({"y": y.astype(bool), "s": pd.to_numeric(score, errors="coerce")}).dropna()
    pos = z[z.y]; neg = z[~z.y]
    if pos.empty or neg.empty:
        return None
    ranks = z.s.rank(method="average")
    rank_sum = float(ranks[z.y].sum())
    n1, n0 = len(pos), len(neg)
    auc = (rank_sum - n1 * (n1 + 1) / 2) / (n1 * n0)
    return round(float(auc) * 100, 2)


def _aggregate(s: pd.Series, k: int) -> float | None:
    z = pd.to_numeric(s, errors="coerce").dropna().sort_values(ascending=False).head(k)
    if z.empty:
        return None
    return float(0.50 * z.mean() + 0.30 * z.median() + 0.20 * z.iloc[0])


def specialist_score(features: pd.DataFrame, events: pd.DataFrame, labels: pd.DataFrame, cfg: dict, qt: pd.Timestamp, kind: str) -> dict:
    c = cfg.get("v037", {})
    horizon = int(c.get("outcome_horizon_days", 180))
    gap_days = int(c.get("zone_cluster_gap_days", 14))
    k = int(c.get("analog_k", 6))
    min_pos = int(c.get("min_positive_anchors", 5))
    price = pd.to_numeric(features["price"], errors="coerce")
    zone_col = "bottom_zone" if kind == "bottom" else "top_zone"
    all_anchors = _cluster_anchors(labels[zone_col], price, kind, gap_days)
    cutoff = qt - pd.Timedelta(days=horizon)
    anchors = [t for t in all_anchors if t <= cutoff]
    if len(anchors) < min_pos:
        return {"kind": kind, "score": None, "status": "INSUFFICIENT_HISTORY", "positive_anchors": int(len(anchors))}

    spec = v035.REGIME_SUBSPACES["BOTTOM" if kind == "bottom" else "TOP"]
    cols = [c0 for c0 in spec["state"] + spec["path"] if c0 in features.columns]
    sub = features[["price"] + cols].copy()
    scfg = _specialist_cfg(cfg, kind)
    sims = v02.similarity_scores_fast(sub.loc[:qt], qt, scfg, required_horizon=horizon)
    if sims.empty:
        return {"kind": kind, "score": None, "status": "NO_SIMILARITY_HISTORY"}
    sim = pd.to_numeric(sims["similarity"], errors="coerce")
    pos_sim = sim.reindex(anchors).dropna()

    controls = v034._episode_representatives(features, events, cfg, qt)
    controls = controls[~controls.index.isin(anchors)].copy()
    control_sim = sim.reindex(controls.index).dropna()
    if len(pos_sim) < min_pos or len(control_sim) < k:
        return {"kind": kind, "score": None, "status": "INSUFFICIENT_BALANCED_HISTORY", "positive_anchors": int(len(pos_sim)), "controls": int(len(control_sim))}

    pa = _aggregate(pos_sim, k); ca = _aggregate(control_sim, k)
    if pa is None or ca is None:
        return {"kind": kind, "score": None, "status": "NO_AGGREGATE"}
    gap = float(pa - ca)
    med = float(pos_sim.median()); iqr = v034._safe_iqr(pos_sim)
    z = 0.0 if not np.isfinite(iqr) else (pa - med) / iqr
    gap_component = 50.0 + 42.0 * math.tanh(gap / max(float(c.get("gap_scale", 6.0)), 1e-6))
    norm_component = float(np.clip(50.0 + 14.0 * z, 0, 100))
    support = min(1.0, len(pos_sim) / max(float(c.get("full_support_anchors", 12)), 1.0))
    score = int(round(np.clip((0.72 * gap_component + 0.28 * norm_component) * (0.85 + 0.15 * support), 0, 100)))
    top_pos = pos_sim.sort_values(ascending=False).head(k)
    return {
        "kind": kind, "score": score, "status": "OK", "positive_anchors": int(len(pos_sim)), "controls": int(len(control_sim)),
        "positive_similarity": round(pa, 2), "control_similarity": round(ca, 2), "gap": round(gap, 2), "within_positive_z": round(float(z), 3),
        "analogs": [{"date": t.date().isoformat(), "similarity": round(float(v), 2)} for t, v in top_pos.items()],
        "warning": "Similarity score, not probability. Historical zone labels are used only after their forward outcome window is fully known.",
    }


def walk_forward_specialists(features: pd.DataFrame, events: pd.DataFrame, labels: pd.DataFrame, cfg: dict, anchor_wf: pd.DataFrame) -> pd.DataFrame:
    eps = v02.episode_ids(features["price"].dropna(), float(cfg.get("episode_reversal_pct", 0.20)), int(cfg.get("episode_max_days", 365)))
    rows = []
    for qt in anchor_wf.index:
        try:
            b = specialist_score(features, events, labels, cfg, qt, "bottom")
            t = specialist_score(features, events, labels, cfg, qt, "top")
        except Exception as e:
            rows.append({"time": qt, "error": str(e)}); continue
        rows.append({
            "time": qt, "bottom_score": b.get("score"), "top_score": t.get("score"),
            "actual_bottom_zone": bool(labels.reindex([qt])["bottom_zone"].fillna(False).iloc[0]) if qt in labels.index else False,
            "actual_top_zone": bool(labels.reindex([qt])["top_zone"].fillna(False).iloc[0]) if qt in labels.index else False,
            "test_episode_id": int(eps.loc[qt]) if qt in eps.index and pd.notna(eps.loc[qt]) else np.nan,
        })
    return pd.DataFrame(rows).set_index("time") if rows else pd.DataFrame()


def _specialist_validation(indep: pd.DataFrame, kind: str, cfg: dict) -> dict:
    scol = f"{kind}_score"; ycol = f"actual_{kind}_zone"
    z = indep[[scol, ycol]].copy(); z[scol] = pd.to_numeric(z[scol], errors="coerce"); z = z.dropna(subset=[scol])
    pos = int(z[ycol].sum()); neg = int((~z[ycol].astype(bool)).sum())
    auc = _auc(z[ycol], z[scol]) if len(z) else None
    baseline = None if not len(z) else float(z[ycol].mean()) * 100
    q = float(cfg.get("v037", {}).get("high_score_quantile", 0.75))
    cutoff = None if not len(z) else float(z[scol].quantile(q))
    high = z[z[scol] >= cutoff] if cutoff is not None else z.iloc[0:0]
    high_rate = None if not len(high) else float(high[ycol].mean()) * 100
    lift = None if not baseline or high_rate is None else high_rate / baseline
    return {
        "rows": int(len(z)), "positive": pos, "negative": neg, "auc": auc,
        "baseline_positive_rate_pct": None if baseline is None else round(baseline, 2),
        "top_quartile_cutoff": None if cutoff is None else round(cutoff, 2), "top_quartile_count": int(len(high)),
        "top_quartile_positive_rate_pct": None if high_rate is None else round(high_rate, 2), "top_quartile_lift": None if lift is None else round(lift, 2),
    }


def validate(indep: pd.DataFrame, cfg: dict) -> dict:
    c = cfg.get("v037", {}).get("acceptance", {})
    b = _specialist_validation(indep, "bottom", cfg); t = _specialist_validation(indep, "top", cfg)
    def gates(x):
        return {
            "sample_gate": x["positive"] >= int(c.get("min_positive_cases", 6)),
            "auc_gate": x.get("auc") is not None and x["auc"] >= float(c.get("min_auc", 65)),
            "lift_gate": x.get("top_quartile_lift") is not None and x["top_quartile_lift"] >= float(c.get("min_top_quartile_lift", 1.5)),
        }
    bg, tg = gates(b), gates(t)
    return {"bottom": b, "bottom_gates": bg, "bottom_pass": all(bg.values()), "top": t, "top_gates": tg, "top_pass": all(tg.values()), "specialist_stage": "PASS" if all(bg.values()) and all(tg.values()) else "PARTIAL_OR_FAIL"}


def main_v037() -> None:
    # Preserve and rebuild the already-passed V0.3.5 core; specialists are additional diagnostics only.
    v035.main_v035()
    cfg, out, data = core.load_config(), core.OUT, core.DATA
    features = pd.read_csv(data / "historical_features_daily.csv", parse_dates=["time"]).set_index("time")
    events = pd.read_csv(data / "event_registry.csv", parse_dates=["time"]).set_index("time")
    anchor_wf = pd.read_csv(out / "walk_forward_price_core.csv", parse_dates=["time"]).set_index("time")
    labels = turning_zone_labels(features, cfg)
    labels.to_csv(out / "turning_zone_labels_v037.csv")
    wf = walk_forward_specialists(features, events, labels, cfg, anchor_wf)
    wf.to_csv(out / "walk_forward_v037_specialists.csv")
    indep = v032.independent_rows(wf.assign(pred="ABSTAIN", actual="UP30_FIRST"))
    indep.to_csv(out / "episode_independent_v037_specialists.csv")

    qt = features.dropna(subset=["price"]).index.max()
    current = {
        "engine": "BTC_HISTORICAL_REGIME_OUTCOME_V0_3_7",
        "schema_version": "0.3.7",
        "architecture": "V035_CORE_PLUS_OBJECTIVE_BOTTOM_TOP_ZONE_SPECIALISTS",
        "query_date": qt.date().isoformat(),
        "bottom_specialist": specialist_score(features, events, labels, cfg, qt, "bottom"),
        "top_specialist": specialist_score(features, events, labels, cfg, qt, "top"),
        "master_integration": "FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL",
    }
    (out / "current_v037_specialists.json").write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    validation = validate(indep, cfg)
    final = {
        "engine": "BTC_HISTORICAL_REGIME_OUTCOME_V0_3_7", "schema_version": "0.3.7",
        "architecture": "V035_CORE_PLUS_OBJECTIVE_BOTTOM_TOP_ZONE_SPECIALISTS",
        "core_freeze": "V0.3.5 FULL_PASS core unchanged; V0.3.6 onchain/macro remains context-only after utility FAIL",
        "validation": validation,
        "next_step": "BUILD_LAYER_B_DERIVATIVES" if validation.get("specialist_stage") == "PASS" else "KEEP_PASSED_SPECIALISTS_ONLY_AND_BUILD_LAYER_B_DERIVATIVES",
        "note": "Specialist scores are historical-pattern similarity, not probabilities. Failure does not invalidate V0.3.5 core.",
    }
    (out / "v037_validation_summary.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(final, ensure_ascii=False))


if __name__ == "__main__":
    main_v037()
