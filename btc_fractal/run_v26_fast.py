from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

import run_v26_fractal as core

BASE_BUILD_FEATURES = core.build_features


def build_event_registry_fast(price: pd.Series, cfg: dict) -> pd.DataFrame:
    price = price.dropna().astype(float)
    arr = price.to_numpy(dtype=float)
    idx = price.index
    n = len(arr)
    max_h = int(max(cfg["outcome_horizons_days"]))
    usable_n = max(0, n - max_h)
    if usable_n <= 0:
        return pd.DataFrame()

    base = arr[:usable_n]
    future_ret = np.full((usable_n, max_h), np.nan, dtype=float)
    for d in range(1, max_h + 1):
        future_ret[:, d - 1] = arr[d:d + usable_n] / base - 1.0

    out: dict[str, object] = {"time": idx[:usable_n], "anchor_price": base}
    targets = [int(x) for x in cfg["up_targets_pct"]] + [int(x) for x in cfg["down_targets_pct"]]

    def first_days(mask: np.ndarray) -> np.ndarray:
        any_hit = mask.any(axis=1)
        first = np.argmax(mask, axis=1).astype(float) + 1.0
        first[~any_hit] = np.nan
        return first

    cache: dict[tuple[int, int], np.ndarray] = {}
    for h in [int(x) for x in cfg["outcome_horizons_days"]]:
        block = future_ret[:, :h]
        out[f"mfe_{h}d"] = np.nanmax(block, axis=1)
        out[f"mae_{h}d"] = np.nanmin(block, axis=1)
        for t in targets:
            mask = block >= t / 100.0 if t > 0 else block <= t / 100.0
            days = first_days(mask)
            cache[(t, h)] = days
            out[f"hit_{'up' if t > 0 else 'dn'}{abs(t)}_{h}d"] = days

    up30 = cache.get((30, 365))
    dn20 = cache.get((-20, 365))
    fp = np.full(usable_n, "NONE", dtype=object)
    if up30 is not None and dn20 is not None:
        up_ok = np.isfinite(up30)
        dn_ok = np.isfinite(dn20)
        fp[up_ok & ~dn_ok] = "UP30_FIRST"
        fp[dn_ok & ~up_ok] = "DN20_FIRST"
        both = up_ok & dn_ok
        fp[both & (up30 < dn20)] = "UP30_FIRST"
        fp[both & (dn20 < up30)] = "DN20_FIRST"
        fp[both & (up30 == dn20)] = "SAME_DAY"

    out["fp_up30_vs_dn20"] = fp
    return pd.DataFrame(out).set_index("time")


def build_features_v02(raw: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    f = BASE_BUILD_FEATURES(raw, cfg)
    x = raw.sort_index()

    if {"CapMrktCurUSD", "CapMVRVCur"}.issubset(x.columns):
        mcap = pd.to_numeric(x["CapMrktCurUSD"], errors="coerce")
        mvrv = pd.to_numeric(x["CapMVRVCur"], errors="coerce").replace(0, np.nan)
        realized = mcap / mvrv
        f["CapRealDerivedUSD"] = realized

        mcap_std = mcap.expanding(min_periods=365).std(ddof=0).replace(0, np.nan)
        f["MVRVZ_DERIVED_PIT"] = (mcap - realized) / mcap_std
        for n in (30, 90, 180):
            f[f"MVRVZ_DERIVED_PIT_chg_{n}d"] = f["MVRVZ_DERIVED_PIT"] - f["MVRVZ_DERIVED_PIT"].shift(n)

        if "SplyCur" in x.columns:
            supply = pd.to_numeric(x["SplyCur"], errors="coerce").replace(0, np.nan)
            realized_price = realized / supply
            f["RealizedPriceDerivedUSD"] = realized_price
            f["dist_to_realized_price"] = f["price"] / realized_price - 1.0

    return f.replace([np.inf, -np.inf], np.nan)


def feature_groups_v02(cols: list[str]) -> dict[str, list[str]]:
    price_state = [
        c for c in cols
        if c.startswith("dist_sma")
        or c in {"rsi14", "dd_from_365d_high", "rebound_from_365d_low"}
    ]
    price_path = [
        c for c in cols
        if c.startswith("ret_")
        or c.startswith("vol_")
        or c.startswith("path_eff_")
        or (c.startswith("sma") and "slope" in c)
    ]

    onchain_state: list[str] = []
    for c in ("CapMVRVCur", "MVRVZ_DERIVED_PIT", "AdrActCnt_z", "TxCnt_z", "HashRate_z"):
        if c in cols:
            onchain_state.append(c)

    onchain_path = [
        c for c in cols
        if c.startswith(("CapMVRVCur_chg_", "MVRVZ_DERIVED_PIT_chg_", "AdrActCnt_chg_", "TxCnt_chg_", "HashRate_chg_"))
    ]

    macro = [
        c for c in cols
        if c.startswith(("US2Y", "US10Y", "US30Y", "US10Y_REAL", "YC_10Y2Y"))
    ]

    groups = {
        "price_state": price_state,
        "price_path": price_path,
        "onchain_state": onchain_state,
        "onchain_path": onchain_path,
        "macro": macro,
    }
    return {k: v for k, v in groups.items() if v}


def similarity_scores_fast(features: pd.DataFrame, query_time: pd.Timestamp, cfg: dict, required_horizon: int = 365) -> pd.DataFrame:
    if query_time not in features.index:
        raise ValueError(f"query_time {query_time} missing")

    candidate_end = query_time - pd.Timedelta(days=required_horizon)
    hist = features.loc[:candidate_end].copy()
    q = features.loc[query_time]
    numeric = [c for c in features.columns if c != "price"]
    groups = feature_groups_v02(numeric)

    med, scale = core.robust_scale_from_history(features.loc[:query_time, numeric])
    valid_scale = scale.notna() & (scale != 0)
    qz = (q[numeric] - med) / scale
    qz.loc[~valid_scale] = np.nan
    hz = (hist[numeric] - med) / scale

    invalid_cols = [c for c in hz.columns if not bool(valid_scale.get(c, False))]
    if invalid_cols:
        hz.loc[:, invalid_cols] = np.nan

    numerator = pd.Series(0.0, index=hist.index)
    denominator = pd.Series(0.0, index=hist.index)
    used_features = pd.Series(0.0, index=hist.index)
    group_outputs: dict[str, pd.Series] = {}

    for group, cols in groups.items():
        weight = float(cfg["feature_group_weights"].get(group, 0.0))
        if weight <= 0:
            continue

        usable_cols = [c for c in cols if c in hz.columns and pd.notna(qz.get(c))]
        if not usable_cols:
            continue

        diff = hz[usable_cols].sub(qz[usable_cols], axis=1).abs()
        cnt = diff.notna().sum(axis=1)
        dist = diff.mean(axis=1, skipna=True)
        sim = 100.0 * np.exp(-0.65 * dist)
        available = cnt > 0

        numerator = numerator.add(sim.where(available, 0.0) * weight, fill_value=0.0)
        denominator = denominator.add(available.astype(float) * weight, fill_value=0.0)
        used_features = used_features.add(cnt, fill_value=0.0)
        group_outputs[group] = sim.where(available)

    score = numerator / denominator.replace(0, np.nan)
    out = pd.DataFrame(
        {
            "similarity": score,
            "used_features": used_features,
            "coverage_weight": denominator,
        }
    )
    for group, series in group_outputs.items():
        out[f"sim_{group}"] = series

    out = out[
        (out["used_features"] >= int(cfg["min_complete_features"]))
        & out["similarity"].notna()
    ]
    return out.sort_values("similarity", ascending=False)


def episode_ids(price: pd.Series, reversal_pct: float = 0.20, max_days: int = 365) -> pd.Series:
    p = price.dropna().astype(float)
    if p.empty:
        return pd.Series(dtype="Int64")

    eid = 0
    mode = "neutral"
    anchor_price = float(p.iloc[0])
    high = anchor_price
    low = anchor_price
    boundary_date = p.index[0]
    labels: dict[pd.Timestamp, int] = {}

    for t, value in p.items():
        value = float(value)
        if (t - boundary_date).days >= max_days:
            eid += 1
            mode = "neutral"
            anchor_price = value
            high = value
            low = value
            boundary_date = t

        high = max(high, value)
        low = min(low, value)

        if mode == "neutral":
            if value >= anchor_price * (1.0 + reversal_pct):
                mode = "up"
                high = value
            elif value <= anchor_price * (1.0 - reversal_pct):
                mode = "down"
                low = value
        elif mode == "up":
            if value <= high * (1.0 - reversal_pct):
                eid += 1
                mode = "down"
                anchor_price = value
                high = value
                low = value
                boundary_date = t
        elif mode == "down":
            if value >= low * (1.0 + reversal_pct):
                eid += 1
                mode = "up"
                anchor_price = value
                high = value
                low = value
                boundary_date = t

        labels[t] = eid

    return pd.Series(labels, dtype="Int64")


def select_episode_analogs(scores: pd.DataFrame, features: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    if scores.empty:
        return pd.DataFrame()

    eps = episode_ids(
        features["price"],
        reversal_pct=float(cfg.get("episode_reversal_pct", 0.20)),
        max_days=int(cfg.get("episode_max_days", 365)),
    )

    selected: list[pd.Timestamp] = []
    seen_episodes: set[int] = set()
    min_sep = int(cfg.get("analog_min_separation_days", 21))
    top_k = int(cfg["analog_top_k"])

    for t in scores.index:
        if t not in eps.index or pd.isna(eps.loc[t]):
            continue
        episode = int(eps.loc[t])
        if episode in seen_episodes:
            continue
        if any(abs((t - prev).days) < min_sep for prev in selected):
            continue
        selected.append(t)
        seen_episodes.add(episode)
        if len(selected) >= top_k:
            break

    if not selected:
        return pd.DataFrame()

    out = scores.loc[selected].copy()
    out["episode_id"] = [int(eps.loc[t]) for t in selected]
    return out


def enrich_summary_with_episodes(summary: dict, analogs: pd.DataFrame) -> dict:
    if summary.get("status") != "OK" or analogs.empty:
        return summary

    summary = json.loads(json.dumps(summary))
    summary["episode_count"] = int(analogs["episode_id"].nunique()) if "episode_id" in analogs else int(len(analogs))

    by_date = {
        t.date().isoformat(): int(r["episode_id"])
        for t, r in analogs.iterrows()
        if "episode_id" in analogs.columns
    }
    for item in summary.get("analogs", []):
        if item.get("date") in by_date:
            item["episode_id"] = by_date[item["date"]]
    return summary


def wilson_lower_bound(successes: int, total: int, z: float = 1.6448536269514722) -> float:
    if total <= 0:
        return 0.0
    p = successes / total
    den = 1.0 + z * z / total
    center = p + z * z / (2.0 * total)
    adj = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total)
    return max(0.0, (center - adj) / den)


def confidence_score_v02(summary: dict, analogs: pd.DataFrame) -> dict:
    if summary.get("status") != "OK" or analogs.empty:
        return {"score": 0, "grade": "LOW", "reasons": ["no valid analogs"]}

    n = int(summary.get("analog_count", 0))
    years = int(summary.get("distinct_years", 0))
    episodes = int(summary.get("episode_count", n))
    fp = summary.get("first_passage_counts", {})
    up = int(fp.get("UP30_FIRST", 0))
    dn = int(fp.get("DN20_FIRST", 0))
    directional = up + dn

    if directional <= 0:
        return {
            "score": 0,
            "grade": "LOW",
            "reasons": ["no directional first-passage cases"],
            "analog_count": n,
            "distinct_years": years,
            "episode_count": episodes,
        }

    majority = max(up, dn)
    majority_share = majority / directional
    margin = abs(up - dn) / directional
    wilson = wilson_lower_bound(majority, directional)

    median_similarity = float(summary.get("median_similarity", 0.0))
    feature_cov = float(analogs["coverage_weight"].median()) if "coverage_weight" in analogs else 0.0

    sample_score = min(15.0, directional / 12.0 * 15.0)
    year_score = min(10.0, years / 8.0 * 10.0)
    consensus_score = np.clip((majority_share - 0.50) / 0.50, 0.0, 1.0) * 30.0
    wilson_score = np.clip((wilson - 0.35) / 0.45, 0.0, 1.0) * 20.0
    similarity_score = np.clip((median_similarity - 60.0) / 30.0, 0.0, 1.0) * 15.0
    coverage_score = np.clip(feature_cov, 0.0, 1.0) * 10.0

    score = int(round(min(
        100.0,
        sample_score + year_score + consensus_score + wilson_score + similarity_score + coverage_score,
    )))

    high = (
        score >= 75
        and directional >= 10
        and years >= 5
        and episodes >= 8
        and majority_share >= 0.80
        and wilson >= 0.58
    )
    medium = (
        score >= 55
        and directional >= 8
        and years >= 4
        and majority_share >= 0.62
    )
    grade = "HIGH" if high else ("MEDIUM" if medium else "LOW")

    reasons: list[str] = []
    if majority_share < 0.80:
        reasons.append("directional consensus below HIGH gate")
    if wilson < 0.58:
        reasons.append("conservative majority lower-bound below HIGH gate")
    if years < 5:
        reasons.append("historical year diversity below HIGH gate")
    if episodes < 8:
        reasons.append("independent episode diversity below HIGH gate")

    return {
        "score": score,
        "grade": grade,
        "majority_share_pct": round(majority_share * 100, 1),
        "directional_margin_pct": round(margin * 100, 1),
        "wilson_lower_pct": round(wilson * 100, 1),
        "analog_count": n,
        "directional_case_count": directional,
        "distinct_years": years,
        "episode_count": episodes,
        "median_similarity": round(median_similarity, 2),
        "feature_coverage_weight": round(feature_cov, 3),
        "reasons": reasons,
    }


def predict_at(features: pd.DataFrame, events: pd.DataFrame, cfg: dict, query_time: pd.Timestamp) -> dict:
    view = features.loc[:query_time]
    scores = similarity_scores_fast(view, query_time, cfg, required_horizon=365)
    analogs = select_episode_analogs(scores, view, cfg)
    summary = core.summarize_analogs(analogs, events, cfg)
    summary = enrich_summary_with_episodes(summary, analogs)
    conf = confidence_score_v02(summary, analogs)

    fp = summary.get("first_passage_counts", {}) if summary.get("status") == "OK" else {}
    up = int(fp.get("UP30_FIRST", 0))
    dn = int(fp.get("DN20_FIRST", 0))
    denom = up + dn

    if denom == 0 or up == dn:
        pred = None
    else:
        pred = "UP30_FIRST" if up > dn else "DN20_FIRST"

    return {
        "pred": pred,
        "up_case_share": round(up / denom * 100, 1) if denom else None,
        "down_case_share": round(dn / denom * 100, 1) if denom else None,
        "confidence": conf,
        "summary": summary,
        "analogs": analogs,
    }


def run_current_v02(features: pd.DataFrame, events: pd.DataFrame, cfg: dict) -> dict:
    valid = features.dropna(subset=["price"])
    query = valid.index.max()
    p = predict_at(features, events, cfg, query)

    return {
        "engine": cfg["engine"],
        "schema_version": cfg["schema_version"],
        "query_date": query.date().isoformat(),
        "layer": "A_FULL_HISTORY_PRICE_ONCHAIN_MACRO",
        "direction_case_share": {
            "up_case_share": p["up_case_share"],
            "down_case_share": p["down_case_share"],
        },
        "confidence": p["confidence"],
        "summary": p["summary"],
        "warning": (
            "Historical case rates are not calibrated probabilities. "
            "Derivatives/ETF layers remain excluded from full-history similarity."
        ),
    }


def price_only_frame(features: pd.DataFrame) -> pd.DataFrame:
    return features[
        [
            c for c in features.columns
            if c == "price"
            or c.startswith((
                "ret_",
                "vol_",
                "path_eff_",
                "dist_sma",
                "sma",
                "rsi14",
                "dd_from",
                "rebound_from",
            ))
        ]
    ].copy()


def price_only_cfg(cfg: dict) -> dict:
    x = json.loads(json.dumps(cfg))
    x["feature_group_weights"] = {
        "price_state": 0.35,
        "price_path": 0.65,
        "onchain_state": 0.0,
        "onchain_path": 0.0,
        "macro": 0.0,
    }
    return x


def walk_forward_v02(features: pd.DataFrame, events: pd.DataFrame, cfg: dict, step_days: int = 30) -> pd.DataFrame:
    start = max(
        features.index.min() + pd.Timedelta(days=365 * 3),
        pd.Timestamp("2016-01-01", tz="UTC"),
    )
    end = features.index.max() - pd.Timedelta(days=365)
    rows: list[dict] = []

    if end <= start:
        return pd.DataFrame()

    for q in pd.date_range(start, end, freq=f"{step_days}D"):
        idx = features.index[features.index <= q]
        if len(idx) == 0:
            continue
        qt = idx.max()

        try:
            p = predict_at(features, events, cfg, qt)
            actual = events.loc[qt, "fp_up30_vs_dn20"] if qt in events.index else None
            pred = p["pred"]
            correct = pred == actual if pred in ("UP30_FIRST", "DN20_FIRST") and actual in ("UP30_FIRST", "DN20_FIRST") else np.nan

            rows.append(
                {
                    "time": qt,
                    "pred": pred,
                    "actual": actual,
                    "confidence": p["confidence"]["score"],
                    "confidence_grade": p["confidence"]["grade"],
                    "majority_share_pct": p["confidence"].get("majority_share_pct"),
                    "wilson_lower_pct": p["confidence"].get("wilson_lower_pct"),
                    "analog_count": p["confidence"].get("analog_count"),
                    "episode_count": p["confidence"].get("episode_count"),
                    "distinct_years": p["confidence"].get("distinct_years"),
                    "correct": correct,
                }
            )
        except Exception:
            continue

    return pd.DataFrame(rows).set_index("time") if rows else pd.DataFrame()


def summarize_wf(wf: pd.DataFrame) -> dict:
    out = {"rows": int(len(wf))}
    if wf.empty:
        return out

    ev = wf[wf["correct"].notna()]
    high = ev[ev["confidence_grade"] == "HIGH"]
    medium_or_high = ev[ev["confidence_grade"].isin(["MEDIUM", "HIGH"])]

    out.update(
        {
            "evaluable": int(len(ev)),
            "overall_accuracy": round(float(ev["correct"].mean()) * 100, 2) if len(ev) else None,
            "high_confidence_count": int(len(high)),
            "high_confidence_accuracy": round(float(high["correct"].mean()) * 100, 2) if len(high) else None,
            "medium_or_high_count": int(len(medium_or_high)),
            "medium_or_high_accuracy": round(float(medium_or_high["correct"].mean()) * 100, 2) if len(medium_or_high) else None,
            "median_confidence_score": round(float(ev["confidence"].median()), 2) if len(ev) else None,
        }
    )
    return out


def domain_cfg(cfg: dict, domain: str) -> dict:
    c = json.loads(json.dumps(cfg))
    domain_groups = {
        "price": ["price_state", "price_path"],
        "onchain": ["onchain_state", "onchain_path"],
        "macro": ["macro"],
    }
    for group in domain_groups[domain]:
        c["feature_group_weights"][group] = 0.0
    return c


def ablation_current(features: pd.DataFrame, events: pd.DataFrame, cfg: dict) -> dict:
    query = features.dropna(subset=["price"]).index.max()
    base = predict_at(features, events, cfg, query)
    base_pred = base["pred"]

    results: dict[str, dict] = {}
    for domain in ("price", "onchain", "macro"):
        p = predict_at(features, events, domain_cfg(cfg, domain), query)
        results[domain] = {
            "pred": p["pred"],
            "up_case_share": p["up_case_share"],
            "confidence_score": p["confidence"]["score"],
            "confidence_grade": p["confidence"]["grade"],
            "direction_flip": (
                base_pred is not None
                and p["pred"] is not None
                and base_pred != p["pred"]
            ),
        }

    return {
        "query_date": query.date().isoformat(),
        "base_pred": base_pred,
        "base_up_case_share": base["up_case_share"],
        "base_confidence": base["confidence"],
        "ablations": results,
    }


def ablation_walk_forward(features: pd.DataFrame, events: pd.DataFrame, cfg: dict, wf: pd.DataFrame) -> dict:
    if wf.empty:
        return {"evaluated_points": 0, "domains": {}}

    query_times = list(wf.index[::2])
    domains: dict[str, dict] = {}

    for domain in ("price", "onchain", "macro"):
        c = domain_cfg(cfg, domain)
        flips = 0
        comparable = 0
        evaluable = 0
        correct = 0

        for qt in query_times:
            base_pred = wf.loc[qt, "pred"]
            actual = wf.loc[qt, "actual"]
            try:
                p = predict_at(features, events, c, qt)
                pred = p["pred"]
            except Exception:
                pred = None

            if base_pred in ("UP30_FIRST", "DN20_FIRST") and pred in ("UP30_FIRST", "DN20_FIRST"):
                comparable += 1
                if base_pred != pred:
                    flips += 1

            if pred in ("UP30_FIRST", "DN20_FIRST") and actual in ("UP30_FIRST", "DN20_FIRST"):
                evaluable += 1
                if pred == actual:
                    correct += 1

        domains[domain] = {
            "sample_points": len(query_times),
            "comparable_predictions": comparable,
            "direction_flip_count": flips,
            "direction_flip_rate_pct": round(flips / comparable * 100, 2) if comparable else None,
            "evaluable": evaluable,
            "accuracy": round(correct / evaluable * 100, 2) if evaluable else None,
        }

    return {"evaluated_points": len(query_times), "domains": domains}


def acceptance_decision(
    complex_summary: dict,
    price_summary: dict,
    ablation_summary: dict,
) -> dict:
    c_acc = complex_summary.get("overall_accuracy")
    p_acc = price_summary.get("overall_accuracy")
    delta = None if c_acc is None or p_acc is None else round(float(c_acc) - float(p_acc), 2)

    high_count = int(complex_summary.get("high_confidence_count") or 0)
    high_acc = complex_summary.get("high_confidence_accuracy")
    min_high_count = max(8, int(round((complex_summary.get("evaluable") or 0) * 0.08)))

    non_price_flip_rates = []
    for domain in ("onchain", "macro"):
        val = ablation_summary.get("domains", {}).get(domain, {}).get("direction_flip_rate_pct")
        if val is not None:
            non_price_flip_rates.append(float(val))
    max_non_price_flip = max(non_price_flip_rates) if non_price_flip_rates else None

    technical_pass = True
    statistical_pass = bool(
        delta is not None
        and delta >= 1.0
        and high_count >= min_high_count
        and high_acc is not None
        and p_acc is not None
        and float(high_acc) >= float(p_acc) + 2.0
        and max_non_price_flip is not None
        and max_non_price_flip <= 10.0
    )

    if statistical_pass:
        recommendation = "PASS_FOR_NEXT_LAYER_RESEARCH"
    else:
        recommendation = "KEEP_PRICE_CORE_AND_TREAT_ONCHAIN_MACRO_AS_CONFIRMATION_ONLY"

    return {
        "complex": complex_summary,
        "price_only": price_summary,
        "accuracy_delta_pp": delta,
        "high_confidence_min_required": min_high_count,
        "max_non_price_ablation_flip_rate_pct": max_non_price_flip,
        "technical_pass": technical_pass,
        "statistical_pass": statistical_pass,
        "recommendation": recommendation,
        "rule": (
            "Complex model must beat price-only OOS by at least 1pp, create enough HIGH-confidence "
            "cases that outperform price-only by at least 2pp, and keep non-price ablation flip rate <=10%."
        ),
    }


def main_fast() -> None:
    cfg = core.load_config()
    core.OUT.mkdir(parents=True, exist_ok=True)
    core.DATA.mkdir(parents=True, exist_ok=True)
    now = pd.Timestamp.now(tz="UTC").floor("D")

    cm, cm_fail = core.cm_fetch_asset_metrics(
        cfg["community_asset_metrics"],
        cfg["start_date"],
        now.date().isoformat(),
    )
    macro, macro_fail = core.treasury_history(
        pd.Timestamp(cfg["start_date"]).year,
        now.year,
    )

    raw = cm.join(macro, how="left") if not macro.empty else cm
    raw.to_csv(core.DATA / "historical_raw_daily.csv")

    features = build_features_v02(raw, cfg)
    features.to_csv(core.DATA / "historical_features_daily.csv")

    events = build_event_registry_fast(features["price"].dropna(), cfg)
    events.to_csv(core.DATA / "event_registry.csv")

    derived_cols = ["CapRealDerivedUSD", "MVRVZ_DERIVED_PIT", "RealizedPriceDerivedUSD", "dist_to_realized_price"]
    meta = {
        "generated_at_utc": core.datetime.now(core.UTC).isoformat(),
        "rows_raw": len(raw),
        "rows_features": len(features),
        "rows_events": len(events),
        "coinmetrics_failures": cm_fail,
        "treasury_failures_count": len(macro_fail),
        "available_columns": list(raw.columns),
        "derived_metric_non_null_counts": {
            c: int(features[c].notna().sum())
            for c in derived_cols
            if c in features.columns
        },
        "episode_rule": {
            "reversal_pct": cfg.get("episode_reversal_pct", 0.20),
            "max_days": cfg.get("episode_max_days", 365),
        },
    }
    (core.OUT / "build_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    current = run_current_v02(features, events, cfg)
    pframe = price_only_frame(features)
    pcfg = price_only_cfg(cfg)
    baseline_current = run_current_v02(pframe, events, pcfg)

    (core.OUT / "current_fractal.json").write_text(
        json.dumps(current, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (core.OUT / "current_price_only.json").write_text(
        json.dumps(baseline_current, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    current_ablation = ablation_current(features, events, cfg)
    (core.OUT / "ablation_current.json").write_text(
        json.dumps(current_ablation, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    wf = walk_forward_v02(features, events, cfg, step_days=30)
    wf.to_csv(core.OUT / "walk_forward.csv")
    bwf = walk_forward_v02(pframe, events, pcfg, step_days=30)
    bwf.to_csv(core.OUT / "walk_forward_price_only.csv")

    ablation_wf = ablation_walk_forward(features, events, cfg, wf)
    (core.OUT / "ablation_walk_forward_summary.json").write_text(
        json.dumps(ablation_wf, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    complex_summary = summarize_wf(wf)
    price_summary = summarize_wf(bwf)
    acceptance = acceptance_decision(complex_summary, price_summary, ablation_wf)

    (core.OUT / "walk_forward_summary.json").write_text(
        json.dumps(acceptance, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    decision = {
        "schema_version": cfg["schema_version"],
        "current_confidence": current["confidence"],
        "current_direction_case_share": current["direction_case_share"],
        "current_episode_count": current.get("summary", {}).get("episode_count"),
        "acceptance": acceptance,
        "next_step": (
            "BUILD_MODERN_AND_INSTITUTIONAL_LAYERS"
            if acceptance["statistical_pass"]
            else "DO_NOT_BUILD_NEXT_LAYERS_YET"
        ),
    }
    (core.OUT / "model_decision.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(meta, ensure_ascii=False))


core.build_features = build_features_v02
core.feature_groups = feature_groups_v02
core.build_event_registry = build_event_registry_fast
core.similarity_scores = similarity_scores_fast
core.confidence_score = confidence_score_v02
core.run_current = run_current_v02
core.walk_forward = walk_forward_v02


if __name__ == "__main__":
    main_fast()
