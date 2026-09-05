from __future__ import annotations

import argparse
import json
import math
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "btc_fractal"
OUT = BASE / "output"
DATA = BASE / "data"
CONFIG_PATH = BASE / "config.json"
CM_ASSET = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
TREASURY_XML = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
HEADERS = {"User-Agent": "master-btc-trend-fractal-v0.1"}


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def get_json(url: str, params: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
    last: Exception | None = None
    for i in range(3):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            if i < 2:
                time.sleep(0.8 * (i + 1))
    raise RuntimeError(str(last))


def cm_fetch_asset_metrics(metrics: list[str], start_date: str, end_date: str | None = None) -> tuple[pd.DataFrame, dict[str, str]]:
    """Fetch metrics one-by-one so a paid/unavailable metric cannot poison the whole request."""
    merged: pd.DataFrame | None = None
    failures: dict[str, str] = {}
    for metric in metrics:
        rows: list[dict[str, Any]] = []
        params: dict[str, Any] = {
            "assets": "btc",
            "metrics": metric,
            "frequency": "1d",
            "start_time": start_date,
            "page_size": 10000,
            "paging_from": "start",
        }
        if end_date:
            params["end_time"] = end_date
        url = CM_ASSET
        try:
            while True:
                doc = get_json(url, params=params if url == CM_ASSET else None)
                rows.extend(doc.get("data", []))
                nxt = doc.get("next_page_url")
                if not nxt:
                    break
                url = nxt.replace("https://api.coinmetrics.io/v4", "https://community-api.coinmetrics.io/v4")
                params = None
            if not rows:
                failures[metric] = "no_rows"
                continue
            frame = pd.DataFrame(rows)
            if metric not in frame.columns:
                failures[metric] = "metric_absent"
                continue
            frame["time"] = pd.to_datetime(frame["time"], utc=True, errors="coerce").dt.floor("D")
            frame[metric] = pd.to_numeric(frame[metric], errors="coerce")
            frame = frame[["time", metric]].dropna(subset=["time"]).drop_duplicates("time").set_index("time")
            merged = frame if merged is None else merged.join(frame, how="outer")
        except Exception as e:
            failures[metric] = f"{type(e).__name__}:{str(e)[:160]}"
    if merged is None or "PriceUSD" not in merged.columns:
        raise RuntimeError(f"PriceUSD unavailable; failures={failures}")
    return merged.sort_index(), failures


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def treasury_history(start_year: int, end_year: int) -> tuple[pd.DataFrame, dict[str, str]]:
    """Official Treasury daily yields. Used as market observations, not revised macro-release vintages."""
    specs = {
        "nominal": ("daily_treasury_yield_curve", {"US2Y": "BC_2YEAR", "US10Y": "BC_10YEAR", "US30Y": "BC_30YEAR"}),
        "real": ("daily_treasury_real_yield_curve", {"US10Y_REAL": "TC_10YEAR"}),
    }
    frames: list[pd.DataFrame] = []
    failures: dict[str, str] = {}
    for label, (data_key, fmap) in specs.items():
        all_rows: list[dict[str, Any]] = []
        for year in range(start_year, end_year + 1):
            try:
                r = requests.get(
                    TREASURY_XML,
                    params={"data": data_key, "field_tdr_date_value": str(year)},
                    headers=HEADERS,
                    timeout=30,
                )
                r.raise_for_status()
                root = ET.fromstring(r.content)
                for elem in root.iter():
                    if _local(elem.tag) != "properties":
                        continue
                    vals = {_local(c.tag): (c.text or "").strip() for c in list(elem)}
                    dt = pd.to_datetime(vals.get("NEW_DATE"), utc=True, errors="coerce")
                    if pd.isna(dt):
                        continue
                    row: dict[str, Any] = {"time": dt.floor("D")}
                    for metric, tag in fmap.items():
                        try:
                            row[metric] = float(vals.get(tag)) if vals.get(tag) not in (None, "") else np.nan
                        except Exception:
                            row[metric] = np.nan
                    all_rows.append(row)
            except Exception as e:
                failures[f"{label}:{year}"] = f"{type(e).__name__}:{str(e)[:120]}"
        if all_rows:
            frames.append(pd.DataFrame(all_rows).drop_duplicates("time").set_index("time").sort_index())
    if not frames:
        return pd.DataFrame(), failures
    out = frames[0]
    for f in frames[1:]:
        out = out.join(f, how="outer")
    if {"US2Y", "US10Y"}.issubset(out.columns):
        out["YC_10Y2Y"] = out["US10Y"] - out["US2Y"]
    return out, failures


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff(); up = d.clip(lower=0); dn = -d.clip(upper=0)
    au = up.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    ad = dn.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + au / ad.replace(0, np.nan))


def robust_z(s: pd.Series, window: int = 730, minp: int = 120) -> pd.Series:
    med = s.rolling(window, min_periods=minp).median()
    q1 = s.rolling(window, min_periods=minp).quantile(0.25)
    q3 = s.rolling(window, min_periods=minp).quantile(0.75)
    return (s - med) / (q3 - q1).replace(0, np.nan)


def build_features(raw: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    x = raw.copy().sort_index(); p = x["PriceUSD"].astype(float)
    f = pd.DataFrame(index=x.index); f["price"] = p
    for n in cfg["feature_windows_days"]:
        f[f"ret_{n}d"] = p.pct_change(n)
        f[f"vol_{n}d"] = p.pct_change().rolling(n, min_periods=max(5, n//3)).std(ddof=0) * np.sqrt(365)
        f[f"path_eff_{n}d"] = (p / p.shift(n) - 1) / p.pct_change().abs().rolling(n, min_periods=max(5, n//3)).sum().replace(0, np.nan)
    for n in [20, 50, 200]:
        ma = p.rolling(n, min_periods=max(10, n//2)).mean()
        f[f"dist_sma{n}"] = p / ma - 1
        if n in (50, 200):
            f[f"sma{n}_slope30"] = ma / ma.shift(30) - 1
    f["rsi14"] = rsi(p, 14)
    rolling_max = p.rolling(365, min_periods=60).max(); rolling_min = p.rolling(365, min_periods=60).min()
    f["dd_from_365d_high"] = p / rolling_max - 1
    f["rebound_from_365d_low"] = p / rolling_min - 1

    onchain = [c for c in ["CapMVRVCur", "CapMVRVZ", "SOPR", "CapRealUSD", "AdrActCnt", "TxCnt", "HashRate"] if c in x.columns]
    for c in onchain:
        f[c] = x[c]; f[f"{c}_z"] = robust_z(x[c])
        for n in [30, 90, 180]:
            f[f"{c}_chg_{n}d"] = x[c].pct_change(n) if c not in ("CapMVRVCur", "CapMVRVZ", "SOPR") else x[c] - x[c].shift(n)

    macro = [c for c in ["US2Y", "US10Y", "US30Y", "US10Y_REAL", "YC_10Y2Y"] if c in x.columns]
    for c in macro:
        f[c] = x[c].ffill(limit=7)
        f[f"{c}_chg30d"] = f[c] - f[c].shift(30)
        f[f"{c}_chg90d"] = f[c] - f[c].shift(90)
    return f.replace([np.inf, -np.inf], np.nan)


def first_hit_time(prices: pd.Series, anchor_pos: int, target_pct: float, max_days: int) -> int | None:
    base = float(prices.iloc[anchor_pos])
    if not math.isfinite(base) or base <= 0:
        return None
    end = min(len(prices) - 1, anchor_pos + max_days); future = prices.iloc[anchor_pos + 1:end + 1]
    if target_pct > 0:
        hits = np.flatnonzero((future / base - 1).to_numpy() >= target_pct / 100)
    else:
        hits = np.flatnonzero((future / base - 1).to_numpy() <= target_pct / 100)
    return int(hits[0] + 1) if len(hits) else None


def build_event_registry(price: pd.Series, cfg: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []; max_h = max(cfg["outcome_horizons_days"])
    all_targets = cfg["up_targets_pct"] + cfg["down_targets_pct"]
    for i in range(len(price) - max_h):
        base = float(price.iloc[i])
        if not math.isfinite(base) or base <= 0:
            continue
        row: dict[str, Any] = {"time": price.index[i], "anchor_price": base}
        for h in cfg["outcome_horizons_days"]:
            seg = price.iloc[i + 1:i + h + 1]; r = seg / base - 1
            row[f"mfe_{h}d"] = float(r.max()) if len(r) else np.nan
            row[f"mae_{h}d"] = float(r.min()) if len(r) else np.nan
            for t in all_targets:
                days = first_hit_time(price, i, t, h); key = f"hit_{'up' if t > 0 else 'dn'}{abs(t)}_{h}d"
                row[key] = days if days is not None else np.nan
        up30 = first_hit_time(price, i, 30, 365); dn20 = first_hit_time(price, i, -20, 365)
        if up30 is None and dn20 is None: row["fp_up30_vs_dn20"] = "NONE"
        elif dn20 is None or (up30 is not None and up30 < dn20): row["fp_up30_vs_dn20"] = "UP30_FIRST"
        elif up30 is None or dn20 < up30: row["fp_up30_vs_dn20"] = "DN20_FIRST"
        else: row["fp_up30_vs_dn20"] = "SAME_DAY"
        rows.append(row)
    return pd.DataFrame(rows).set_index("time") if rows else pd.DataFrame()


def feature_groups(cols: list[str]) -> dict[str, list[str]]:
    groups = {
        "price_state": [c for c in cols if c.startswith("dist_sma") or c in ["rsi14", "dd_from_365d_high", "rebound_from_365d_low"]],
        "price_path": [c for c in cols if c.startswith("ret_") or c.startswith("vol_") or c.startswith("path_eff_") or (c.startswith("sma") and "slope" in c)],
        "onchain_state": [c for c in cols if c in ["CapMVRVCur", "CapMVRVZ", "SOPR"] or c.endswith("_z")],
        "onchain_path": [c for c in cols if "_chg_" in c and not c.startswith(("US2Y", "US10Y", "US30Y", "US10Y_REAL", "YC_10Y2Y"))],
        "macro": [c for c in cols if c.startswith(("US2Y", "US10Y", "US30Y", "US10Y_REAL", "YC_10Y2Y"))],
    }
    return {k: v for k, v in groups.items() if v}


def robust_scale_from_history(hist: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    med = hist.median(axis=0, skipna=True); q1 = hist.quantile(0.25); q3 = hist.quantile(0.75)
    return med, (q3 - q1).replace(0, np.nan)


def similarity_scores(features: pd.DataFrame, query_time: pd.Timestamp, cfg: dict[str, Any], required_horizon: int = 365) -> pd.DataFrame:
    if query_time not in features.index:
        raise ValueError(f"query_time {query_time} missing")
    candidate_end = query_time - pd.Timedelta(days=required_horizon); hist = features.loc[:candidate_end].copy(); q = features.loc[query_time]
    numeric = [c for c in features.columns if c != "price"]; groups = feature_groups(numeric)
    med, scale = robust_scale_from_history(features.loc[:query_time, numeric]); qz = (q[numeric] - med) / scale; hz = (hist[numeric] - med) / scale
    rows = []; weights_cfg = cfg["feature_group_weights"]
    for t, r in hz.iterrows():
        group_sims: dict[str, float] = {}; used_weight = 0.0; weighted = 0.0; used_features = 0
        for g, cols in groups.items():
            valid = [c for c in cols if pd.notna(qz.get(c)) and pd.notna(r.get(c)) and pd.notna(scale.get(c))]
            if not valid: continue
            dist = float(np.nanmean(np.abs(r[valid].astype(float) - qz[valid].astype(float))))
            sim = 100.0 * math.exp(-0.65 * dist); w = float(weights_cfg.get(g, 0.0))
            if w <= 0: continue
            weighted += w * sim; used_weight += w; used_features += len(valid); group_sims[g] = sim
        if used_features < int(cfg["min_complete_features"]) or used_weight <= 0: continue
        rows.append({"time": t, "similarity": weighted / used_weight, "used_features": used_features, "coverage_weight": used_weight, **{f"sim_{k}": v for k, v in group_sims.items()}})
    if not rows: return pd.DataFrame()
    return pd.DataFrame(rows).set_index("time").sort_values("similarity", ascending=False)


def diversify_analogs(scores: pd.DataFrame, top_k: int, min_sep_days: int) -> pd.DataFrame:
    selected: list[pd.Timestamp] = []
    for t in scores.index:
        if all(abs((t - x).days) >= min_sep_days for x in selected):
            selected.append(t)
            if len(selected) >= top_k: break
    return scores.loc[selected].copy() if selected else pd.DataFrame()


def summarize_analogs(analogs: pd.DataFrame, events: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, Any]:
    if analogs.empty: return {"status": "NO_ANALOGS"}
    joined = analogs.join(events, how="left")
    out: dict[str, Any] = {"status": "OK", "analog_count": int(len(joined)), "distinct_years": int(joined.index.year.nunique()), "median_similarity": float(joined["similarity"].median()), "analogs": []}
    for t, r in joined.iterrows():
        out["analogs"].append({"date": t.date().isoformat(), "similarity": round(float(r["similarity"]), 2), "first_passage_up30_vs_dn20": r.get("fp_up30_vs_dn20"), "mfe_90d": None if pd.isna(r.get("mfe_90d")) else round(float(r.get("mfe_90d")) * 100, 2), "mae_90d": None if pd.isna(r.get("mae_90d")) else round(float(r.get("mae_90d")) * 100, 2)})
    fp = joined["fp_up30_vs_dn20"].dropna(); out["first_passage_counts"] = fp.value_counts().to_dict()
    for h in cfg["outcome_horizons_days"]:
        out[f"median_mfe_{h}d_pct"] = round(float(joined[f"mfe_{h}d"].median()) * 100, 2) if f"mfe_{h}d" in joined else None
        out[f"median_mae_{h}d_pct"] = round(float(joined[f"mae_{h}d"].median()) * 100, 2) if f"mae_{h}d" in joined else None
        for t in cfg["up_targets_pct"]:
            c = f"hit_up{t}_{h}d"
            if c in joined:
                out[f"up{t}_{h}d_case_rate"] = round(float(joined[c].notna().mean()) * 100, 1)
                out[f"up{t}_{h}d_median_days"] = round(float(joined[c].dropna().median()), 1) if joined[c].notna().any() else None
        for t in cfg["down_targets_pct"]:
            c = f"hit_dn{abs(t)}_{h}d"
            if c in joined:
                out[f"dn{abs(t)}_{h}d_case_rate"] = round(float(joined[c].notna().mean()) * 100, 1)
                out[f"dn{abs(t)}_{h}d_median_days"] = round(float(joined[c].dropna().median()), 1) if joined[c].notna().any() else None
    return out


def confidence_score(summary: dict[str, Any], analogs: pd.DataFrame) -> dict[str, Any]:
    if summary.get("status") != "OK" or analogs.empty: return {"score": 0, "grade": "LOW", "reasons": ["no valid analogs"]}
    n = int(summary.get("analog_count", 0)); years = int(summary.get("distinct_years", 0)); fp = summary.get("first_passage_counts", {})
    directional = int(fp.get("UP30_FIRST", 0)) + int(fp.get("DN20_FIRST", 0)); consensus = max(int(fp.get("UP30_FIRST", 0)), int(fp.get("DN20_FIRST", 0))) / directional if directional else 0.0
    sim = float(summary.get("median_similarity", 0)); feature_cov = float(analogs["coverage_weight"].median()) if "coverage_weight" in analogs else 0.0
    score = min(25, n / 12 * 25) + min(20, years / 5 * 20) + consensus * 25 + min(20, sim / 85 * 20) + min(10, feature_cov * 10)
    score = int(round(min(100, score))); grade = "HIGH" if score >= 75 and n >= 8 and years >= 3 else ("MEDIUM" if score >= 55 else "LOW")
    return {"score": score, "grade": grade, "consensus_pct": round(consensus * 100, 1), "analog_count": n, "distinct_years": years}


def run_current(features: pd.DataFrame, events: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, Any]:
    valid = features.dropna(subset=["price"]); query = valid.index.max(); scores = similarity_scores(features, query, cfg, required_horizon=365)
    analogs = diversify_analogs(scores, int(cfg["analog_top_k"]), int(cfg["analog_min_separation_days"])); summary = summarize_analogs(analogs, events, cfg); conf = confidence_score(summary, analogs)
    fp = summary.get("first_passage_counts", {}) if summary.get("status") == "OK" else {}; up = int(fp.get("UP30_FIRST", 0)); dn = int(fp.get("DN20_FIRST", 0)); denom = up + dn
    direction = {"up_case_share": round(up / denom * 100, 1) if denom else None, "down_case_share": round(dn / denom * 100, 1) if denom else None}
    return {"engine": cfg["engine"], "schema_version": cfg["schema_version"], "query_date": query.date().isoformat(), "layer": "A_FULL_HISTORY_PRICE_ONCHAIN_MACRO", "direction_case_share": direction, "confidence": conf, "summary": summary, "warning": "Historical case rates are not calibrated probabilities until walk-forward calibration passes. Derivatives/ETF layers are intentionally excluded from full-history similarity until comparable history exists."}


def price_only_baseline(features: pd.DataFrame, events: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, Any]:
    cfg2 = json.loads(json.dumps(cfg)); cfg2["feature_group_weights"] = {"price_state": 0.35, "price_path": 0.65, "onchain_state": 0, "onchain_path": 0, "macro": 0}
    cols = [c for c in features.columns if c == "price" or c.startswith(("ret_", "vol_", "path_eff_", "dist_sma", "sma", "rsi14", "dd_from", "rebound_from"))]
    return run_current(features[cols], events, cfg2)


def walk_forward(features: pd.DataFrame, events: pd.DataFrame, cfg: dict[str, Any], step_days: int = 30) -> pd.DataFrame:
    start = max(features.index.min() + pd.Timedelta(days=365*3), pd.Timestamp("2016-01-01", tz="UTC")); end = features.index.max() - pd.Timedelta(days=365)
    rows: list[dict[str, Any]] = []
    if end <= start: return pd.DataFrame()
    for q in pd.date_range(start, end, freq=f"{step_days}D"):
        idx = features.index[features.index <= q]
        if len(idx) == 0: continue
        qt = idx.max()
        try:
            scores = similarity_scores(features.loc[:qt], qt, cfg, required_horizon=365); analogs = diversify_analogs(scores, int(cfg["analog_top_k"]), int(cfg["analog_min_separation_days"])); summary = summarize_analogs(analogs, events, cfg); conf = confidence_score(summary, analogs)
            fp = summary.get("first_passage_counts", {}) if summary.get("status") == "OK" else {}; pred = "UP30_FIRST" if int(fp.get("UP30_FIRST", 0)) > int(fp.get("DN20_FIRST", 0)) else "DN20_FIRST"; actual = events.loc[qt, "fp_up30_vs_dn20"] if qt in events.index else None
            rows.append({"time": qt, "pred": pred, "actual": actual, "confidence": conf["score"], "analog_count": conf.get("analog_count"), "distinct_years": conf.get("distinct_years"), "correct": pred == actual if actual in ("UP30_FIRST", "DN20_FIRST") else np.nan})
        except Exception:
            continue
    return pd.DataFrame(rows).set_index("time") if rows else pd.DataFrame()


def main() -> None:
    ap = argparse.ArgumentParser(); ap.add_argument("--mode", choices=["build", "current", "validate", "all"], default="all"); args = ap.parse_args()
    cfg = load_config(); OUT.mkdir(parents=True, exist_ok=True); DATA.mkdir(parents=True, exist_ok=True); now = pd.Timestamp.now(tz="UTC").floor("D")
    cm, cm_fail = cm_fetch_asset_metrics(cfg["community_asset_metrics"], cfg["start_date"], now.date().isoformat()); macro, macro_fail = treasury_history(pd.Timestamp(cfg["start_date"]).year, now.year)
    raw = cm.join(macro, how="left") if not macro.empty else cm; raw.to_csv(DATA / "historical_raw_daily.csv"); features = build_features(raw, cfg); features.to_csv(DATA / "historical_features_daily.csv"); events = build_event_registry(features["price"].dropna(), cfg); events.to_csv(DATA / "event_registry.csv")
    meta = {"generated_at_utc": datetime.now(UTC).isoformat(), "rows_raw": len(raw), "rows_features": len(features), "rows_events": len(events), "coinmetrics_failures": cm_fail, "treasury_failures_count": len(macro_fail), "available_columns": list(raw.columns)}
    (OUT / "build_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.mode in ("current", "all"):
        current = run_current(features, events, cfg); baseline = price_only_baseline(features, events, cfg)
        (OUT / "current_fractal.json").write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"); (OUT / "current_price_only.json").write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.mode in ("validate", "all"):
        wf = walk_forward(features, events, cfg, step_days=30); wf.to_csv(OUT / "walk_forward.csv"); result: dict[str, Any] = {"rows": len(wf)}
        if not wf.empty:
            evaluable = wf[wf["correct"].notna()]; result["overall_accuracy"] = round(float(evaluable["correct"].mean()) * 100, 2) if len(evaluable) else None; high = evaluable[evaluable["confidence"] >= int(cfg["high_confidence_min_score"])]; result["high_confidence_count"] = len(high); result["high_confidence_accuracy"] = round(float(high["correct"].mean()) * 100, 2) if len(high) else None
        (OUT / "walk_forward_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
