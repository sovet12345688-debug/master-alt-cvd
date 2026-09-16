from __future__ import annotations

import json
import math
import numpy as np
import pandas as pd

import run_v26_v035 as v035

v034 = v035.v034
v033 = v035.v033
v032 = v035.v032
v03 = v033.v032.v03
v02 = v035.v02
core = v035.core
D = v035.D


def _agg(s: pd.Series) -> float | None:
    z = pd.to_numeric(s, errors="coerce").dropna().sort_values(ascending=False)
    if z.empty:
        return None
    return float(0.50 * z.mean() + 0.30 * z.median() + 0.20 * z.iloc[0])


def _domain_balanced_signal(features: pd.DataFrame, events: pd.DataFrame, cfg: dict, qt: pd.Timestamp, domain: str) -> dict:
    """Confirmation-only signal within a price-similar independent-episode pool.

    Price/regime core selects the neighborhood. The domain may only say AGREE/CONFLICT/NO_SIGNAL;
    it never changes the V0.3.5 core prediction. Equal UP/DOWN counts remove outcome-frequency advantage.
    """
    c = cfg.get("v036", {})
    pool_k = int(c.get("price_pool_k", 40))
    k_target = int(c.get("domain_per_class_k", 5))
    min_k = int(c.get("domain_min_per_class", 4))
    min_gap = float(c.get("domain_min_score_gap", 1.25))
    min_pair_wins = int(c.get("domain_min_pair_wins", 3))

    price_reps = v034._episode_representatives(features, events, cfg, qt)
    if price_reps.empty:
        return {"domain": domain, "usable": False, "pred": None, "reason": "NO_PRICE_POOL"}
    price_pool = price_reps.sort_values("similarity", ascending=False).head(min(pool_k, len(price_reps))).copy()

    dcfg = v03.standalone_domain_cfg(cfg, domain)
    ds = v02.similarity_scores_fast(features.loc[:qt], qt, dcfg, required_horizon=365)
    if ds.empty:
        return {"domain": domain, "usable": False, "pred": None, "reason": "NO_DOMAIN_HISTORY"}
    common = price_pool.index.intersection(ds.index)
    if len(common) < 2 * min_k:
        return {"domain": domain, "usable": False, "pred": None, "reason": "INSUFFICIENT_COMMON_PRICE_POOL", "common": int(len(common))}

    x = price_pool.loc[common].copy()
    x["domain_similarity"] = pd.to_numeric(ds.reindex(common)["similarity"], errors="coerce")
    x["outcome"] = events.reindex(common)["fp_up30_vs_dn20"]
    x = x[x["domain_similarity"].notna() & x["outcome"].isin(D)].copy()
    up = x[x["outcome"] == D[0]].sort_values("domain_similarity", ascending=False)
    dn = x[x["outcome"] == D[1]].sort_values("domain_similarity", ascending=False)
    k = min(k_target, len(up), len(dn))
    if k < min_k:
        return {
            "domain": domain, "usable": False, "pred": None, "reason": "INSUFFICIENT_BALANCED_OUTCOMES",
            "available": {"up": int(len(up)), "down": int(len(dn))}, "k_each": int(k),
        }
    up = up.head(k).copy(); dn = dn.head(k).copy()
    us = pd.to_numeric(up["domain_similarity"], errors="coerce").to_numpy(dtype=float)
    ds_ = pd.to_numeric(dn["domain_similarity"], errors="coerce").to_numpy(dtype=float)
    up_score = _agg(up["domain_similarity"]); dn_score = _agg(dn["domain_similarity"])
    if up_score is None or dn_score is None:
        return {"domain": domain, "usable": False, "pred": None, "reason": "NO_BALANCED_SCORE"}
    gap = float(up_score - dn_score)
    winner = D[0] if gap > 0 else D[1]
    up_wins = int(np.sum(us > ds_)); dn_wins = int(np.sum(ds_ > us))
    wins = up_wins if winner == D[0] else dn_wins
    usable = bool(abs(gap) >= min_gap and wins >= min_pair_wins)

    return {
        "domain": domain,
        "usable": usable,
        "pred": winner if usable else None,
        "k_each": int(k),
        "score": {"up": round(float(up_score), 2), "down": round(float(dn_score), 2), "gap_up_minus_down": round(gap, 2)},
        "pair_wins": {"up": up_wins, "down": dn_wins, "required": min_pair_wins},
        "price_pool_count": int(len(price_pool)),
        "domain_common_count": int(len(x)),
        "up_dates": [t.date().isoformat() for t in up.index],
        "down_dates": [t.date().isoformat() for t in dn.index],
        "rule": "Confirmation only inside price-similar independent episodes; equal UP/DOWN domain comparison; cannot flip price/regime core.",
    }


def confirmation_at_v036(features: pd.DataFrame, events: pd.DataFrame, cfg: dict, qt: pd.Timestamp, base_pred: str) -> dict:
    domains = {d: _domain_balanced_signal(features, events, cfg, qt, d) for d in ("onchain", "macro")}
    agree, conflict = [], []
    if base_pred in D:
        for name, z in domains.items():
            if z.get("usable") and z.get("pred") in D:
                (agree if z["pred"] == base_pred else conflict).append(name)
    if base_pred not in D:
        status = "BASE_ABSTAIN"
    elif agree and conflict:
        status = "MIXED_CONFLICT"
    elif len(agree) == 2:
        status = "CONFIRMED_BOTH"
    elif len(agree) == 1:
        status = "CONFIRMED_ONE"
    elif conflict:
        status = "CONFLICT"
    else:
        status = "NO_CONFIRMATION"
    return {"query_date": qt.date().isoformat(), "base_pred": base_pred, "status": status, "agree": agree, "conflict": conflict, "domains": domains}


def walk_forward_v036(features: pd.DataFrame, events: pd.DataFrame, cfg: dict, core_wf: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for qt, base in core_wf.iterrows():
        pred, actual = base.get("pred"), base.get("actual")
        try:
            c = confirmation_at_v036(features, events, cfg, qt, pred)
        except Exception as e:
            rows.append({"time": qt, "base_pred": pred, "actual": actual, "error": str(e)})
            continue
        rows.append({
            "time": qt, "base_pred": pred, "actual": actual,
            "base_correct": pred == actual if pred in D and actual in D else np.nan,
            "confirmation_status": c["status"],
            "onchain_pred": c["domains"]["onchain"].get("pred"),
            "onchain_usable": bool(c["domains"]["onchain"].get("usable")),
            "onchain_gap": c["domains"]["onchain"].get("score", {}).get("gap_up_minus_down"),
            "macro_pred": c["domains"]["macro"].get("pred"),
            "macro_usable": bool(c["domains"]["macro"].get("usable")),
            "macro_gap": c["domains"]["macro"].get("score", {}).get("gap_up_minus_down"),
            "test_episode_id": base.get("test_episode_id"),
        })
    return pd.DataFrame(rows).set_index("time") if rows else pd.DataFrame()


def _subset(frame: pd.DataFrame) -> dict:
    z = frame[frame["base_correct"].notna()]
    return {"count": int(len(z)), "accuracy": None if not len(z) else round(float(z["base_correct"].mean()) * 100, 2)}


def _summary(indep: pd.DataFrame) -> dict:
    ev = indep[indep["base_correct"].notna()].copy()
    baseline = None if not len(ev) else round(float(ev["base_correct"].mean()) * 100, 2)
    confirmed = ev[ev["confirmation_status"].isin(["CONFIRMED_ONE", "CONFIRMED_BOTH"])]
    both = ev[ev["confirmation_status"] == "CONFIRMED_BOTH"]
    conflict = ev[ev["confirmation_status"].isin(["CONFLICT", "MIXED_CONFLICT"])]
    no_conf = ev[ev["confirmation_status"] == "NO_CONFIRMATION"]
    out = {
        "rows": int(len(indep)), "evaluable": int(len(ev)), "baseline_price_regime_core_accuracy": baseline,
        "confirmed": _subset(confirmed), "confirmed_both": _subset(both), "conflict": _subset(conflict), "no_confirmation": _subset(no_conf),
        "confirmed_coverage_pct": None if not len(ev) else round(len(confirmed) / len(ev) * 100, 2),
        "conflict_coverage_pct": None if not len(ev) else round(len(conflict) / len(ev) * 100, 2),
    }
    for domain in ("onchain", "macro"):
        z = ev[ev[f"{domain}_usable"].fillna(False) & ev[f"{domain}_pred"].isin(D)]
        a = z[z[f"{domain}_pred"] == z["base_pred"]]
        c = z[z[f"{domain}_pred"] != z["base_pred"]]
        out[domain] = {"usable": int(len(z)), "agree": _subset(a), "conflict": _subset(c)}
    return out


def _accept(summary: dict, cfg: dict) -> dict:
    # Preserve the original confirmation utility hurdles; do not lower them because V0.3.5 passed.
    base = summary.get("baseline_price_regime_core_accuracy")
    confirmed = summary.get("confirmed", {}); both = summary.get("confirmed_both", {}); conflict = summary.get("conflict", {})
    cc = cfg.get("confirmation_acceptance", {})
    cg = None if base is None or confirmed.get("accuracy") is None else round(float(confirmed["accuracy"]) - float(base), 2)
    bg = None if base is None or both.get("accuracy") is None else round(float(both["accuracy"]) - float(base), 2)
    fg = None if base is None or conflict.get("accuracy") is None else round(float(conflict["accuracy"]) - float(base), 2)
    positive = bool(int(confirmed.get("count") or 0) >= int(cc.get("min_confirmed_cases", 20)) and cg is not None and cg >= float(cc.get("min_confirmed_accuracy_gain_pp", 3)))
    strong = bool(int(both.get("count") or 0) >= int(cc.get("min_both_confirmed_cases", 10)) and bg is not None and bg >= float(cc.get("min_both_confirmed_accuracy_gain_pp", 6)))
    conflict_gate = bool(int(conflict.get("count") or 0) >= int(cc.get("min_conflict_cases", 10)) and fg is not None and fg <= float(cc.get("max_conflict_accuracy_gap_pp", -3)))
    return {
        "utility_pass": bool(positive and (strong or conflict_gate)),
        "confirmed_gain_pp": cg, "both_gain_pp": bg, "conflict_gap_pp": fg,
        "positive_confirmation_gate": positive, "strong_confirmation_gate": strong, "conflict_gate": conflict_gate,
        "locked_hurdles": cc,
    }


def main_v036() -> None:
    v035.main_v035()
    cfg, out, data = core.load_config(), core.OUT, core.DATA
    features = pd.read_csv(data / "historical_features_daily.csv", parse_dates=["time"]).set_index("time")
    events = pd.read_csv(data / "event_registry.csv", parse_dates=["time"]).set_index("time")
    core_wf = pd.read_csv(out / "walk_forward_v035.csv", parse_dates=["time"]).set_index("time")
    wf = walk_forward_v036(features, events, cfg, core_wf)
    wf.to_csv(out / "walk_forward_v036_confirmation.csv")
    indep = v032.independent_rows(wf.rename(columns={"base_pred": "pred", "base_correct": "correct"})).rename(columns={"pred": "base_pred", "correct": "base_correct"})
    indep.to_csv(out / "episode_independent_v036_confirmation.csv")

    qt = features.dropna(subset=["price"]).index.max()
    current_core = v035.regime_predict_v035(features, events, cfg, qt)
    current = confirmation_at_v036(features, events, cfg, qt, current_core.get("pred"))
    (out / "current_v036_confirmation.json").write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = _summary(indep)
    acceptance = _accept(summary, cfg)
    final = {
        "engine": "BTC_HISTORICAL_REGIME_OUTCOME_V0_3_6",
        "schema_version": "0.3.6",
        "architecture": "V035_REGIME_CORE_PLUS_PRICE_CONDITIONAL_ONCHAIN_MACRO_CONFIRMATION",
        "core_freeze": "V0.3.5 FULL_PASS core is unchanged",
        "validation": {"summary": summary, "acceptance": acceptance},
        "next_step": "BUILD_LAYER_B_DERIVATIVES" if acceptance["utility_pass"] else "KEEP_ONCHAIN_MACRO_CONTEXT_ONLY_AND_BUILD_LAYER_B_SEPARATELY",
        "master_readiness": "NOT_READY_PENDING_LAYER_B_C_AND_EXPLICIT_APPROVAL",
        "master_integration": "FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL",
        "note": "Confirmation cannot flip V0.3.5 direction. Failing confirmation utility does not invalidate the already-passed price/regime core.",
    }
    (out / "v036_validation_summary.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(final, ensure_ascii=False))


if __name__ == "__main__":
    main_v036()
