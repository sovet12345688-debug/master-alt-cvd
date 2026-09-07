from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
BTC_ROOT = HERE.parent
sys.path.insert(0, str(BTC_ROOT))

from r20.r20_engine import R20Engine, build_feature_bundle

ROOT = Path("btc_trend_v30/output/validation_v1")
DATA1D = ROOT / "data_integrity/btc_usdt_1d_matrix.csv"
EP = ROOT / "step3_episode_dedupe/independent_episodes.csv"
TRUTH = ROOT / "step4_outcome_labels/episode_medium_long_truth_labels.csv"
H1_RAW = ROOT / "step6_state_machine_replay/btc_usdt_1h_matrix.csv"
H4_RAW = ROOT / "step6_state_machine_replay/btc_usdt_complete_4h_matrix.csv"
FREEZE = HERE / "r20_frozen_config_rev2.json"
MANIFEST = HERE / "r20_freeze_manifest_rev2.json"
OUT = HERE / "output/historical_replay"
OUT.mkdir(parents=True, exist_ok=True)

OOS_START = pd.Timestamp("2021-01-01T00:00:00Z")
OOS_END = pd.Timestamp("2026-09-04T23:59:59Z")
END_EXCL = pd.Timestamp("2026-09-05T00:00:00Z")


def as_bool(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s
    return s.astype(str).str.lower().map({"true": True, "false": False}).fillna(False)


def safe_rate(n: float | int, d: float | int):
    return float(n / d) if d else None


def finite(v: Any) -> bool:
    try:
        return pd.notna(v) and np.isfinite(float(v))
    except Exception:
        return False


def ohlcv_daily(raw: pd.DataFrame) -> pd.DataFrame:
    x = raw.copy()
    x.index = pd.to_datetime(x["open_time"], unit="ms", utc=True)
    return x[["open", "high", "low", "close", "volume"]].astype(float).sort_index()


def ohlcv_tf(raw: pd.DataFrame) -> pd.DataFrame:
    x = raw.copy()
    x["time"] = pd.to_datetime(x["time"], utc=True)
    x = x.sort_values("time").drop_duplicates("time").set_index("time")
    return x[["open", "high", "low", "close", "volume"]].astype(float)


def direction_sign(direction: str) -> float:
    return 1.0 if direction == "LONG" else -1.0


def price_at(h1: pd.DataFrame, ts: pd.Timestamp) -> float | None:
    i = h1.index.searchsorted(pd.Timestamp(ts), side="right") - 1
    if i < 0:
        return None
    v = float(h1.iloc[i].close)
    return v if np.isfinite(v) else None


def daily_source_index_at(daily_source: pd.DataFrame, decision_ts: pd.Timestamp, cfg: dict) -> int:
    off = pd.Timedelta(hours=int(cfg["data"]["timeframe_available_offset_hours"]["1D"]))
    return int(daily_source.index.searchsorted(pd.Timestamp(decision_ts) - off, side="right") - 1)


def latest_h4_index(h4_ctx: pd.DataFrame, ts: pd.Timestamp) -> int:
    return int(h4_ctx.index.searchsorted(pd.Timestamp(ts), side="right") - 1)


def latest_weekly_row(weekly: pd.DataFrame, ts: pd.Timestamp) -> pd.Series | None:
    i = weekly.index.searchsorted(pd.Timestamp(ts), side="right") - 1
    return None if i < 0 else weekly.iloc[i]


def stop_hit_between(h1: pd.DataFrame, start_exclusive: pd.Timestamp, end_inclusive: pd.Timestamp, direction: str, stop: float):
    q = h1[(h1.index > start_exclusive) & (h1.index <= end_inclusive)]
    if q.empty:
        return None
    hit = q.low <= stop if direction == "LONG" else q.high >= stop
    z = q[hit]
    return None if z.empty else pd.Timestamp(z.index[0])


def leg_unit_r(direction: str, entry: float, stop: float, exit_price: float) -> float:
    risk = abs(float(entry) - float(stop))
    if risk <= 0:
        return float("nan")
    return direction_sign(direction) * (float(exit_price) - float(entry)) / risk


def family_key(seed: pd.Series) -> str:
    # Deterministic causal family identity with no performance tuning: route + exact frozen reference rounded only for stable serialization.
    return f"{seed.route}|{float(seed.reference):.8f}"


def open_leg(stage: str, t: pd.Timestamp, entry: float, stop: float, weight: float) -> dict:
    return {
        "stage": stage,
        "open_time": pd.Timestamp(t),
        "entry": float(entry),
        "stop": float(stop),
        "initial_weight_R": float(weight),
        "remaining_weight_R": float(weight),
        "closes": [],
    }


def close_fraction(legs: list[dict], direction: str, t: pd.Timestamp, price: float, fraction: float, reason: str, tx_rows: list[dict], episode_id: str):
    for leg in legs:
        rem = float(leg["remaining_weight_R"])
        amount = rem * float(fraction)
        if amount <= 1e-15:
            continue
        ur = leg_unit_r(direction, leg["entry"], leg["stop"], price)
        wr = amount * ur
        c = {"time": pd.Timestamp(t), "price": float(price), "weight_R": amount, "unit_R": ur, "weighted_R": wr, "reason": reason}
        leg["closes"].append(c)
        leg["remaining_weight_R"] = rem - amount
        tx_rows.append({"episode_id": episode_id, "direction": direction, "stage": leg["stage"], "open_time": leg["open_time"], "entry": leg["entry"], "stop": leg["stop"], **c})


def close_all(legs: list[dict], direction: str, t: pd.Timestamp, price: float, reason: str, tx_rows: list[dict], episode_id: str):
    close_fraction(legs, direction, t, price, 1.0, reason, tx_rows, episode_id)


def simulate_episode(seed: pd.Series, serial: int, engine: R20Engine, daily: pd.DataFrame, h4: pd.DataFrame, weekly: pd.DataFrame, h1: pd.DataFrame, cfg: dict):
    direction = str(seed.direction)
    seed_time = pd.Timestamp(seed.timestamp)
    episode_id = f"R20-{direction}-{serial:04d}"
    stop = float(seed.stop)
    seed_i = daily_source_index_at(daily, seed_time, cfg)
    if seed_i < 1:
        return None, []
    dseed = daily.iloc[seed_i]
    risk_seed = float(cfg["long" if direction == "LONG" else "short"]["seed"]["risk_R"])
    legs = [open_leg("SEED", seed_time, float(seed.entry), stop, risk_seed)]
    tx_rows: list[dict] = []
    confirmed_time = None
    core_time = None
    derisk_time = None
    exit_time = None
    exit_reason = None
    max_state = "SEED"
    cursor = seed_time
    high_close = float(dseed.close)
    low_close = float(dseed.close)
    side = "long" if direction == "LONG" else "short"
    confirm_window = int(cfg[side]["confirm"]["window_days"])

    for day_i in range(seed_i + 1, len(daily)):
        davail = pd.Timestamp(daily.index[day_i]) + pd.Timedelta(hours=int(cfg["data"]["timeframe_available_offset_hours"]["1D"]))
        if davail >= END_EXCL:
            break

        st = stop_hit_between(h1, cursor, davail, direction, stop)
        if st is not None:
            close_all(legs, direction, st, stop, "STRUCTURAL_STOP", tx_rows, episode_id)
            exit_time, exit_reason = st, "STRUCTURAL_STOP"
            break

        drow = daily.iloc[day_i]
        high_close = max(high_close, float(drow.close))
        low_close = min(low_close, float(drow.close))
        h4i = latest_h4_index(h4, davail)
        if h4i < 0:
            cursor = davail
            continue

        if direction == "LONG":
            derisk, full_exit = engine.long_exit_flags(daily, h4, day_i, h4i, high_close)
        else:
            derisk, full_exit = engine.short_exit_flags(daily, h4, day_i, h4i, low_close)

        if full_exit:
            close_all(legs, direction, davail, float(drow.close), "PRECORE_INVALIDATION" if core_time is None else "FULL_EXIT", tx_rows, episode_id)
            exit_time, exit_reason = davail, "PRECORE_INVALIDATION" if core_time is None else "FULL_EXIT"
            break

        if core_time is not None and derisk_time is None and derisk:
            close_fraction(legs, direction, davail, float(drow.close), float(cfg["holding"]["derisk_fraction"]), "DERISK_50", tx_rows, episode_id)
            derisk_time = davail
            max_state = "DERISK"

        days_after_seed = day_i - seed_i
        if confirmed_time is None and days_after_seed <= confirm_window:
            if direction == "LONG":
                ok = engine.long_confirmed(daily, seed_i, day_i, float(dseed.high), float(seed.reference))
            else:
                ok = engine.short_confirmed(daily, seed_i, day_i, float(dseed.low), float(seed.reference))
            if ok:
                add_risk = float(cfg[side]["confirm"]["risk_add_R"])
                if (direction == "LONG" and float(drow.close) > stop) or (direction == "SHORT" and float(drow.close) < stop):
                    legs.append(open_leg("CONFIRM", davail, float(drow.close), stop, add_risk))
                    confirmed_time = davail
                    max_state = "CONFIRMED"

        if confirmed_time is not None and core_time is None:
            wrow = latest_weekly_row(weekly, davail)
            if wrow is not None:
                ok_core = engine.long_core_allowed(drow, wrow) if direction == "LONG" else engine.short_core_allowed(drow, wrow)
                if ok_core:
                    add_risk = float(cfg[side]["core"]["risk_add_R"])
                    if (direction == "LONG" and float(drow.close) > stop) or (direction == "SHORT" and float(drow.close) < stop):
                        legs.append(open_leg("CORE", davail, float(drow.close), stop, add_risk))
                        core_time = davail
                        max_state = "CORE"

        cursor = davail

    if exit_time is None:
        st = stop_hit_between(h1, cursor, END_EXCL - pd.Timedelta(microseconds=1), direction, stop)
        if st is not None:
            close_all(legs, direction, st, stop, "STRUCTURAL_STOP", tx_rows, episode_id)
            exit_time, exit_reason = st, "STRUCTURAL_STOP"

    realized = float(sum(float(x["weighted_R"]) for x in tx_rows)) if tx_rows else 0.0
    remaining = float(sum(float(x["remaining_weight_R"]) for x in legs))
    mark = price_at(h1, END_EXCL - pd.Timedelta(microseconds=1))
    mark_r = realized
    if mark is not None:
        for leg in legs:
            rem = float(leg["remaining_weight_R"])
            if rem > 1e-15:
                mark_r += rem * leg_unit_r(direction, leg["entry"], leg["stop"], mark)
    if remaining <= 1e-12 and exit_time is None:
        exit_time = max((pd.Timestamp(c["time"]) for leg in legs for c in leg["closes"]), default=pd.NaT)
        exit_reason = "CLOSED"

    pos = {
        "episode_id": episode_id,
        "direction": direction,
        "route": str(seed.route),
        "family_key": family_key(seed),
        "reference": float(seed.reference),
        "seed_time": seed_time,
        "seed_entry": float(seed.entry),
        "stop": stop,
        "seed_day_index": int(seed_i),
        "confirmed_time": confirmed_time,
        "core_time": core_time,
        "derisk_time": derisk_time,
        "exit_time": exit_time,
        "exit_reason": exit_reason or "CENSORED_OPEN",
        "max_state": max_state,
        "realized_R": realized,
        "marked_R_at_end": float(mark_r),
        "remaining_weight_R": remaining,
        "resolved": bool(remaining <= 1e-12),
        "invalidated_before_confirm": bool(exit_reason == "STRUCTURAL_STOP" and confirmed_time is None),
        "legs": legs,
    }
    return pos, tx_rows


def completed_sessions_since_exit(daily: pd.DataFrame, exit_time: pd.Timestamp, candidate_time: pd.Timestamp, cfg: dict) -> int:
    off = pd.Timedelta(hours=int(cfg["data"]["timeframe_available_offset_hours"]["1D"]))
    avail = daily.index + off
    return int(((avail > pd.Timestamp(exit_time)) & (avail <= pd.Timestamp(candidate_time))).sum())


def build_positions(engine: R20Engine, daily: pd.DataFrame, h4: pd.DataFrame, weekly: pd.DataFrame, h1: pd.DataFrame, seeds: pd.DataFrame, cfg: dict):
    positions: list[dict] = []
    transactions: list[dict] = []
    for direction in ["LONG", "SHORT"]:
        q = seeds[(seeds.direction == direction) & (seeds.timestamp >= OOS_START) & (seeds.timestamp < END_EXCL)].sort_values("timestamp").reset_index(drop=True)
        last_exit = None
        last_family = None
        serial = 0
        for _, seed in q.iterrows():
            st = pd.Timestamp(seed.timestamp)
            if last_exit is not None:
                if st <= pd.Timestamp(last_exit):
                    continue
                sessions = completed_sessions_since_exit(daily, pd.Timestamp(last_exit), st, cfg)
                new_family = family_key(seed) != last_family
                if not (sessions >= int(cfg["reset"]["minimum_completed_daily_sessions"]) and new_family):
                    continue
            serial += 1
            pos, tx = simulate_episode(seed, serial, engine, daily, h4, weekly, h1, cfg)
            if pos is None:
                continue
            positions.append(pos)
            transactions.extend(tx)
            if pos["exit_time"] is None or pd.isna(pos["exit_time"]):
                break
            last_exit = pd.Timestamp(pos["exit_time"])
            last_family = pos["family_key"]
    positions.sort(key=lambda x: x["seed_time"])
    return positions, transactions


def active_weight_at(leg: dict, ts: pd.Timestamp) -> float:
    if pd.Timestamp(leg["open_time"]) > ts:
        return 0.0
    closed = sum(float(c["weight_R"]) for c in leg["closes"] if pd.Timestamp(c["time"]) <= ts)
    return max(0.0, float(leg["initial_weight_R"]) - closed)


def captured_return_for_window(positions: list[dict], direction: str, start: pd.Timestamp, horizon: pd.Timestamp, start_close: float, h1: pd.DataFrame) -> tuple[float, bool, float | None]:
    mark_start = price_at(h1, start)
    mark_end = price_at(h1, horizon)
    total = 0.0
    participated = False
    first_seed = None
    for pos in positions:
        if pos["direction"] != direction:
            continue
        seed = pd.Timestamp(pos["seed_time"])
        ex = pd.Timestamp(pos["exit_time"]) if pos["exit_time"] is not None and pd.notna(pos["exit_time"]) else END_EXCL
        if seed > horizon or ex <= start:
            continue
        participated = True
        if first_seed is None or seed < first_seed:
            first_seed = seed
        for leg in pos["legs"]:
            ot = pd.Timestamp(leg["open_time"])
            if ot > horizon:
                continue
            if ot <= start:
                w = active_weight_at(leg, start)
                if w <= 1e-15 or mark_start is None:
                    continue
                basis = float(mark_start)
                relevant = [c for c in leg["closes"] if start < pd.Timestamp(c["time"]) <= horizon]
            else:
                w = float(leg["initial_weight_R"])
                basis = float(leg["entry"])
                relevant = [c for c in leg["closes"] if ot <= pd.Timestamp(c["time"]) <= horizon]
            rem = w
            for c in relevant:
                amount = min(rem, float(c["weight_R"]))
                if amount <= 0:
                    continue
                total += amount * direction_sign(direction) * (float(c["price"]) - basis) / float(start_close)
                rem -= amount
            if rem > 1e-15 and mark_end is not None:
                total += rem * direction_sign(direction) * (float(mark_end) - basis) / float(start_close)
    lag = None if first_seed is None else max(0.0, (first_seed - start).total_seconds() / 86400.0)
    return max(0.0, float(total)), participated, lag


def truth_metrics(positions: list[dict], ep: pd.DataFrame, truth: pd.DataFrame, h1: pd.DataFrame):
    e = ep.copy()
    e["start_date"] = pd.to_datetime(e["start_date"], utc=True)
    t = truth.copy()
    for c in ["available_90d", "available_365d"]:
        t[c] = as_bool(t[c])
    key = "independent_episode_id"
    m = e.merge(t, on=key, how="inner", suffixes=("", "_truth"))
    m = m[(m.start_date >= OOS_START) & (m.start_date <= OOS_END)].copy()
    rows = []
    for _, r in m.iterrows():
        direction = str(r.direction).upper()
        start = pd.Timestamp(r.start_date) + pd.Timedelta(days=1)
        start_close = float(r.start_close)
        med = bool(r.available_90d and str(r.medium_truth_status) in ("MEDIUM_SUCCESS_20", "MEDIUM_SUCCESS_30"))
        lng = bool(r.available_365d and str(r.long_truth_status) in ("LONG_SUCCESS_PRIMARY", "LONG_SUCCESS_EXTENSION"))
        cap90, part90, lag90 = captured_return_for_window(positions, direction, start, min(start + pd.Timedelta(days=90), END_EXCL), start_close, h1)
        cap365, part365, lag365 = captured_return_for_window(positions, direction, start, min(start + pd.Timedelta(days=365), END_EXCL), start_close, h1)
        mfe90 = float(r.MFE_90d) if finite(r.MFE_90d) else np.nan
        mfe365 = float(r.MFE_365d) if finite(r.MFE_365d) else np.nan
        rows.append({
            "truth_episode_id": str(r[key]), "direction": direction, "start_date": r.start_date, "known_start": start,
            "medium_truth_positive": med, "long_truth_positive": lng,
            "participated_90d": bool(part90), "participated_365d": bool(part365),
            "lag90_days": lag90, "lag365_days": lag365,
            "capture90_return": cap90, "capture365_return": cap365,
            "MFE_90d": mfe90, "MFE_365d": mfe365,
            "mcr90": min(1.0, cap90 / mfe90) if med and finite(mfe90) and mfe90 > 0 else np.nan,
            "mcr365": min(1.0, cap365 / mfe365) if lng and finite(mfe365) and mfe365 > 0 else np.nan,
        })
    return pd.DataFrame(rows)


def has_medium_truth_overlap(pos: dict, tm: pd.DataFrame) -> bool:
    st = pd.Timestamp(pos["seed_time"])
    en = pd.Timestamp(pos["exit_time"]) if pos["exit_time"] is not None and pd.notna(pos["exit_time"]) else min(st + pd.Timedelta(days=90), END_EXCL)
    q = tm[(tm.direction == pos["direction"]) & tm.medium_truth_positive]
    for _, r in q.iterrows():
        a = pd.Timestamp(r.known_start)
        b = min(a + pd.Timedelta(days=90), END_EXCL)
        if max(st, a) < min(en, b):
            return True
    return False


def expectancy(rows: pd.DataFrame) -> dict:
    if rows.empty:
        return {"episodes": 0, "resolved": 0, "mean_R": None, "wins": 0, "losses": 0, "gross_positive_R": 0.0, "gross_negative_R": 0.0}
    r = pd.to_numeric(rows.loc[rows.resolved, "realized_R"], errors="coerce").dropna()
    return {
        "episodes": int(len(rows)), "resolved": int(len(r)), "mean_R": float(r.mean()) if len(r) else None,
        "wins": int((r > 0).sum()), "losses": int((r < 0).sum()),
        "gross_positive_R": float(r[r > 0].sum()) if len(r) else 0.0,
        "gross_negative_R": float(-r[r < 0].sum()) if len(r) else 0.0,
    }


def gate(value, op: str, threshold: float) -> str:
    if value is None or not finite(value):
        return "UNRESOLVED"
    if op == ">=":
        return "PASS" if float(value) >= threshold else "FAIL"
    if op == "<=":
        return "PASS" if float(value) <= threshold else "FAIL"
    if op == ">":
        return "PASS" if float(value) > threshold else "FAIL"
    raise ValueError(op)


def main():
    required = [DATA1D, EP, TRUTH, H1_RAW, H4_RAW, FREEZE, MANIFEST]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise SystemExit(f"R20_BASELINE_INPUTS_MISSING:{missing}")

    cfg = json.loads(FREEZE.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if cfg.get("status") != "FINAL_FROZEN_NO_REPLAY_REV2":
        raise SystemExit("R20_REV2_NOT_FROZEN")
    if manifest.get("historical_replay_performed_before_freeze") is not False:
        raise SystemExit("REPLAY_FIREWALL_BROKEN")

    raw1d = pd.read_csv(DATA1D)
    rawh1 = pd.read_csv(H1_RAW)
    rawh4 = pd.read_csv(H4_RAW)
    daily_raw = ohlcv_daily(raw1d)
    h1_raw = ohlcv_tf(rawh1)
    h4_raw = ohlcv_tf(rawh4)

    engine = R20Engine(cfg)
    bundle = build_feature_bundle(daily_raw, h4_raw, h1_raw, cfg)
    daily = bundle["1D"]
    h4 = bundle["4H"]
    h1 = bundle["1H"]
    weekly = bundle["1W"]
    seeds = engine.scan_seed_candidates(daily_raw, h4_raw, h1_raw)
    if seeds.empty:
        seeds = pd.DataFrame(columns=["direction", "route", "entry", "stop", "reference", "timestamp"])
    else:
        seeds["timestamp"] = pd.to_datetime(seeds["timestamp"], utc=True)

    positions, tx = build_positions(engine, daily, h4, weekly, h1, seeds, cfg)
    pos_rows = []
    for p in positions:
        pos_rows.append({k: v for k, v in p.items() if k != "legs"})
    posdf = pd.DataFrame(pos_rows)
    txdf = pd.DataFrame(tx)
    ep = pd.read_csv(EP)
    truth = pd.read_csv(TRUTH)
    tm = truth_metrics(positions, ep, truth, h1)

    if not posdf.empty:
        posdf["seed_time"] = pd.to_datetime(posdf.seed_time, utc=True)
        posdf["exit_time"] = pd.to_datetime(posdf.exit_time, utc=True, errors="coerce")
        complete90 = posdf.seed_time + pd.Timedelta(days=90) <= END_EXCL
        posdf["complete90"] = complete90
        posdf["has_medium_truth_overlap"] = [has_medium_truth_overlap(p, tm) for p in positions]
        posdf["confirmed_false_start"] = posdf.invalidated_before_confirm & posdf.complete90 & (~posdf.has_medium_truth_overlap)
    else:
        for c in ["complete90", "has_medium_truth_overlap", "confirmed_false_start"]:
            posdf[c] = pd.Series(dtype=bool)

    long_exp = expectancy(posdf[posdf.direction == "LONG"]) if not posdf.empty else expectancy(posdf)
    short_exp = expectancy(posdf[posdf.direction == "SHORT"]) if not posdf.empty else expectancy(posdf)
    resolved = posdf[posdf.resolved].copy() if not posdf.empty else posdf.copy()
    rr = pd.to_numeric(resolved.realized_R, errors="coerce").dropna() if not resolved.empty else pd.Series(dtype=float)
    gp = float(rr[rr > 0].sum()) if len(rr) else 0.0
    gn = float(-rr[rr < 0].sum()) if len(rr) else 0.0
    capture_loss = safe_rate(gp, gn)

    med = tm[tm.medium_truth_positive].copy()
    lng = tm[tm.long_truth_positive].copy()
    mcr90 = float(pd.to_numeric(med.mcr90, errors="coerce").dropna().mean()) if len(med) else None
    mcr365 = float(pd.to_numeric(lng.mcr365, errors="coerce").dropna().mean()) if len(lng) else None
    recall90 = safe_rate(int(med.participated_90d.sum()), len(med)) if len(med) else None
    recall365 = safe_rate(int(lng.participated_365d.sum()), len(lng)) if len(lng) else None
    lag90s = pd.to_numeric(med.loc[med.participated_90d, "lag90_days"], errors="coerce").dropna()
    median_lag = float(lag90s.median()) if len(lag90s) else None
    fs_denom = int(posdf.complete90.sum()) if not posdf.empty else 0
    fs_num = int(posdf.confirmed_false_start.sum()) if not posdf.empty else 0
    fs_rate = safe_rate(fs_num, fs_denom)

    g = cfg["predeclared_candidate_gates"]
    gates = {
        "mcr90_mean": {"value": mcr90, "threshold": g["mcr_90d_mean_min"], "result": gate(mcr90, ">=", float(g["mcr_90d_mean_min"]))},
        "mcr365_mean": {"value": mcr365, "threshold": g["mcr_365d_mean_min"], "result": gate(mcr365, ">=", float(g["mcr_365d_mean_min"]))},
        "trend_recall90": {"value": recall90, "threshold": g["trend_recall_90d_min"], "result": gate(recall90, ">=", float(g["trend_recall_90d_min"]))},
        "trend_recall365": {"value": recall365, "threshold": g["trend_recall_365d_min"], "result": gate(recall365, ">=", float(g["trend_recall_365d_min"]))},
        "long_expectancy": {"value": long_exp["mean_R"], "threshold": g["long_expectancy_gt_R"], "result": gate(long_exp["mean_R"], ">", float(g["long_expectancy_gt_R"]))},
        "short_expectancy": {"value": short_exp["mean_R"], "threshold": g["short_expectancy_gt_R"], "result": gate(short_exp["mean_R"], ">", float(g["short_expectancy_gt_R"]))},
        "confirmed_false_start_rate": {"value": fs_rate, "threshold": g["confirmed_false_start_rate_max"], "result": gate(fs_rate, "<=", float(g["confirmed_false_start_rate_max"]))},
        "capture_to_loss_ratio": {"value": capture_loss, "threshold": g["capture_to_loss_ratio_gt"], "result": gate(capture_loss, ">", float(g["capture_to_loss_ratio_gt"]))},
        "median_seed_lag_days": {"value": median_lag, "threshold": g["median_seed_lag_days_max"], "result": gate(median_lag, "<=", float(g["median_seed_lag_days_max"]))},
        "cycle_independence": {"value": None, "threshold": g["cycle_bucket_expectancy_floor_R"], "result": "UNRESOLVED", "reason": "EXACT_CYCLE_BUCKET_FORMULA_NOT_FROZEN_PRE_REPLAY"},
        "robustness": {"value": None, "result": "UNRESOLVED", "reason": "SEPARATE_POST_REPLAY_REQUIRED"},
        "state_monotonicity": {"value": None, "result": "UNRESOLVED", "reason": "EXACT_BINNING_RULE_NOT_FROZEN_PRE_REPLAY"},
        "full_risk_governor": {"value": None, "result": "UNRESOLVED"},
        "exact_v26_h2h": {"value": None, "result": "UNRESOLVED_LINK_NA"},
        "forward_untouched_oos": {"value": None, "result": "UNRESOLVED_NOT_MATURE"},
    }
    hard_failures = [k for k, v in gates.items() if v["result"] == "FAIL"]
    unresolved = [k for k, v in gates.items() if str(v["result"]).startswith("UNRESOLVED")]

    # Episode-order risk-weighted drawdown on resolved R20 episodes; not concurrent portfolio MDD.
    if not resolved.empty:
        rz = resolved.sort_values("seed_time").realized_R.astype(float).cumsum()
        dd = rz - rz.cummax()
        episode_order_mdd = float(dd.min()) if len(dd) else 0.0
    else:
        episode_order_mdd = None

    audit = {
        "model": "MASTER_BTC_TREND_V3_R2_0",
        "freeze_revision": "REV2_PIT_AVAILABILITY_HOTFIX",
        "status": "HISTORICAL_DIAGNOSTIC_ONLY_NOT_UNTOUCHED_OOS",
        "historical_window": [str(OOS_START), str(OOS_END)],
        "production_promotion_evidence": False,
        "promotion_decision": "HOLD",
        "freeze_identity": {
            "candidate_config_sha256": manifest["candidate_config_sha256"],
            "frozen_config_git_blob_sha1": manifest["frozen_config_git_blob_sha1"],
            "engine_git_blob_sha1": manifest["engine_git_blob_sha1"],
            "pit_contract_run_id": manifest["pit_contract_run_id"],
            "pit_tests": manifest["pit_tests"],
        },
        "counts": {
            "raw_seed_candidates": int(len(seeds)),
            "executed_r20_episodes": int(len(posdf)),
            "resolved_r20_episodes": int(posdf.resolved.sum()) if len(posdf) else 0,
            "censored_open": int((~posdf.resolved).sum()) if len(posdf) else 0,
            "long_episodes": int((posdf.direction == "LONG").sum()) if len(posdf) else 0,
            "short_episodes": int((posdf.direction == "SHORT").sum()) if len(posdf) else 0,
            "confirmed": int(posdf.confirmed_time.notna().sum()) if len(posdf) else 0,
            "core": int(posdf.core_time.notna().sum()) if len(posdf) else 0,
            "derisked": int(posdf.derisk_time.notna().sum()) if len(posdf) else 0,
        },
        "summary": {
            "mcr90_mean": mcr90,
            "mcr365_mean": mcr365,
            "trend_recall90": recall90,
            "trend_recall365": recall365,
            "median_seed_lag_days_90d_truth": median_lag,
            "confirmed_false_start_rate": fs_rate,
            "confirmed_false_starts": fs_num,
            "complete90_seed_episodes": fs_denom,
            "long_expectancy": long_exp,
            "short_expectancy": short_exp,
            "capture_to_loss_ratio": capture_loss,
            "episode_order_mdd_R": episode_order_mdd,
        },
        "gates": gates,
        "hard_failures": hard_failures,
        "unresolved_blockers": unresolved,
        "methodology_notes": [
            "Source timestamps are candle OPEN times; REV2 shifts 1H/4H/1D to close availability before decisions.",
            "R20 LONG and SHORT machines are replayed independently.",
            "Reset requires >=3 completed daily sessions after exit AND a changed causal route/reference family key.",
            "No stop widening or fixed full-profit target is introduced.",
            "MCR numerator includes only exposure actually active inside each truth window; pre-window PnL is excluded.",
            "Historical 2021-2026 results cannot promote R2.0 and cannot change REV2 thresholds.",
            "Cycle and state-monotonicity exact formulas were not frozen; they remain UNRESOLVED rather than invented post hoc.",
        ],
    }

    posdf.to_csv(OUT / "r20_positions.csv", index=False)
    txdf.to_csv(OUT / "r20_leg_transactions.csv", index=False)
    tm.to_csv(OUT / "truth_episode_metrics.csv", index=False)
    seeds.to_csv(OUT / "r20_seed_candidates.csv", index=False)
    (OUT / "audit.json").write_text(json.dumps(audit, indent=2, default=str), encoding="utf-8")
    print(json.dumps(audit, indent=2, default=str))


if __name__ == "__main__":
    main()
