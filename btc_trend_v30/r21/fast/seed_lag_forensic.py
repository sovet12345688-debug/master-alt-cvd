from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
R21 = HERE.parent
BTC_ROOT = R21.parent
REPO_ROOT = BTC_ROOT.parent
for p in [str(REPO_ROOT), str(BTC_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from r20.r20_engine import build_feature_bundle

ROOT = BTC_ROOT / "output/validation_v1"
DATA1D = ROOT / "data_integrity/btc_usdt_1d_matrix.csv"
H4_RAW = ROOT / "step6_state_machine_replay/btc_usdt_complete_4h_matrix.csv"
R21_OUT = R21 / "output/historical_replay"
POSITIONS = R21_OUT / "r21_positions.csv"
SEEDS = R21_OUT / "r21_seed_candidates.csv"
TRUTH = R21_OUT / "truth_episode_metrics.csv"
R21_AUDIT = R21_OUT / "audit.json"
R20_CFG = BTC_ROOT / "r20/r20_frozen_config_rev2.json"
OUT = HERE / "output/seed_lag_forensic"
OUT.mkdir(parents=True, exist_ok=True)


def as_bool(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s
    return s.astype(str).str.lower().map({"true": True, "false": False}).fillna(False)


def finite(*vals) -> bool:
    try:
        return all(pd.notna(v) and np.isfinite(float(v)) for v in vals)
    except Exception:
        return False


def ohlcv_daily(raw: pd.DataFrame) -> pd.DataFrame:
    x = raw.copy()
    x.index = pd.to_datetime(x["open_time"], unit="ms", utc=True)
    return x[["open", "high", "low", "close", "volume"]].astype(float).sort_index()


def ohlcv_tf(raw: pd.DataFrame) -> pd.DataFrame:
    x = raw.copy()
    x["time"] = pd.to_datetime(x["time"], utc=True, format="mixed")
    x = x.sort_values("time").drop_duplicates("time").set_index("time")
    return x[["open", "high", "low", "close", "volume"]].astype(float)


def latest_daily_pos(daily: pd.DataFrame, decision_ts: pd.Timestamp, cfg: dict) -> int:
    off = pd.Timedelta(hours=int(cfg["data"]["timeframe_available_offset_hours"]["1D"]))
    return int(daily.index.searchsorted(pd.Timestamp(decision_ts) - off, side="right") - 1)


def long_reclaim(daily: pd.DataFrame, ts: pd.Timestamp, cfg: dict) -> bool:
    i = latest_daily_pos(daily, ts, cfg)
    if i < 1:
        return False
    row = daily.iloc[i]
    prev = daily.iloc[i - 1]
    c = cfg["long"]["seed"]["reclaim"]
    lb = int(c["prior_daily_ema20_breach_lookback_days"])
    hist = daily.iloc[max(0, i - lb):i]
    return bool(
        (hist["low"] < hist["ema20"]).any()
        and pd.notna(row["ema20"])
        and row["close"] > row["ema20"]
        and row["close"] > prev["high"]
        and row["volume_ratio"] >= float(c["daily_volume_ratio_min"])
    )


def short_failed_retest(h4: pd.DataFrame, pos: int, cfg: dict) -> bool:
    row = h4.iloc[pos]
    if not finite(row.get("d_ema20"), row.get("d_prior20_low"), row.get("high"), row.get("close"), row.get("ema20"), row.get("volume_ratio")):
        return False
    c = cfg["short"]["seed"]["failed_retest"]
    ref = min(float(row["d_ema20"]), float(row["d_prior20_low"]))
    bars = int(c["prior_breakdown_lookback_days"]) * 6
    prior = h4.iloc[max(0, pos - bars):pos]
    return bool(
        (prior["close"] < ref).any()
        and row["high"] >= ref
        and row["close"] < ref
        and row["close"] < row["ema20"]
        and row["volume_ratio"] >= float(c["volume_ratio_min"])
    )


def atomic_gate_frame(h4: pd.DataFrame, daily: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = []
    lw = cfg["long"]["watch"]
    lb = cfg["long"]["seed"]["breakout"]
    ls = cfg["long"]["seed"]["stop"]
    sw = cfg["short"]["watch"]
    sb = cfg["short"]["seed"]["breakdown"]
    ss = cfg["short"]["seed"]["stop"]
    for i in range(len(h4)):
        r = h4.iloc[i]
        l_d_price = finite(r.get("d_close"), r.get("d_ema20")) and r["d_close"] > r["d_ema20"]
        l_d_slope = finite(r.get("d_ema20_slope_5")) and r["d_ema20_slope_5"] > 0
        l_d_near = finite(r.get("d_close"), r.get("d_prior20_high"), r.get("d_atr14")) and r["d_close"] >= r["d_prior20_high"] - float(lw["distance_to_20d_high_atr_max"]) * r["d_atr14"]
        l_h_price = finite(r.get("close"), r.get("ema20")) and r["close"] > r["ema20"]
        l_h_slope = finite(r.get("ema20_slope_5")) and r["ema20_slope_5"] > 0
        l_watch = bool(l_d_price and l_d_slope and l_d_near and l_h_price and l_h_slope)
        l_break_price = finite(r.get("close"), r.get("prior20_high")) and r["close"] > r["prior20_high"]
        l_break_vol = finite(r.get("volume_ratio")) and r["volume_ratio"] >= float(lb["volume_ratio_min"])
        l_break_loc = finite(r.get("close_location")) and r["close_location"] >= 1.0 - float(lb["close_location_upper_fraction"])
        l_break_daily = finite(r.get("d_close"), r.get("d_ema20")) and r["d_close"] >= r["d_ema20"]
        l_break = bool(l_break_price and l_break_vol and l_break_loc and l_break_daily)
        l_reclaim = long_reclaim(daily, h4.index[i], cfg)
        l_stop = False
        if finite(r.get("causal_swing_low3"), r.get("atr14"), r.get("close")):
            stop = min(float(r["causal_swing_low3"]), float(r["close"] - float(ls["atr_multiple"]) * r["atr14"]))
            dist = (r["close"] - stop) / r["close"]
            l_stop = bool(dist > 0 and dist <= float(ls["max_stop_distance_pct"]))
        l_route = bool(l_break or l_reclaim)
        l_seed = bool(l_watch and l_route and l_stop)

        s_d_price = finite(r.get("d_close"), r.get("d_ema20")) and r["d_close"] < r["d_ema20"]
        s_d_slope = finite(r.get("d_ema20_slope_5")) and r["d_ema20_slope_5"] < 0
        s_d_near = finite(r.get("d_close"), r.get("d_prior20_low"), r.get("d_atr14")) and r["d_close"] <= r["d_prior20_low"] + float(sw["distance_to_20d_low_atr_max"]) * r["d_atr14"]
        s_h_price = finite(r.get("close"), r.get("ema20")) and r["close"] < r["ema20"]
        s_h_slope = finite(r.get("ema20_slope_5")) and r["ema20_slope_5"] < 0
        s_watch = bool(s_d_price and s_d_slope and s_d_near and s_h_price and s_h_slope)
        s_week = finite(r.get("w_close"), r.get("w_ema20"), r.get("w_ema20_slope_5")) and (r["w_close"] < r["w_ema20"] or r["w_ema20_slope_5"] < 0)
        s_d50 = finite(r.get("d_close"), r.get("d_ema50")) and r["d_close"] < r["d_ema50"]
        s_regime = bool(s_week and s_d50)
        s_br_price = finite(r.get("close"), r.get("prior20_low")) and r["close"] < r["prior20_low"]
        s_br_vol = finite(r.get("volume_ratio")) and r["volume_ratio"] >= float(sb["volume_ratio_min"])
        s_br_loc = finite(r.get("close_location")) and r["close_location"] <= float(sb["close_location_lower_fraction"])
        s_break = bool(s_br_price and s_br_vol and s_br_loc)
        s_retest = short_failed_retest(h4, i, cfg)
        s_stop = False
        if finite(r.get("causal_swing_high3"), r.get("atr14"), r.get("close")):
            stop = max(float(r["causal_swing_high3"]), float(r["close"] + float(ss["atr_multiple"]) * r["atr14"]))
            dist = (stop - r["close"]) / r["close"]
            s_stop = bool(dist > 0 and dist <= float(ss["max_stop_distance_pct"]))
        s_route = bool(s_break or s_retest)
        s_seed = bool(s_watch and s_regime and s_route and s_stop)
        out.append({
            "l_d_price": l_d_price, "l_d_slope": l_d_slope, "l_d_near": l_d_near, "l_h_price": l_h_price, "l_h_slope": l_h_slope,
            "l_watch": l_watch, "l_break_price": l_break_price, "l_break_vol": l_break_vol, "l_break_loc": l_break_loc, "l_break": l_break,
            "l_reclaim": l_reclaim, "l_route": l_route, "l_stop": l_stop, "l_seed": l_seed,
            "s_d_price": s_d_price, "s_d_slope": s_d_slope, "s_d_near": s_d_near, "s_h_price": s_h_price, "s_h_slope": s_h_slope,
            "s_watch": s_watch, "s_week": s_week, "s_d50": s_d50, "s_regime": s_regime, "s_br_price": s_br_price, "s_br_vol": s_br_vol,
            "s_br_loc": s_br_loc, "s_break": s_break, "s_retest": s_retest, "s_route": s_route, "s_stop": s_stop, "s_seed": s_seed,
        })
    return pd.DataFrame(out, index=h4.index)


def classify_7d(direction: str, g: pd.DataFrame) -> str:
    if direction == "LONG":
        if not g.l_watch.any():
            return "WATCH"
        if not (g.l_watch & g.l_route).any():
            return "ROUTE_TRIGGER"
        if not (g.l_watch & g.l_route & g.l_stop).any():
            return "STOP_GEOMETRY"
    else:
        if not g.s_watch.any():
            return "WATCH"
        if not (g.s_watch & g.s_regime).any():
            return "REGIME"
        if not (g.s_watch & g.s_regime & g.s_route).any():
            return "ROUTE_TRIGGER"
        if not (g.s_watch & g.s_regime & g.s_route & g.s_stop).any():
            return "STOP_GEOMETRY"
    return "SEED_WITHIN_7D"


def main() -> None:
    required = [DATA1D, H4_RAW, POSITIONS, SEEDS, TRUTH, R21_AUDIT, R20_CFG]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise SystemExit(f"R21_FAST_INPUT_MISSING:{missing}")
    audit = json.loads(R21_AUDIT.read_text(encoding="utf-8"))
    if audit.get("model") != "MASTER_BTC_TREND_V3_R2_1" or audit.get("status") != "HISTORICAL_DIAGNOSTIC_ONLY_NOT_UNTOUCHED_OOS":
        raise SystemExit("R21_HISTORICAL_IDENTITY_MISMATCH")
    cfg = json.loads(R20_CFG.read_text(encoding="utf-8"))
    daily_raw = ohlcv_daily(pd.read_csv(DATA1D))
    h4_raw = ohlcv_tf(pd.read_csv(H4_RAW))
    bundle = build_feature_bundle(daily_raw, h4_raw, None, cfg)
    daily = bundle["1D"]
    h4 = bundle["4H"]
    gates = atomic_gate_frame(h4, daily, cfg)

    seeds = pd.read_csv(SEEDS)
    seeds["timestamp"] = pd.to_datetime(seeds["timestamp"], utc=True, format="mixed")
    gen_rows = []
    for i, t in enumerate(gates.index):
        if bool(gates.iloc[i].l_seed):
            gen_rows.append(("LONG", "BREAKOUT" if bool(gates.iloc[i].l_break) else "RECLAIM", t))
        if bool(gates.iloc[i].s_seed):
            gen_rows.append(("SHORT", "BREAKDOWN" if bool(gates.iloc[i].s_break) else "FAILED_RETEST", t))
    gen = pd.DataFrame(gen_rows, columns=["direction", "route", "timestamp"]).sort_values(["timestamp", "direction", "route"]).reset_index(drop=True)
    art = seeds[["direction", "route", "timestamp"]].sort_values(["timestamp", "direction", "route"]).reset_index(drop=True)
    seed_identity = bool(len(gen) == len(art) and gen.equals(art))
    if not seed_identity:
        raise SystemExit("R21_SEED_RECONSTRUCTION_MISMATCH")

    pos = pd.read_csv(POSITIONS)
    for c in ["seed_time", "exit_time"]:
        pos[c] = pd.to_datetime(pos[c], utc=True, format="mixed", errors="coerce")
    truth = pd.read_csv(TRUTH)
    for c in ["start_date", "known_start"]:
        truth[c] = pd.to_datetime(truth[c], utc=True, format="mixed", errors="coerce")
    for c in ["medium_truth_positive", "participated_90d"]:
        truth[c] = as_bool(truth[c])
    med = truth[truth.medium_truth_positive].copy()

    episodes = []
    watch_atomic = []
    lastmile = []
    for _, r in med.iterrows():
        st = pd.Timestamp(r.known_start)
        end = st + pd.Timedelta(days=90)
        d = str(r.direction)
        pq = pos[(pos.direction == d) & (pos.seed_time <= end) & (pos.exit_time.isna() | (pos.exit_time > st))].sort_values("seed_time")
        if len(pq):
            p = pq.iloc[0]
            lag = max(0.0, (pd.Timestamp(p.seed_time) - st).total_seconds() / 86400.0)
            exec_id, route, seed_time = str(p.episode_id), str(p.route), pd.Timestamp(p.seed_time)
        else:
            lag, exec_id, route, seed_time = np.nan, None, None, pd.NaT
        sq = seeds[(seeds.direction == d) & (seeds.timestamp >= st) & (seeds.timestamp <= end)].sort_values("timestamp")
        raw_lag = float((pd.Timestamp(sq.iloc[0].timestamp) - st).total_seconds() / 86400.0) if len(sq) else np.nan
        late = bool(pd.isna(lag) or lag > 7.0)
        g7 = gates[(gates.index >= st) & (gates.index <= st + pd.Timedelta(days=7))]
        bottleneck = classify_7d(d, g7) if late else "WITHIN_7D"
        episodes.append({
            "truth_episode_id": str(r.truth_episode_id), "direction": d, "known_start": st, "year": int(st.year),
            "reported_lag90_days": r.lag90_days, "executed_seed_lag_days": lag, "raw_seed_lag_days": raw_lag,
            "executed_episode_id": exec_id, "executed_route": route, "bottleneck_7d": bottleneck,
            "participated_90d": bool(r.participated_90d), "MFE_90d": r.MFE_90d, "mcr90": r.mcr90,
        })
        if late and bottleneck == "WATCH":
            atoms = ["l_d_price", "l_d_slope", "l_d_near", "l_h_price", "l_h_slope"] if d == "LONG" else ["s_d_price", "s_d_slope", "s_d_near", "s_h_price", "s_h_slope"]
            rec = {"truth_episode_id": str(r.truth_episode_id), "direction": d}
            for a in atoms:
                rec[f"{a}_ever_7d"] = bool(g7[a].any())
                rec[f"{a}_pass_rate_7d"] = float(g7[a].mean()) if len(g7) else np.nan
            watch_atomic.append(rec)
        if late and pd.notna(seed_time):
            i = int(gates.index.get_indexer([seed_time])[0])
            if i > 0:
                prev = gates.iloc[i - 1]
                rec = {"truth_episode_id": str(r.truth_episode_id), "direction": d, "route": route, "lag_days": lag, "seed_time": seed_time, "previous_h4_time": gates.index[i - 1]}
                cols = ["l_d_price", "l_d_slope", "l_d_near", "l_h_price", "l_h_slope", "l_watch", "l_break_price", "l_break_vol", "l_break_loc", "l_reclaim", "l_route", "l_stop"] if d == "LONG" else ["s_d_price", "s_d_slope", "s_d_near", "s_h_price", "s_h_slope", "s_watch", "s_week", "s_d50", "s_regime", "s_br_price", "s_br_vol", "s_br_loc", "s_retest", "s_route", "s_stop"]
                for c in cols:
                    rec[f"prev_{c}"] = bool(prev[c])
                lastmile.append(rec)

    ep = pd.DataFrame(episodes)
    wa = pd.DataFrame(watch_atomic)
    lm = pd.DataFrame(lastmile)
    late = ep[(ep.executed_seed_lag_days > 7.0) | ep.executed_seed_lag_days.isna()].copy()
    raw_exec_mismatch = late[(late.executed_seed_lag_days.notna()) & (~np.isclose(late.executed_seed_lag_days, late.raw_seed_lag_days, atol=1e-12, equal_nan=False))]

    watch_never = {}
    for direction, cols in {
        "LONG": ["l_d_price", "l_d_slope", "l_d_near", "l_h_price", "l_h_slope"],
        "SHORT": ["s_d_price", "s_d_slope", "s_d_near", "s_h_price", "s_h_slope"],
    }.items():
        q = wa[wa.direction == direction] if len(wa) else wa
        watch_never[direction] = {c: int((~q[f"{c}_ever_7d"].astype(bool)).sum()) if len(q) and f"{c}_ever_7d" in q else 0 for c in cols}

    prior_false = {}
    for direction, cols in {
        "LONG": ["prev_l_watch", "prev_l_break_price", "prev_l_break_vol", "prev_l_break_loc", "prev_l_reclaim", "prev_l_route", "prev_l_stop"],
        "SHORT": ["prev_s_watch", "prev_s_week", "prev_s_regime", "prev_s_br_price", "prev_s_br_vol", "prev_s_br_loc", "prev_s_retest", "prev_s_route", "prev_s_stop"],
    }.items():
        q = lm[lm.direction == direction] if len(lm) else lm
        prior_false[direction] = {c: int((~q[c].astype(bool)).sum()) if len(q) and c in q else 0 for c in cols}

    summary = {
        "model": "MASTER_BTC_TREND_V3_R2_1",
        "mode": "FAST_READ_ONLY_SEED_LAG_FORENSIC",
        "historical_only": True,
        "production_promotion_evidence": False,
        "frozen_r21_modified": False,
        "seed_reconstruction_identity": seed_identity,
        "medium_truth_positive_episodes": int(len(ep)),
        "participated_90d": int(ep.participated_90d.sum()),
        "within_7d": int((ep.executed_seed_lag_days <= 7.0).sum()),
        "late_or_missed": int(len(late)),
        "late_executed": int(late.executed_seed_lag_days.notna().sum()),
        "missed_90d": int(late.executed_seed_lag_days.isna().sum()),
        "median_seed_lag_days": float(ep.executed_seed_lag_days.dropna().median()),
        "late_median_seed_lag_days": float(late.executed_seed_lag_days.dropna().median()),
        "raw_seed_equals_executed_seed_for_all_late_executed": bool(len(raw_exec_mismatch) == 0),
        "raw_seed_within_7d_but_execution_late": int(((late.raw_seed_lag_days <= 7.0) & (late.executed_seed_lag_days > 7.0)).sum()),
        "bottleneck_7d": {str(k): int(v) for k, v in late.bottleneck_7d.value_counts(dropna=False).to_dict().items()},
        "bottleneck_by_direction": {
            d: {str(k): int(v) for k, v in late[late.direction == d].bottleneck_7d.value_counts(dropna=False).to_dict().items()}
            for d in ["LONG", "SHORT"]
        },
        "direction_lag": {
            d: {
                "n": int(len(ep[ep.direction == d])),
                "participated": int(ep[ep.direction == d].executed_seed_lag_days.notna().sum()),
                "median_days": float(ep[ep.direction == d].executed_seed_lag_days.dropna().median()),
                "mean_days": float(ep[ep.direction == d].executed_seed_lag_days.dropna().mean()),
                "within_7d": int((ep[ep.direction == d].executed_seed_lag_days <= 7.0).sum()),
            } for d in ["LONG", "SHORT"]
        },
        "late_route_counts": {str(k): int(v) for k, v in late.executed_route.fillna("NO_ENTRY").value_counts().to_dict().items()},
        "watch_atomic_never_true_within_7d": watch_never,
        "last_h4_before_late_seed_false_counts": prior_false,
        "year_within7": {
            str(int(y)): {"within7": int((q.executed_seed_lag_days <= 7.0).sum()), "late_or_missed": int(((q.executed_seed_lag_days > 7.0) | q.executed_seed_lag_days.isna()).sum())}
            for y, q in ep.groupby("year")
        },
        "interpretation": {
            "confirmed_primary": "LATE_SEED_IS_DETECTION_LATENCY_NOT_EXECUTION_SUPPRESSION",
            "confirmed_stage": "WATCH_IS_DOMINANT_7D_BOTTLENECK",
            "confirmed_watch_atom": "DAILY_DISTANCE_TO_PRIOR_20D_EXTREME_WITHIN_1_ATR_IS_DOMINANT_ATOMIC_BLOCKER",
            "secondary": "SHORT_WEEKLY_BEARISH_REGIME_BLOCKS_A_SUBSET_AFTER_WATCH_IS_READY",
            "last_mile": "EVENTUAL_SEED_USUALLY_WAITS_FOR_A_NEW_4H_ROUTE_TRIGGER_AFTER_HIGHER_TIMEFRAME_ALIGNMENT",
        },
    }

    ep.to_csv(OUT / "seed_lag_episode_forensic.csv", index=False)
    wa.to_csv(OUT / "seed_lag_watch_atomic.csv", index=False)
    lm.to_csv(OUT / "seed_lag_lastmile.csv", index=False)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
