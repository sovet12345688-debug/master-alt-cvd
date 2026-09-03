from __future__ import annotations

import math
import numpy as np
import pandas as pd

from btc_fractal import run_v26_fractal as core


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
    out = pd.DataFrame({
        "similarity": score,
        "used_features": used_features,
        "coverage_weight": denominator,
    })
    for g, s in group_outputs.items():
        out[f"sim_{g}"] = s
    out = out[(out["used_features"] >= int(cfg["min_complete_features"])) & out["similarity"].notna()]
    return out.sort_values("similarity", ascending=False)


core.similarity_scores = similarity_scores_fast

if __name__ == "__main__":
    core.main()
