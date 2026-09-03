from __future__ import annotations

import numpy as np
import pandas as pd

import run_v26_fractal as core


def build_event_registry_fast(price: pd.Series, cfg: dict) -> pd.DataFrame:
    """Vectorized forward-path matrix for all first-passage targets and MFE/MAE horizons."""
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

    out: dict[str, object] = {
        "time": idx[:usable_n],
        "anchor_price": base,
    }
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
            key = f"hit_{'up' if t > 0 else 'dn'}{abs(t)}_{h}d"
            out[key] = days

    up30 = cache.get((30, 365))
    dn20 = cache.get((-20, 365))
    fp = np.full(usable_n, "NONE", dtype=object)
    if up30 is not None and dn20 is not None:
        up_ok = np.isfinite(up30); dn_ok = np.isfinite(dn20)
        fp[up_ok & ~dn_ok] = "UP30_FIRST"
        fp[dn_ok & ~up_ok] = "DN20_FIRST"
        both = up_ok & dn_ok
        fp[both & (up30 < dn20)] = "UP30_FIRST"
        fp[both & (dn20 < up30)] = "DN20_FIRST"
        fp[both & (up30 == dn20)] = "SAME_DAY"
    out["fp_up30_vs_dn20"] = fp
    return pd.DataFrame(out).set_index("time")


def similarity_scores_fast(features: pd.DataFrame, query_time: pd.Timestamp, cfg: dict, required_horizon: int = 365) -> pd.DataFrame:
    if query_time not in features.index:
        raise ValueError(f"query_time {query_time} missing")
    candidate_end = query_time - pd.Timedelta(days=required_horizon)
    hist = features.loc[:candidate_end].copy()
    q = features.loc[query_time]
    numeric = [c for c in features.columns if c != "price"]
    groups = core.feature_groups(numeric)
    med, scale = core.robust_scale_from_history(features.loc[:query_time, numeric])
    valid_scale = scale.notna() & (scale != 0)
    qz = ((q[numeric] - med) / scale).where(valid_scale)
    hz = ((hist[numeric] - med) / scale).where(valid_scale)

    numerator = pd.Series(0.0, index=hist.index)
    denominator = pd.Series(0.0, index=hist.index)
    used_features = pd.Series(0, index=hist.index, dtype=float)
    group_outputs: dict[str, pd.Series] = {}

    for g, cols in groups.items():
        w = float(cfg["feature_group_weights"].get(g, 0.0))
        if w <= 0:
            continue
        usable_cols = [c for c in cols if c in hz.columns and pd.notna(qz.get(c))]
        if not usable_cols:
            continue
        diff = hz[usable_cols].sub(qz[usable_cols], axis=1).abs()
        cnt = diff.notna().sum(axis=1)
        dist = diff.mean(axis=1, skipna=True)
        sim = 100.0 * np.exp(-0.65 * dist)
        available = cnt > 0
        numerator = numerator.add(sim.where(available, 0.0) * w, fill_value=0.0)
        denominator = denominator.add(available.astype(float) * w, fill_value=0.0)
        used_features = used_features.add(cnt, fill_value=0.0)
        group_outputs[g] = sim.where(available)

    score = numerator / denominator.replace(0, np.nan)
    out = pd.DataFrame({"similarity": score, "used_features": used_features, "coverage_weight": denominator})
    for g, s in group_outputs.items():
        out[f"sim_{g}"] = s
    out = out[(out["used_features"] >= int(cfg["min_complete_features"])) & out["similarity"].notna()]
    return out.sort_values("similarity", ascending=False)


core.build_event_registry = build_event_registry_fast
core.similarity_scores = similarity_scores_fast

if __name__ == "__main__":
    core.main()
