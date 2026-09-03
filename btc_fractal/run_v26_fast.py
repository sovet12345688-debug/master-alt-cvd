from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import run_v26_fractal as core


def build_event_registry_fast(price: pd.Series, cfg: dict) -> pd.DataFrame:
    price = price.dropna().astype(float)
    arr = price.to_numpy(dtype=float); idx = price.index; n = len(arr)
    max_h = int(max(cfg["outcome_horizons_days"])); usable_n = max(0, n - max_h)
    if usable_n <= 0: return pd.DataFrame()
    base = arr[:usable_n]
    future_ret = np.full((usable_n, max_h), np.nan, dtype=float)
    for d in range(1, max_h + 1):
        future_ret[:, d - 1] = arr[d:d + usable_n] / base - 1.0
    out: dict[str, object] = {"time": idx[:usable_n], "anchor_price": base}
    targets = [int(x) for x in cfg["up_targets_pct"]] + [int(x) for x in cfg["down_targets_pct"]]
    def first_days(mask: np.ndarray) -> np.ndarray:
        any_hit = mask.any(axis=1); first = np.argmax(mask, axis=1).astype(float) + 1.0; first[~any_hit] = np.nan; return first
    cache: dict[tuple[int, int], np.ndarray] = {}
    for h in [int(x) for x in cfg["outcome_horizons_days"]]:
        block = future_ret[:, :h]; out[f"mfe_{h}d"] = np.nanmax(block, axis=1); out[f"mae_{h}d"] = np.nanmin(block, axis=1)
        for t in targets:
            mask = block >= t / 100.0 if t > 0 else block <= t / 100.0
            days = first_days(mask); cache[(t, h)] = days
            out[f"hit_{'up' if t > 0 else 'dn'}{abs(t)}_{h}d"] = days
    up30 = cache.get((30, 365)); dn20 = cache.get((-20, 365)); fp = np.full(usable_n, "NONE", dtype=object)
    if up30 is not None and dn20 is not None:
        up_ok = np.isfinite(up30); dn_ok = np.isfinite(dn20); fp[up_ok & ~dn_ok] = "UP30_FIRST"; fp[dn_ok & ~up_ok] = "DN20_FIRST"
        both = up_ok & dn_ok; fp[both & (up30 < dn20)] = "UP30_FIRST"; fp[both & (dn20 < up30)] = "DN20_FIRST"; fp[both & (up30 == dn20)] = "SAME_DAY"
    out["fp_up30_vs_dn20"] = fp
    return pd.DataFrame(out).set_index("time")


def similarity_scores_fast(features: pd.DataFrame, query_time: pd.Timestamp, cfg: dict, required_horizon: int = 365) -> pd.DataFrame:
    if query_time not in features.index: raise ValueError(f"query_time {query_time} missing")
    candidate_end = query_time - pd.Timedelta(days=required_horizon); hist = features.loc[:candidate_end].copy(); q = features.loc[query_time]
    numeric = [c for c in features.columns if c != "price"]; groups = core.feature_groups(numeric)
    med, scale = core.robust_scale_from_history(features.loc[:query_time, numeric]); valid_scale = scale.notna() & (scale != 0)
    qz = (q[numeric] - med) / scale; qz.loc[~valid_scale] = np.nan
    hz = (hist[numeric] - med) / scale
    invalid_cols = [c for c in hz.columns if not bool(valid_scale.get(c, False))]
    if invalid_cols: hz.loc[:, invalid_cols] = np.nan
    numerator = pd.Series(0.0, index=hist.index); denominator = pd.Series(0.0, index=hist.index); used_features = pd.Series(0, index=hist.index, dtype=float); group_outputs: dict[str, pd.Series] = {}
    for g, cols in groups.items():
        w = float(cfg["feature_group_weights"].get(g, 0.0))
        if w <= 0: continue
        usable_cols = [c for c in cols if c in hz.columns and pd.notna(qz.get(c))]
        if not usable_cols: continue
        diff = hz[usable_cols].sub(qz[usable_cols], axis=1).abs(); cnt = diff.notna().sum(axis=1); dist = diff.mean(axis=1, skipna=True); sim = 100.0 * np.exp(-0.65 * dist); available = cnt > 0
        numerator = numerator.add(sim.where(available, 0.0) * w, fill_value=0.0); denominator = denominator.add(available.astype(float) * w, fill_value=0.0); used_features = used_features.add(cnt, fill_value=0.0); group_outputs[g] = sim.where(available)
    score = numerator / denominator.replace(0, np.nan); out = pd.DataFrame({"similarity": score, "used_features": used_features, "coverage_weight": denominator})
    for g, s in group_outputs.items(): out[f"sim_{g}"] = s
    out = out[(out["used_features"] >= int(cfg["min_complete_features"])) & out["similarity"].notna()]
    return out.sort_values("similarity", ascending=False)


def price_only_frame(features: pd.DataFrame) -> pd.DataFrame:
    return features[[c for c in features.columns if c == "price" or c.startswith(("ret_", "vol_", "path_eff_", "dist_sma", "sma", "rsi14", "dd_from", "rebound_from"))]].copy()


def price_only_cfg(cfg: dict) -> dict:
    x = json.loads(json.dumps(cfg)); x["feature_group_weights"] = {"price_state": 0.35, "price_path": 0.65, "onchain_state": 0, "onchain_path": 0, "macro": 0}; return x


def summarize_wf(wf: pd.DataFrame, high_threshold: int) -> dict:
    out = {"rows": int(len(wf))}
    if wf.empty: return out
    ev = wf[wf["correct"].notna()]; high = ev[ev["confidence"] >= high_threshold]
    out.update({"evaluable": int(len(ev)), "overall_accuracy": round(float(ev["correct"].mean()) * 100, 2) if len(ev) else None, "high_confidence_count": int(len(high)), "high_confidence_accuracy": round(float(high["correct"].mean()) * 100, 2) if len(high) else None})
    return out


def ablation_current(features: pd.DataFrame, events: pd.DataFrame, cfg: dict) -> dict:
    base = core.run_current(features, events, cfg); base_up = base.get("direction_case_share", {}).get("up_case_share")
    results = {}
    for group in ["onchain_state", "onchain_path", "macro"]:
        c = json.loads(json.dumps(cfg)); c["feature_group_weights"][group] = 0
        r = core.run_current(features, events, c); up = r.get("direction_case_share", {}).get("up_case_share")
        results[group] = {"up_case_share": up, "direction_flip": (base_up is not None and up is not None and (base_up >= 50) != (up >= 50))}
    return {"base_up_case_share": base_up, "ablations": results}


core.build_event_registry = build_event_registry_fast
core.similarity_scores = similarity_scores_fast


def main_fast() -> None:
    cfg = core.load_config(); core.OUT.mkdir(parents=True, exist_ok=True); core.DATA.mkdir(parents=True, exist_ok=True); now = pd.Timestamp.now(tz="UTC").floor("D")
    cm, cm_fail = core.cm_fetch_asset_metrics(cfg["community_asset_metrics"], cfg["start_date"], now.date().isoformat()); macro, macro_fail = core.treasury_history(pd.Timestamp(cfg["start_date"]).year, now.year)
    raw = cm.join(macro, how="left") if not macro.empty else cm; raw.to_csv(core.DATA / "historical_raw_daily.csv"); features = core.build_features(raw, cfg); features.to_csv(core.DATA / "historical_features_daily.csv"); events = build_event_registry_fast(features["price"].dropna(), cfg); events.to_csv(core.DATA / "event_registry.csv")
    meta = {"generated_at_utc": core.datetime.now(core.UTC).isoformat(), "rows_raw": len(raw), "rows_features": len(features), "rows_events": len(events), "coinmetrics_failures": cm_fail, "treasury_failures_count": len(macro_fail), "available_columns": list(raw.columns)}
    (core.OUT / "build_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    current = core.run_current(features, events, cfg); pframe = price_only_frame(features); pcfg = price_only_cfg(cfg); baseline_current = core.run_current(pframe, events, pcfg)
    (core.OUT / "current_fractal.json").write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"); (core.OUT / "current_price_only.json").write_text(json.dumps(baseline_current, ensure_ascii=False, indent=2), encoding="utf-8")
    wf = core.walk_forward(features, events, cfg, step_days=30); wf.to_csv(core.OUT / "walk_forward.csv"); bwf = core.walk_forward(pframe, events, pcfg, step_days=30); bwf.to_csv(core.OUT / "walk_forward_price_only.csv")
    cs = summarize_wf(wf, int(cfg["high_confidence_min_score"])); bs = summarize_wf(bwf, int(cfg["high_confidence_min_score"])); delta = None if cs.get("overall_accuracy") is None or bs.get("overall_accuracy") is None else round(cs["overall_accuracy"] - bs["overall_accuracy"], 2)
    acceptance = {"complex": cs, "price_only": bs, "accuracy_delta_pp": delta, "technical_pass": current.get("summary", {}).get("status") == "OK", "statistical_pass": bool(delta is not None and delta > 0 and cs.get("high_confidence_count", 0) > 0), "rule": "Complex model must improve price-only OOS and produce usable high-confidence cases before MASTER integration."}
    (core.OUT / "walk_forward_summary.json").write_text(json.dumps(acceptance, ensure_ascii=False, indent=2), encoding="utf-8"); (core.OUT / "ablation_current.json").write_text(json.dumps(ablation_current(features, events, cfg), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main_fast()
