from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import run_sr_strength_v01 as v01

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "btc_sr_strength"
OUT = BASE / "output"
OUT.mkdir(parents=True, exist_ok=True)

FEATURES = [
    "defense_norm",
    "log_touches",
    "log_age",
    "event_vol_log",
    "source_vol_log",
    "ma_dist",
    "slope",
    "zone_pct_feat",
]
MIN_TRAIN = {"4H": 150, "1D": 100, "1W": 50}


def _success_num(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s.astype(int)
    if pd.api.types.is_numeric_dtype(s):
        return pd.to_numeric(s, errors="coerce")
    return s.astype(str).map({"True": 1, "False": 0, "true": 1, "false": 0})


def prepare_features(events: pd.DataFrame, tf: str) -> pd.DataFrame:
    x = events.copy().sort_values("time")
    x["y"] = _success_num(x["success"])
    sec = {"4H": 4 * 3600, "1D": 24 * 3600, "1W": 7 * 24 * 3600}[tf]
    x["defense_norm"] = (x["close"].astype(float) / x["level"].astype(float) - 1).abs() / x["zone_pct"].replace(0, np.nan)
    x["pivot_age_bars"] = (pd.to_datetime(x["time"], utc=True) - pd.to_datetime(x["pivot_time"], utc=True)).dt.total_seconds() / sec
    x["log_touches"] = np.log1p(pd.to_numeric(x["touches_before"], errors="coerce").clip(lower=0))
    x["log_age"] = np.log1p(pd.to_numeric(x["pivot_age_bars"], errors="coerce").clip(lower=0))
    x["event_vol_log"] = np.log1p(pd.to_numeric(x["event_vol_ratio"], errors="coerce").clip(lower=0))
    x["source_vol_log"] = np.log1p(pd.to_numeric(x["source_vol_ratio"], errors="coerce").clip(lower=0))
    x["ma_dist"] = x["close"].astype(float) / pd.to_numeric(x["ma50"], errors="coerce") - 1
    x["slope"] = pd.to_numeric(x["ma50_slope"], errors="coerce")
    x["zone_pct_feat"] = pd.to_numeric(x["zone_pct"], errors="coerce")
    return x.replace([np.inf, -np.inf], np.nan)


def fit_model(train: pd.DataFrame):
    X = train[FEATURES].copy()
    med = X.median(numeric_only=True)
    X = X.fillna(med)
    m = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=1000))
    m.fit(X, train["y"].astype(int))
    return m, med


def walk_forward(events: pd.DataFrame, tf: str) -> pd.DataFrame:
    x = prepare_features(events, tf)
    out = []
    for side in ["SUPPORT", "RESISTANCE"]:
        z = x[(x["side"] == side) & x["y"].notna()].sort_values("time").copy()
        for year in sorted(pd.to_datetime(z["time"], utc=True).dt.year.unique()):
            test = z[pd.to_datetime(z["time"], utc=True).dt.year == year]
            train = z[pd.to_datetime(z["time"], utc=True).dt.year < year]
            # only matured history can train the model at the test period boundary
            year_start = pd.Timestamp(f"{int(year)}-01-01", tz="UTC")
            train = train[pd.to_datetime(train["mature_time"], utc=True, errors="coerce") < year_start]
            if len(train) < MIN_TRAIN[tf] or test.empty or train["y"].nunique() < 2:
                continue
            model, med = fit_model(train)
            Xtr = train[FEATURES].fillna(med)
            Xte = test[FEATURES].fillna(med)
            p_train = model.predict_proba(Xtr)[:, 1]
            p_test = model.predict_proba(Xte)[:, 1]
            scores = np.asarray([100.0 * np.mean(p_train <= p) for p in p_test])
            q = test[["tf", "time", "side", "level", "zone_pct", "success", "mature_time"]].copy()
            q["y"] = test["y"].astype(int).values
            q["model_p"] = p_test
            q["strength_score"] = np.round(scores, 2)
            out.append(q)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def _band(z: pd.DataFrame, lo: float, hi: float | None = None) -> dict:
    q = z[z["strength_score"] >= lo] if hi is None else z[(z["strength_score"] >= lo) & (z["strength_score"] < hi)]
    return {"count": int(len(q)), "success_pct": None if q.empty else round(float(q["y"].mean()) * 100, 2)}


def validation_stats(scored: pd.DataFrame, tf: str, side: str) -> dict:
    z = scored[(scored["side"] == side) & scored["y"].notna()].copy()
    low, mid, high = _band(z, 0, 50), _band(z, 50, 80), _band(z, 80, None)
    overall = None if z.empty else round(float(z["y"].mean()) * 100, 2)
    gain = None if overall is None or high["success_pct"] is None else round(high["success_pct"] - overall, 2)
    corr = None if len(z) < 8 else round(float(z[["strength_score", "y"]].corr(method="spearman").iloc[0, 1]), 4)
    high_years = int(pd.to_datetime(z[z["strength_score"] >= 80]["time"], utc=True).dt.year.nunique()) if not z.empty else 0
    modern = z[pd.to_datetime(z["time"], utc=True).dt.year >= 2024]
    mhigh = modern[modern["strength_score"] >= 80]
    modern_base = None if modern.empty else round(float(modern["y"].mean()) * 100, 2)
    modern_high = None if mhigh.empty else round(float(mhigh["y"].mean()) * 100, 2)
    modern_gain = None if modern_base is None or modern_high is None else round(modern_high - modern_base, 2)
    monotonic = all(v is not None for v in [low["success_pct"], mid["success_pct"], high["success_pct"]]) and high["success_pct"] >= mid["success_pct"] and mid["success_pct"] >= low["success_pct"]
    gates = {
        "case_gate": len(z) >= 50,
        "high_case_gate": high["count"] >= 10,
        "high_gain_gate_10pp": gain is not None and gain >= 10.0,
        "rank_corr_gate": corr is not None and corr >= 0.10,
        "monotonic_gate": bool(monotonic),
        "cross_year_gate": high_years >= 3,
        "modern_2024_plus_high_case_gate": len(mhigh) >= (20 if tf in ["4H", "1D"] else 5),
        "modern_2024_plus_gain_gate_10pp": modern_gain is not None and modern_gain >= 10.0,
        "modern_2024_plus_high_rate_gate": modern_high is not None and modern_high >= 50.0,
    }
    return {
        "count": int(len(z)),
        "overall_success_pct": overall,
        "bands": {"LOW_0_49": low, "MID_50_79": mid, "HIGH_80_100": high},
        "high_gain_vs_all_pp": gain,
        "spearman_score_vs_success": corr,
        "high_distinct_years": high_years,
        "modern_2024_plus": {"count": int(len(modern)), "base_success_pct": modern_base, "high_count": int(len(mhigh)), "high_success_pct": modern_high, "high_gain_pp": modern_gain},
        "gates": gates,
        "pass": all(gates.values()),
    }


def nearest_pivot_row(df: pd.DataFrame, piv: pd.DataFrame, tf: str, side: str):
    i = len(df) - 1
    t = df.index[i]
    z = piv[(piv["type"] == side) & (piv["known_i"] <= i) & (piv["pivot_i"] >= max(0, i-v01.CFG[tf]["lookback"]))].copy()
    if z.empty:
        return None
    cl = float(df["close"].iloc[i])
    if side == "SUPPORT":
        z = z[z["level"] <= cl]
    else:
        z = z[z["level"] >= cl]
    if z.empty:
        return None
    cand = z.iloc[int(np.argmin(np.abs(np.log(cl / z["level"].to_numpy(float)))))]
    return cand


def current_score(df: pd.DataFrame, piv: pd.DataFrame, events: pd.DataFrame, tf: str, side: str) -> dict | None:
    cand = nearest_pivot_row(df, piv, tf, side)
    if cand is None:
        return None
    i = len(df) - 1
    t = df.index[i]
    lvl = float(cand["level"])
    tol = v01.zone_pct(df, i, tf)
    cl = float(df["close"].iloc[i])
    distance_ratio = abs(cl / lvl - 1) / tol
    # Model was validated on completed touch/reaction bars. Do not project it onto untouched distant levels.
    if distance_ratio > 1.5:
        return {"level": round(lvl, 2), "zone_low": round(lvl*(1-tol), 2), "zone_high": round(lvl*(1+tol), 2), "strength_score": None, "status": "AWAIT_TOUCH_CONFIRMATION", "distance_zone_units": round(distance_ratio, 2)}

    x = prepare_features(events, tf)
    train = x[(x["side"] == side) & x["y"].notna() & (pd.to_datetime(x["mature_time"], utc=True, errors="coerce") < t)].copy()
    if len(train) < MIN_TRAIN[tf] or train["y"].nunique() < 2:
        return {"level": round(lvl, 2), "zone_low": round(lvl*(1-tol), 2), "zone_high": round(lvl*(1+tol), 2), "strength_score": None, "status": "INSUFFICIENT_HISTORY"}

    prior = events[(events["side"] == side) & (pd.to_datetime(events["time"], utc=True) < t)]
    same = prior[np.abs(prior["level"].astype(float) / lvl - 1) <= 2 * tol]
    touches = int(len(same))
    row = pd.DataFrame([{
        "tf": tf, "time": t, "side": side, "level": lvl, "zone_pct": tol, "pivot_time": cand["pivot_time"], "known_time": cand["known_time"],
        "source_vol_ratio": cand["source_vol_ratio"], "event_vol_ratio": float(df["vol_ratio"].iloc[i]) if pd.notna(df["vol_ratio"].iloc[i]) else np.nan,
        "close": cl, "ma50": float(df["ma50"].iloc[i]) if pd.notna(df["ma50"].iloc[i]) else np.nan,
        "ma50_slope": float(df["ma50_slope"].iloc[i]) if pd.notna(df["ma50_slope"].iloc[i]) else np.nan,
        "success": np.nan, "fav_atr": np.nan, "mature_time": pd.NaT, "touches_before": touches, "raw_score": np.nan,
    }])
    obs = prepare_features(row, tf)
    model, med = fit_model(train)
    Xtr = train[FEATURES].fillna(med)
    Xobs = obs[FEATURES].fillna(med)
    p_train = model.predict_proba(Xtr)[:, 1]
    p = float(model.predict_proba(Xobs)[:, 1][0])
    score = round(100.0 * float(np.mean(p_train <= p)), 2)
    return {
        "level": round(lvl, 2), "zone_low": round(lvl*(1-tol), 2), "zone_high": round(lvl*(1+tol), 2),
        "strength_score": score, "status": "REACTION_CONFIRMED_MODEL", "distance_zone_units": round(distance_ratio, 2),
        "touches_before": touches,
    }


def main() -> None:
    end = pd.Timestamp.now(tz="UTC").floor("h")
    d4 = v01.add_indicators(v01.fetch_binance("4h", v01.START, end))
    d1 = v01.add_indicators(v01.fetch_binance("1d", v01.START, end))
    w1 = v01.add_indicators(v01.to_weekly(d1))
    frames = {"4H": d4, "1D": d1, "1W": w1}
    piv = {tf: v01.pivots(df, tf) for tf, df in frames.items()}
    events = {}
    scored = {}
    validation = {}
    current = {}
    for tf, df in frames.items():
        ev = v01.generate_events(df, piv[tf], tf)
        ev = v01.score_events(ev, piv[tf], piv, tf)
        events[tf] = ev
        sc = walk_forward(ev, tf)
        scored[tf] = sc
        sc.to_csv(OUT / f"events_{tf.lower()}_v02_scored.csv", index=False)
        validation[tf] = {side: validation_stats(sc, tf, side) for side in ["SUPPORT", "RESISTANCE"]}
        current[tf] = {side: current_score(df, piv[tf], ev, tf, side) for side in ["SUPPORT", "RESISTANCE"]}

    core = [validation[tf][side]["pass"] for tf in ["4H", "1D"] for side in ["SUPPORT", "RESISTANCE"]]
    passed = int(sum(core))
    deployment = "PASS_READ_ONLY" if passed == 4 else "PARTIAL_READ_ONLY" if passed >= 2 else "RESEARCH_ONLY"
    summary = {
        "engine": "BTC_SR_STRENGTH_V0_2",
        "schema_version": "0.2",
        "query_time_utc": end.isoformat(),
        "architecture": "POINT_IN_TIME_LEVEL_FEATURES + COMPLETED_TOUCH_DEFENSE/REJECTION + EXPANDING_YEAR WALK_FORWARD LOGISTIC + TRAIN_DISTRIBUTION_PERCENTILE_SCORE",
        "score_meaning": "0-100 relative reaction-strength rank among historically known signals. NOT a probability. Score requires a completed touch/reaction context; distant untouched zones remain unscored.",
        "features": FEATURES,
        "anti_leakage": ["pivot usable only after confirmation", "each test year trained only on earlier matured events", "test score percentile uses training prediction distribution", "current score forbidden when zone has not been touched/approached"],
        "validation": validation,
        "core_4h_1d_passes": passed,
        "deployment": deployment,
        "current_levels": current,
        "master_integration_rule": "If deployment PASS_READ_ONLY, SCREEN1 may show S/R strength only when the MASTER-detected zone overlaps the engine zone AND current status=REACTION_CONFIRMED_MODEL. Otherwise display '강도 대기'. Never change Entry/Safety/NonChase/SL/RR by score alone.",
    }
    (OUT / "sr_strength_v02_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
