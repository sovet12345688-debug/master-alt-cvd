from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "btc_sr_strength"
OUT = BASE / "output"
OUT.mkdir(parents=True, exist_ok=True)

BASE_URLS = [
    "https://data-api.binance.vision/api/v3/klines",
    "https://api.binance.com/api/v3/klines",
]
SYMBOL = "BTCUSDT"
START = pd.Timestamp("2018-01-01", tz="UTC")

CFG = {
    "4H": {"interval": "4h", "ms": 4 * 3600 * 1000, "pivot_span": 6, "lookback": 540, "horizon": 18, "min_zone": 0.006, "max_zone": 0.018, "sep": 12},
    "1D": {"interval": "1d", "ms": 24 * 3600 * 1000, "pivot_span": 3, "lookback": 365, "horizon": 15, "min_zone": 0.010, "max_zone": 0.030, "sep": 5},
    "1W": {"interval": None, "ms": 7 * 24 * 3600 * 1000, "pivot_span": 2, "lookback": 104, "horizon": 8, "min_zone": 0.020, "max_zone": 0.050, "sep": 3},
}


def fetch_binance(interval: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    rows: list[list] = []
    cursor = start_ms
    last_error = None
    while cursor < end_ms:
        data = None
        for base in BASE_URLS:
            try:
                r = requests.get(base, params={"symbol": SYMBOL, "interval": interval, "startTime": cursor, "endTime": end_ms, "limit": 1000}, timeout=25)
                r.raise_for_status()
                data = r.json()
                if isinstance(data, list):
                    break
            except Exception as e:
                last_error = e
                data = None
        if not data:
            break
        rows.extend(data)
        nxt = int(data[-1][0]) + 1
        if nxt <= cursor:
            break
        cursor = nxt
        time.sleep(0.03)
    if not rows:
        raise RuntimeError(f"Binance {interval} unavailable: {last_error}")
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades", "taker_base", "taker_quote", "ignore"]
    df = pd.DataFrame(rows, columns=cols).drop_duplicates("open_time")
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.set_index("time")[["open", "high", "low", "close", "volume", "quote_volume"]].sort_index()


def to_weekly(d: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "open": d["open"].resample("W-SUN").first(),
        "high": d["high"].resample("W-SUN").max(),
        "low": d["low"].resample("W-SUN").min(),
        "close": d["close"].resample("W-SUN").last(),
        "volume": d["volume"].resample("W-SUN").sum(),
        "quote_volume": d["quote_volume"].resample("W-SUN").sum(),
    }).dropna()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    prev = x["close"].shift(1)
    tr = pd.concat([(x["high"] - x["low"]), (x["high"] - prev).abs(), (x["low"] - prev).abs()], axis=1).max(axis=1)
    x["atr14"] = tr.rolling(14, min_periods=8).mean()
    x["ma50"] = x["close"].rolling(50, min_periods=30).mean()
    x["ma50_slope"] = x["ma50"] / x["ma50"].shift(10) - 1
    x["vol_med20"] = x["quote_volume"].rolling(20, min_periods=10).median()
    x["vol_ratio"] = x["quote_volume"] / x["vol_med20"].replace(0, np.nan)
    return x


def pivots(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    span = CFG[tf]["pivot_span"]
    n = 2 * span + 1
    low_roll = df["low"].rolling(n, center=True, min_periods=n).min()
    high_roll = df["high"].rolling(n, center=True, min_periods=n).max()
    lows = np.flatnonzero(np.isclose(df["low"].to_numpy(), low_roll.to_numpy(), rtol=1e-10, atol=1e-12, equal_nan=False))
    highs = np.flatnonzero(np.isclose(df["high"].to_numpy(), high_roll.to_numpy(), rtol=1e-10, atol=1e-12, equal_nan=False))
    rows = []
    for typ, arr, col in [("SUPPORT", lows, "low"), ("RESISTANCE", highs, "high")]:
        for i in arr:
            known = i + span
            if known >= len(df):
                continue
            atr = float(df["atr14"].iloc[i]) if pd.notna(df["atr14"].iloc[i]) else np.nan
            price = float(df[col].iloc[i])
            vr = float(df["vol_ratio"].iloc[i]) if pd.notna(df["vol_ratio"].iloc[i]) else np.nan
            prominence = np.nan
            if math.isfinite(atr) and atr > 0:
                window = df.iloc[max(0, i-span):min(len(df), i+span+1)]
                if typ == "SUPPORT":
                    prominence = float((window["high"].max() - price) / atr)
                else:
                    prominence = float((price - window["low"].min()) / atr)
            rows.append({"pivot_i": int(i), "known_i": int(known), "pivot_time": df.index[i], "known_time": df.index[known], "type": typ, "level": price, "source_vol_ratio": vr, "prominence_atr": prominence})
    return pd.DataFrame(rows).sort_values(["known_i", "pivot_i"]).reset_index(drop=True)


def zone_pct(df: pd.DataFrame, i: int, tf: str) -> float:
    c = float(df["close"].iloc[i])
    atr = float(df["atr14"].iloc[i]) if pd.notna(df["atr14"].iloc[i]) else np.nan
    base = 0.012 if not math.isfinite(atr) or atr <= 0 or c <= 0 else 0.35 * atr / c
    return float(np.clip(base, CFG[tf]["min_zone"], CFG[tf]["max_zone"]))


def choose_candidate(df: pd.DataFrame, p: pd.DataFrame, i: int, tf: str, side: str) -> pd.Series | None:
    if p.empty:
        return None
    lookback = CFG[tf]["lookback"]
    typ = "SUPPORT" if side == "SUPPORT" else "RESISTANCE"
    z = p[(p["type"] == typ) & (p["known_i"] <= i) & (p["pivot_i"] >= max(0, i-lookback))]
    if z.empty:
        return None
    tol = zone_pct(df, i, tf)
    lo, hi, cl = map(float, [df["low"].iloc[i], df["high"].iloc[i], df["close"].iloc[i]])
    levels = z["level"].to_numpy(float)
    if side == "SUPPORT":
        mask = (lo <= levels * (1 + tol)) & (hi >= levels * (1 - tol)) & (cl >= levels * (1 - 0.75 * tol))
    else:
        mask = (hi >= levels * (1 - tol)) & (lo <= levels * (1 + tol)) & (cl <= levels * (1 + 0.75 * tol))
    z = z.loc[mask]
    if z.empty:
        return None
    dist = np.abs(np.log(cl / z["level"].to_numpy(float)))
    return z.iloc[int(np.argmin(dist))]


def outcome(df: pd.DataFrame, i: int, level: float, side: str, tf: str) -> tuple[bool | None, float | None, pd.Timestamp | None]:
    h = CFG[tf]["horizon"]
    if i + h >= len(df):
        return None, None, None
    atr = float(df["atr14"].iloc[i]) if pd.notna(df["atr14"].iloc[i]) else np.nan
    if not math.isfinite(atr) or atr <= 0:
        return None, None, None
    target_mult = 1.5
    break_mult = 0.75
    seg = df.iloc[i+1:i+h+1]
    if side == "SUPPORT":
        target = level + target_mult * atr
        fail = level - break_mult * atr
        fav = float((seg["high"].max() - level) / atr)
        for t, r in seg.iterrows():
            hit_fail = float(r["low"]) <= fail
            hit_target = float(r["high"]) >= target
            if hit_fail and hit_target:
                return False, fav, t
            if hit_fail:
                return False, fav, t
            if hit_target:
                return True, fav, t
    else:
        target = level - target_mult * atr
        fail = level + break_mult * atr
        fav = float((level - seg["low"].min()) / atr)
        for t, r in seg.iterrows():
            hit_fail = float(r["high"]) >= fail
            hit_target = float(r["low"]) <= target
            if hit_fail and hit_target:
                return False, fav, t
            if hit_fail:
                return False, fav, t
            if hit_target:
                return True, fav, t
    return False, fav, seg.index[-1]


def generate_events(df: pd.DataFrame, p: pd.DataFrame, tf: str) -> pd.DataFrame:
    rows = []
    last: dict[str, tuple[int, float] | None] = {"SUPPORT": None, "RESISTANCE": None}
    start_i = max(70, CFG[tf]["pivot_span"] * 3)
    for i in range(start_i, len(df)):
        for side in ["SUPPORT", "RESISTANCE"]:
            cand = choose_candidate(df, p, i, tf, side)
            if cand is None:
                continue
            lvl = float(cand["level"])
            tol = zone_pct(df, i, tf)
            prior = last[side]
            if prior is not None:
                pi, pl = prior
                if i - pi < CFG[tf]["sep"] and abs(lvl / pl - 1) <= 2 * tol:
                    continue
            ok, fav, mature_t = outcome(df, i, lvl, side, tf)
            rows.append({
                "tf": tf, "time": df.index[i], "i": i, "side": side, "level": lvl, "zone_pct": tol,
                "pivot_time": cand["pivot_time"], "known_time": cand["known_time"], "source_vol_ratio": cand["source_vol_ratio"],
                "event_vol_ratio": float(df["vol_ratio"].iloc[i]) if pd.notna(df["vol_ratio"].iloc[i]) else np.nan,
                "close": float(df["close"].iloc[i]), "ma50": float(df["ma50"].iloc[i]) if pd.notna(df["ma50"].iloc[i]) else np.nan,
                "ma50_slope": float(df["ma50_slope"].iloc[i]) if pd.notna(df["ma50_slope"].iloc[i]) else np.nan,
                "success": ok, "fav_atr": fav, "mature_time": mature_t,
            })
            last[side] = (i, lvl)
    return pd.DataFrame(rows)


def near_pivot_score(all_pivots: dict[str, pd.DataFrame], tf: str, t: pd.Timestamp, level: float, tol: float) -> float:
    others = [x for x in all_pivots if x != tf]
    hits = 0
    for otf in others:
        p = all_pivots[otf]
        z = p[(p["known_time"] <= t) & (p["pivot_time"] >= t - pd.Timedelta(days=730))]
        if not z.empty and np.any(np.abs(z["level"].to_numpy(float) / level - 1) <= 1.5 * tol):
            hits += 1
    return min(20.0, 10.0 * hits)


def role_flip_score(p: pd.DataFrame, t: pd.Timestamp, side: str, level: float, tol: float) -> float:
    opp = "RESISTANCE" if side == "SUPPORT" else "SUPPORT"
    z = p[(p["type"] == opp) & (p["known_time"] < t) & (p["pivot_time"] >= t - pd.Timedelta(days=730))]
    if z.empty:
        return 0.0
    return 15.0 if np.any(np.abs(z["level"].to_numpy(float) / level - 1) <= 1.5 * tol) else 0.0


def volume_score(a: float | None, b: float | None) -> float | None:
    vals = [float(x) for x in [a, b] if x is not None and pd.notna(x) and math.isfinite(float(x))]
    if not vals:
        return None
    r = max(vals)
    if r < 0.75:
        return 3.0
    if r < 1.0:
        return 6.0
    if r < 1.5:
        return 10.0
    return 15.0


def structure_score(row: pd.Series) -> float | None:
    if pd.isna(row["ma50"]) or pd.isna(row["ma50_slope"]):
        return None
    bull = row["close"] > row["ma50"]
    slope_up = row["ma50_slope"] > 0
    if row["side"] == "SUPPORT":
        return 10.0 if bull and slope_up else 5.0 if bull or slope_up else 0.0
    return 10.0 if (not bull) and (not slope_up) else 5.0 if (not bull) or (not slope_up) else 0.0


def score_events(events: pd.DataFrame, piv: pd.DataFrame, all_pivots: dict[str, pd.DataFrame], tf: str) -> pd.DataFrame:
    e = events.sort_values("time").copy().reset_index(drop=True)
    raws = []
    strength = []
    touch_counts = []
    for idx, r in e.iterrows():
        t, side, lvl, tol = r["time"], r["side"], float(r["level"]), float(r["zone_pct"])
        prior = e.iloc[:idx]
        same = prior[(prior["side"] == side) & (np.abs(prior["level"].astype(float) / lvl - 1) <= 2 * tol)]
        touches = len(same)
        touch_counts.append(touches)
        fresh = 15.0 if touches == 0 else 12.0 if touches == 1 else 8.0 if touches == 2 else 4.0 if touches == 3 else 0.0
        matured = same[(same["mature_time"].notna()) & (same["mature_time"] < t) & (same["success"].notna())]
        react = None
        if len(matured):
            sr = float(matured["success"].astype(float).mean())
            fav = float(np.clip(pd.to_numeric(matured["fav_atr"], errors="coerce").median() / 1.5, 0, 1)) if matured["fav_atr"].notna().any() else sr
            react = 20.0 * (0.65 * sr + 0.35 * fav)
        comps = {
            "confluence": near_pivot_score(all_pivots, tf, t, lvl, tol),
            "reaction": react,
            "role_flip": role_flip_score(piv, t, side, lvl, tol),
            "volume": volume_score(r["event_vol_ratio"], r["source_vol_ratio"]),
            "freshness": fresh,
            "structure": structure_score(r),
        }
        maxw = {"confluence": 20, "reaction": 20, "role_flip": 15, "volume": 15, "freshness": 15, "structure": 10}
        num = sum(float(comps[k]) for k in comps if comps[k] is not None)
        den = sum(float(maxw[k]) for k in comps if comps[k] is not None)
        raw = 100.0 * num / den if den else np.nan
        raws.append(raw)

        prior_matured = e.iloc[:idx]
        prior_matured = prior_matured[(prior_matured["side"] == side) & prior_matured["success"].notna()]
        # maturity-safe calibration: only events whose horizon had completed before this event
        prior_raw = []
        for j, pr in prior_matured.iterrows():
            if pr["mature_time"] is not None and pd.notna(pr["mature_time"]) and pr["mature_time"] < t and j < len(raws)-1 and math.isfinite(raws[j]):
                prior_raw.append(raws[j])
        if len(prior_raw) >= 30 and math.isfinite(raw):
            strength.append(round(100.0 * float(np.mean(np.asarray(prior_raw) <= raw)), 2))
        else:
            strength.append(np.nan)
    e["touches_before"] = touch_counts
    e["raw_score"] = raws
    e["strength_score"] = strength
    return e


def band_stats(z: pd.DataFrame) -> dict:
    z = z[z["strength_score"].notna() & z["success"].notna()].copy()
    def b(lo, hi=None):
        q = z[z["strength_score"] >= lo] if hi is None else z[(z["strength_score"] >= lo) & (z["strength_score"] < hi)]
        return {"count": int(len(q)), "success_pct": None if q.empty else round(float(q["success"].astype(float).mean()) * 100, 2)}
    overall = None if z.empty else round(float(z["success"].astype(float).mean()) * 100, 2)
    low, mid, high = b(0, 50), b(50, 80), b(80, None)
    gain = None if high["success_pct"] is None or overall is None else round(high["success_pct"] - overall, 2)
    corr = None if len(z) < 8 else round(float(z[["strength_score", "success"]].astype(float).corr(method="spearman").iloc[0, 1]), 4)
    years = int(z[z["strength_score"] >= 80]["time"].dt.year.nunique()) if not z.empty else 0
    monotonic = all(v is not None for v in [low["success_pct"], mid["success_pct"], high["success_pct"]]) and high["success_pct"] >= mid["success_pct"] and mid["success_pct"] + 2 >= low["success_pct"]
    gates = {
        "case_gate": len(z) >= 50,
        "high_case_gate": high["count"] >= 10,
        "high_gain_gate": gain is not None and gain >= 5.0,
        "rank_corr_gate": corr is not None and corr >= 0.05,
        "monotonic_coarse_gate": bool(monotonic),
        "cross_year_gate": years >= 3,
    }
    return {
        "count": int(len(z)), "overall_success_pct": overall, "bands": {"LOW_0_49": low, "MID_50_79": mid, "HIGH_80_100": high},
        "high_gain_vs_all_pp": gain, "spearman_score_vs_success": corr, "high_distinct_years": years,
        "gates": gates, "pass": all(gates.values()),
    }


def current_level(df: pd.DataFrame, p: pd.DataFrame, e: pd.DataFrame, tf: str, side: str, all_pivots: dict[str, pd.DataFrame]) -> dict | None:
    i = len(df) - 1
    t = df.index[i]
    typ = side
    z = p[(p["type"] == typ) & (p["known_i"] <= i) & (p["pivot_i"] >= max(0, i-CFG[tf]["lookback"]))].copy()
    if z.empty:
        return None
    cl = float(df["close"].iloc[i])
    if side == "SUPPORT":
        z = z[z["level"] <= cl]
        if z.empty: return None
        cand = z.iloc[int(np.argmin(np.abs(np.log(cl / z["level"].to_numpy(float)))))]
    else:
        z = z[z["level"] >= cl]
        if z.empty: return None
        cand = z.iloc[int(np.argmin(np.abs(np.log(cl / z["level"].to_numpy(float)))))]
    lvl = float(cand["level"]); tol = zone_pct(df, i, tf)
    prior = e[(e["side"] == side) & (e["time"] < t)]
    same = prior[np.abs(prior["level"].astype(float) / lvl - 1) <= 2 * tol]
    touches = len(same)
    fresh = 15.0 if touches == 0 else 12.0 if touches == 1 else 8.0 if touches == 2 else 4.0 if touches == 3 else 0.0
    matured = same[(same["mature_time"].notna()) & (same["mature_time"] < t) & same["success"].notna()]
    react = None
    if len(matured):
        sr = float(matured["success"].astype(float).mean())
        fav = float(np.clip(pd.to_numeric(matured["fav_atr"], errors="coerce").median()/1.5, 0, 1)) if matured["fav_atr"].notna().any() else sr
        react = 20.0 * (0.65*sr + 0.35*fav)
    row = pd.Series({"side": side, "close": cl, "ma50": df["ma50"].iloc[i], "ma50_slope": df["ma50_slope"].iloc[i]})
    comps = {
        "confluence": near_pivot_score(all_pivots, tf, t, lvl, tol),
        "reaction": react,
        "role_flip": role_flip_score(p, t, side, lvl, tol),
        "volume": volume_score(cand["source_vol_ratio"], None),
        "freshness": fresh,
        "structure": structure_score(row),
    }
    maxw = {"confluence":20,"reaction":20,"role_flip":15,"volume":15,"freshness":15,"structure":10}
    num = sum(float(comps[k]) for k in comps if comps[k] is not None); den = sum(float(maxw[k]) for k in comps if comps[k] is not None)
    raw = 100*num/den if den else np.nan
    hist = e[(e["side"] == side) & e["strength_score"].notna() & e["raw_score"].notna() & e["mature_time"].notna() & (e["mature_time"] < t)]
    strength = None if hist.empty or not math.isfinite(raw) else round(100.0 * float(np.mean(hist["raw_score"].astype(float).to_numpy() <= raw)), 2)
    half = lvl * tol
    return {"level": round(lvl, 2), "zone_low": round(lvl-half, 2), "zone_high": round(lvl+half, 2), "raw_score": round(raw, 2), "strength_score": strength, "touches_before": touches, "components": {k: None if v is None else round(float(v),2) for k,v in comps.items()}}


def main() -> None:
    end = pd.Timestamp.now(tz="UTC").floor("h")
    d4 = add_indicators(fetch_binance("4h", START, end))
    d1 = add_indicators(fetch_binance("1d", START, end))
    w1 = add_indicators(to_weekly(d1))
    frames = {"4H": d4, "1D": d1, "1W": w1}
    piv = {tf: pivots(df, tf) for tf, df in frames.items()}
    scored: dict[str, pd.DataFrame] = {}
    validation: dict[str, dict] = {}
    current: dict[str, dict] = {}
    for tf, df in frames.items():
        ev = generate_events(df, piv[tf], tf)
        ev = score_events(ev, piv[tf], piv, tf)
        scored[tf] = ev
        ev.to_csv(OUT / f"events_{tf.lower()}_v01.csv", index=False)
        validation[tf] = {s: band_stats(ev[ev["side"] == s]) for s in ["SUPPORT", "RESISTANCE"]}
        current[tf] = {
            "SUPPORT": current_level(df, piv[tf], ev, tf, "SUPPORT", piv),
            "RESISTANCE": current_level(df, piv[tf], ev, tf, "RESISTANCE", piv),
        }
    core_tfs = ["4H", "1D"]
    core_passes = [validation[tf][side]["pass"] for tf in core_tfs for side in ["SUPPORT", "RESISTANCE"]]
    passed = sum(core_passes)
    deployment = "PASS_READ_ONLY" if passed == 4 else "PARTIAL_READ_ONLY" if passed >= 3 else "RESEARCH_ONLY"
    summary = {
        "engine": "BTC_SR_STRENGTH_V0_1",
        "schema_version": "0.1",
        "query_time_utc": end.isoformat(),
        "data": {tf: {"rows": int(len(df)), "start": df.index.min().isoformat(), "end": df.index.max().isoformat()} for tf, df in frames.items()},
        "score_definition": "Walk-forward percentile of a point-in-time raw level-quality score. It is a relative strength score, NOT future hold probability.",
        "raw_components": {"multi_timeframe_confluence":20,"historical_reaction_quality":20,"role_flip":15,"participation_volume":15,"freshness_retest_degradation":15,"structural_alignment":10,"cross_exchange": "N/A in historical backtest; live corroboration confidence only"},
        "validation": validation,
        "core_4h_1d_passes": int(passed),
        "deployment": deployment,
        "integration_rule": "Only timeframe/side cells with pass=true may be shown as calibrated S/R strength. Non-passing cells remain research/N.A.; never bypass Entry/Safety/NonChase/SL/RR.",
        "current_levels": current,
    }
    (OUT / "sr_strength_v01_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
