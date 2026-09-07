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

import validation_protocol_v1_step2_r12_daily_scoring as s2
import validation_protocol_v1_step6_state_machine_replay as s6
from r13.r13_engine import R13Engine, ContractError

ROOT = Path("btc_trend_v30/output/validation_v1")
DATA1D = ROOT / "data_integrity/btc_usdt_1d_matrix.csv"
EP = ROOT / "step3_episode_dedupe/independent_episodes.csv"
SIG = ROOT / "step3_episode_dedupe/daily_signal_family_assignments.csv"
TRUTH = ROOT / "step4_outcome_labels/episode_medium_long_truth_labels.csv"
R12_STATE = ROOT / "step6_state_machine_replay/episode_state_replay.csv"
R12_LEGS = ROOT / "step6_state_machine_replay/independent_trade_legs.csv"
H1_RAW = ROOT / "step6_state_machine_replay/btc_usdt_1h_matrix.csv"
H4_RAW = ROOT / "step6_state_machine_replay/btc_usdt_complete_4h_matrix.csv"
OUT = Path("btc_trend_v30/r13/output/historical_diagnostic")
OUT.mkdir(parents=True, exist_ok=True)

OOS_START = pd.Timestamp("2021-01-01T00:00:00Z")
OOS_END = pd.Timestamp("2026-09-04T23:59:59Z")
END_EXCL = pd.Timestamp("2026-09-05T00:00:00Z")


def as_bool(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s
    return s.astype(str).str.lower().map({"true": True, "false": False}).fillna(False)


def safe_rate(n: int | float, d: int | float):
    return float(n / d) if d else None


def qstats(s: pd.Series) -> dict[str, Any]:
    z = pd.to_numeric(s, errors="coerce").dropna()
    if z.empty:
        return {"n": 0, "mean": None, "median": None, "p75": None, "p90": None}
    return {
        "n": int(len(z)),
        "mean": float(z.mean()),
        "median": float(z.median()),
        "p75": float(z.quantile(.75)),
        "p90": float(z.quantile(.90)),
    }


def expectancy(q: pd.DataFrame) -> dict[str, Any]:
    if q.empty:
        return {"legs": 0, "resolved": 0, "wins": 0, "losses": 0, "win_rate": None, "mean_unit_R": None, "gross_positive_R": 0.0, "gross_negative_R": 0.0, "capture_to_stop_loss_ratio": None}
    r = pd.to_numeric(q.realized_unit_R, errors="coerce").dropna()
    gp = float(r[r > 0].sum()) if len(r) else 0.0
    gn = float(-r[r < 0].sum()) if len(r) else 0.0
    return {
        "legs": int(len(q)),
        "resolved": int(len(r)),
        "wins": int((r > 0).sum()),
        "losses": int((r < 0).sum()),
        "win_rate": safe_rate(int((r > 0).sum()), len(r)),
        "mean_unit_R": float(r.mean()) if len(r) else None,
        "gross_positive_R": gp,
        "gross_negative_R": gn,
        "capture_to_stop_loss_ratio": safe_rate(gp, gn),
    }


def enrich_tf(raw: pd.DataFrame, tf_hours: int) -> pd.DataFrame:
    x = s6.add_tf_features(raw[["time","open","high","low","close","volume","quote_volume","taker_buy_quote"]].copy())
    if tf_hours == 1:
        x["atr14_1h"] = x["atr14"]
    return x


def resolve_with_time(h1: pd.DataFrame, entry_time: pd.Timestamp, direction: str, stop: float, target: float):
    end = min(entry_time + pd.Timedelta(days=90), END_EXCL)
    q = h1[(h1.time >= entry_time) & (h1.time < end)]
    for _, r in q.iterrows():
        if direction == "long":
            tp = float(r.high) >= target
            sl = float(r.low) <= stop
        else:
            tp = float(r.low) <= target
            sl = float(r.high) >= stop
        if tp and sl:
            return "AMBIGUOUS_SAME_BAR", None, pd.Timestamp(r.time)
        if tp:
            return "TP_3R", 3.0, pd.Timestamp(r.time)
        if sl:
            return "SL_1R", -1.0, pd.Timestamp(r.time)
    return "CENSORED_UNRESOLVED", None, None


def daily_snapshot(scored: pd.DataFrame, idx: int) -> dict[str, Any]:
    row = scored.iloc[int(idx)].to_dict()
    # Historical severe-event reconstruction is unavailable in R1.2 data.
    # For this DIAGNOSTIC replay it is explicitly N/A, not silently treated as a promotion PASS.
    row["severe_risk_veto"] = False
    return row


def prepare_h4(h4raw: pd.DataFrame, h1: pd.DataFrame) -> pd.DataFrame:
    h4 = enrich_tf(h4raw, 4).sort_values("time").reset_index(drop=True)
    a = h1[["time", "atr14_1h"]].dropna().sort_values("time")
    h4 = pd.merge_asof(h4.sort_values("time"), a, on="time", direction="backward")
    return h4


def scan_window(engine: R13Engine, tf: pd.DataFrame, *, engine_name: str, stage: int, direction: str, daily: dict[str, Any], start: pd.Timestamp, end: pd.Timestamp, zone_lo: float, zone_hi: float, daily_atr: float, tf_hours: int):
    q = tf[(tf.time >= start) & (tf.time < end)].reset_index(drop=True)
    if len(q) < 2:
        return None
    for i in range(len(q) - 1):
        candidate = q.iloc[i].to_dict()
        nxt = q.iloc[i + 1].to_dict()
        # Both completed bars must belong to the same daily-snapshot window.
        if pd.Timestamp(nxt["time"]) >= end:
            break
        try:
            dec = engine.evaluate_pair(
                engine=engine_name,
                stage=stage,
                direction=direction,
                daily=daily,
                candidate=candidate,
                nxt=nxt,
                zone_lo=zone_lo,
                zone_hi=zone_hi,
                daily_atr=daily_atr,
            )
        except ContractError:
            continue
        if dec.action == "ENTER":
            confirm_time = pd.Timestamp(nxt["time"]) + pd.Timedelta(hours=tf_hours)
            return dec, pd.Timestamp(candidate["time"]), confirm_time
    return None


def replay_episode(engine: R13Engine, e: pd.Series, g: pd.DataFrame, scored: pd.DataFrame, h1: pd.DataFrame, h4: pd.DataFrame):
    g = g.sort_values("date").copy()
    first = g.iloc[0]
    zone_lo, zone_hi = float(first.zone_lo), float(first.zone_hi)
    direction = str(e.direction)
    engine_name = str(e.engine)
    scout_known = pd.Timestamp(first.date) + pd.Timedelta(days=1)
    expiry = pd.Timestamp(g.date.max()) + pd.Timedelta(days=2)

    signal_windows = []
    rows = list(g.itertuples(index=False))
    for j, s in enumerate(rows):
        known = pd.Timestamp(s.date) + pd.Timedelta(days=1)
        nxt_known = (pd.Timestamp(rows[j + 1].date) + pd.Timedelta(days=1)) if j + 1 < len(rows) else expiry
        end = min(nxt_known, expiry)
        if known >= end:
            continue
        signal_windows.append((s, known, end))

    one = None
    one_daily = None
    one_stage = None
    one_risk_state = None
    for s, start, end in signal_windows:
        stage = int(s.stage)
        if not engine.execution_eligible(engine_name, stage):
            continue
        d = daily_snapshot(scored, int(s.idx))
        state = engine.risk_state(d)
        found = scan_window(engine, h1, engine_name=engine_name, stage=stage, direction=direction, daily=d, start=start, end=end, zone_lo=zone_lo, zone_hi=zone_hi, daily_atr=float(scored.iloc[int(s.idx)].atr14), tf_hours=1)
        if found:
            one = found
            one_daily = d
            one_stage = stage
            one_risk_state = state
            break

    if one is None:
        return {
            "episode_id": str(e.independent_episode_id), "family_id": str(e.family_id), "engine": engine_name, "direction": direction,
            "scout_known": scout_known, "expiry": expiry, "entry_1h": False, "add_4h": False, "confirm_1d": False,
            "first_route": None, "first_risk_state": None,
        }, []

    dec, reaction_time, confirm_time = one
    out, rr, rt = resolve_with_time(h1, confirm_time, direction, float(dec.stop), float(dec.target))
    legs = [{
        "episode_id": str(e.independent_episode_id), "family_id": str(e.family_id), "engine": engine_name, "direction": direction,
        "leg": "1H_ENTRY", "route": dec.route, "risk_state": dec.risk_state,
        "reaction_bar_time": reaction_time, "confirm_time": confirm_time, "entry": dec.entry, "stop": dec.stop, "target": dec.target, "risk": dec.risk,
        "quality_score": dec.quality_score, "independent_reasons": dec.independent_reasons, "safety_score": dec.safety_score,
        "outcome": out, "realized_unit_R": rr, "resolved_time": rt,
    }]

    four_found = None
    for s, start, end in signal_windows:
        stage = int(s.stage)
        if not engine.execution_eligible(engine_name, stage):
            continue
        d = daily_snapshot(scored, int(s.idx))
        wstart = max(start, confirm_time)
        if wstart >= end:
            continue
        found = scan_window(engine, h4, engine_name=engine_name, stage=stage, direction=direction, daily=d, start=wstart, end=end, zone_lo=zone_lo, zone_hi=zone_hi, daily_atr=float(scored.iloc[int(s.idx)].atr14), tf_hours=4)
        if found and found[2] > confirm_time:
            four_found = found
            break

    add4 = False
    four_confirm = pd.NaT
    if four_found:
        d4, r4time, c4time = four_found
        out4, rr4, rt4 = resolve_with_time(h1, c4time, direction, float(d4.stop), float(d4.target))
        legs.append({
            "episode_id": str(e.independent_episode_id), "family_id": str(e.family_id), "engine": engine_name, "direction": direction,
            "leg": "4H_ADD", "route": d4.route, "risk_state": d4.risk_state,
            "reaction_bar_time": r4time, "confirm_time": c4time, "entry": d4.entry, "stop": d4.stop, "target": d4.target, "risk": d4.risk,
            "quality_score": d4.quality_score, "independent_reasons": d4.independent_reasons, "safety_score": d4.safety_score,
            "outcome": out4, "realized_unit_R": rr4, "resolved_time": rt4,
        })
        add4 = True
        four_confirm = c4time

    confirm1d = False
    confirm1d_time = pd.NaT
    if add4:
        for s, _, _ in signal_windows:
            known = pd.Timestamp(s.date) + pd.Timedelta(days=1)
            if known <= four_confirm or known > expiry or int(s.stage) < 2:
                continue
            d = daily_snapshot(scored, int(s.idx))
            if engine.risk_state(d) == "BLOCK":
                continue
            sr = scored.iloc[int(s.idx)]
            favorable = bool(sr.close > sr.ema20) if direction == "long" else bool(sr.close < sr.ema20)
            if favorable:
                confirm1d = True
                confirm1d_time = known
                break

    state = {
        "episode_id": str(e.independent_episode_id), "family_id": str(e.family_id), "engine": engine_name, "direction": direction,
        "scout_known": scout_known, "expiry": expiry, "entry_1h": True, "add_4h": add4, "confirm_1d": confirm1d,
        "confirm_1d_time": confirm1d_time, "first_route": dec.route, "first_risk_state": one_risk_state,
        "first_confirm_time": confirm_time, "first_stage": one_stage,
    }
    return state, legs


def reconcile_metrics(ep: pd.DataFrame, truth: pd.DataFrame, sig: pd.DataFrame, scored: pd.DataFrame, state: pd.DataFrame, legs: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    oos = ep[(ep.start_date >= OOS_START) & (ep.start_date <= OOS_END)].copy()
    oos_ids = set(oos.independent_episode_id.astype(str))
    st = state[state.episode_id.astype(str).isin(oos_ids)].copy()
    lg = legs[legs.episode_id.astype(str).isin(oos_ids)].copy()

    for c in ["entry_1h", "add_4h", "confirm_1d"]:
        st[c] = as_bool(st[c])
    for c in ["confirm_time", "resolved_time", "reaction_bar_time"]:
        if c in lg.columns:
            lg[c] = pd.to_datetime(lg[c], utc=True, errors="coerce")
    for c in ["scout_known", "expiry", "first_confirm_time"]:
        if c in st.columns:
            st[c] = pd.to_datetime(st[c], utc=True, errors="coerce")

    first = lg[lg.leg.eq("1H_ENTRY")].sort_values("confirm_time").drop_duplicates("episode_id").copy()
    keep = ["episode_id","confirm_time","entry","stop","target","risk","outcome","realized_unit_R","resolved_time","route","risk_state"]
    for c in keep:
        if c not in first.columns:
            first[c] = np.nan
    first = first[keep].rename(columns={c: f"first_{c}" for c in keep if c != "episode_id"})

    t = truth.rename(columns={"independent_episode_id": "episode_id"}).copy()
    t["episode_id"] = t.episode_id.astype(str)
    for c in ["available_90d", "available_365d"]:
        t[c] = as_bool(t[c])

    e = oos.rename(columns={"independent_episode_id": "episode_id"}).copy()
    e["episode_id"] = e.episode_id.astype(str)
    m = e.merge(st, on=["episode_id","family_id","engine","direction"], how="left", validate="one_to_one")
    m = m.merge(t[["episode_id","available_90d","available_365d","medium_truth_status","long_truth_status","MFE_90d","MFE_365d"]], on="episode_id", how="left", validate="one_to_one")
    m = m.merge(first, on="episode_id", how="left", validate="one_to_one")

    fsig = sig.sort_values(["idx","engine"]).drop_duplicates(["family_id","engine"], keep="first")[["family_id","engine","zone_lo","zone_hi","idx"]].copy()
    fsig["start_atr14"] = [float(scored.iloc[int(i)].atr14) for i in fsig.idx]
    fsig = fsig.drop(columns=["idx"])
    m = m.merge(fsig, on=["family_id","engine"], how="left", validate="many_to_one")

    m["entry_lag_hours"] = (pd.to_datetime(m.first_confirm_time, utc=True, errors="coerce") - pd.to_datetime(m.scout_known, utc=True, errors="coerce")).dt.total_seconds() / 3600.0
    def ext(r):
        if pd.isna(r.first_entry) or pd.isna(r.start_atr14) or float(r.start_atr14) <= 0:
            return np.nan
        return max(0.0, float(r.first_entry) - float(r.zone_hi)) / float(r.start_atr14) if r.direction == "long" else max(0.0, float(r.zone_lo) - float(r.first_entry)) / float(r.start_atr14)
    m["entry_extension_atr"] = m.apply(ext, axis=1)

    med_success = m.medium_truth_status.isin(["MEDIUM_SUCCESS_20", "MEDIUM_SUCCESS_30"])
    long_success = m.long_truth_status.isin(["LONG_SUCCESS_PRIMARY", "LONG_SUCCESS_EXTENSION"])
    m["execution_false_start"] = m.first_outcome.eq("SL_1R")
    m["confirmed_false_start_90d"] = m.execution_false_start & m.available_90d & m.medium_truth_status.eq("NO_MEDIUM_TARGET")
    m["stop_then_medium_trend"] = m.execution_false_start & m.available_90d & med_success
    m["missed_medium"] = (~m.entry_1h.fillna(False)) & m.available_90d & med_success
    m["missed_long"] = (~m.entry_1h.fillna(False)) & m.available_365d & long_success

    m["capture_return"] = 0.0
    tp = m.first_outcome.eq("TP_3R") & m.first_entry.notna() & m.first_target.notna()
    m.loc[tp, "capture_return"] = (m.loc[tp, "first_target"] - m.loc[tp, "first_entry"]).abs() / m.loc[tp, "start_close"].astype(float)
    m["mcr90"] = np.nan
    mm90 = m.available_90d & med_success & pd.to_numeric(m.MFE_90d, errors="coerce").gt(0)
    m.loc[mm90, "mcr90"] = np.minimum(1.0, m.loc[mm90, "capture_return"] / pd.to_numeric(m.loc[mm90, "MFE_90d"], errors="coerce"))
    m["mcr365"] = np.nan
    mm365 = m.available_365d & long_success & pd.to_numeric(m.MFE_365d, errors="coerce").gt(0)
    m.loc[mm365, "mcr365"] = np.minimum(1.0, m.loc[mm365, "capture_return"] / pd.to_numeric(m.loc[mm365, "MFE_365d"], errors="coerce"))

    executed_complete90 = int((m.entry_1h.fillna(False) & m.available_90d).sum())
    confirmed_fs = int(m.confirmed_false_start_90d.sum())
    med_truth_n = int((m.available_90d & med_success).sum())
    long_truth_n = int((m.available_365d & long_success).sum())
    med_entry = int((m.entry_1h.fillna(False) & m.available_90d & med_success).sum())

    summary = {
        "episodes": int(len(m)),
        "first_entries": int(m.entry_1h.fillna(False).sum()),
        "adds_4h": int(m.add_4h.fillna(False).sum()),
        "confirm_1d": int(m.confirm_1d.fillna(False).sum()),
        "all_legs": expectancy(lg),
        "first_1h": expectancy(lg[lg.leg.eq("1H_ENTRY")]),
        "direction": {str(k): expectancy(q) for k, q in lg.groupby("direction")},
        "engine": {str(k): expectancy(q) for k, q in lg.groupby("engine")},
        "route": {str(k): expectancy(q) for k, q in lg.groupby("route")} if "route" in lg.columns else {},
        "first_route_counts": {str(k): int(v) for k, v in m.first_route.value_counts(dropna=False).items()} if "first_route" in m.columns else {},
        "risk_state_counts": {str(k): int(v) for k, v in m.first_risk_state.value_counts(dropna=False).items()} if "first_risk_state" in m.columns else {},
        "false_start": {
            "execution_stop_count": int(m.execution_false_start.sum()),
            "execution_stop_rate": safe_rate(int(m.execution_false_start.sum()), int(m.entry_1h.fillna(False).sum())),
            "confirmed_false_start_count": confirmed_fs,
            "executed_complete_90d": executed_complete90,
            "confirmed_false_start_rate": safe_rate(confirmed_fs, executed_complete90),
            "predeclared_max": 0.50,
            "predeclared_gate_pass": bool(executed_complete90 and confirmed_fs / executed_complete90 <= 0.50),
            "stop_then_medium_trend": int(m.stop_then_medium_trend.sum()),
        },
        "missed_trend": {
            "medium_truth_positive": med_truth_n,
            "medium_no_entry_missed": int(m.missed_medium.sum()),
            "medium_missed_rate": safe_rate(int(m.missed_medium.sum()), med_truth_n),
            "medium_entry_coverage": safe_rate(med_entry, med_truth_n),
            "long_truth_positive": long_truth_n,
            "long_no_entry_missed": int(m.missed_long.sum()),
            "long_missed_rate": safe_rate(int(m.missed_long.sum()), long_truth_n),
        },
        "mcr": {"90d": qstats(m.loc[mm90, "mcr90"]), "365d": qstats(m.loc[mm365, "mcr365"])},
        "localization": {"entry_lag_hours": qstats(m.loc[m.entry_1h.fillna(False), "entry_lag_hours"]), "entry_extension_atr": qstats(m.loc[m.entry_1h.fillna(False), "entry_extension_atr"])},
    }
    return m, summary


def main() -> None:
    required = [DATA1D, EP, SIG, TRUTH, R12_STATE, R12_LEGS, H1_RAW, H4_RAW]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise RuntimeError(f"Required baseline outputs missing: {missing}")

    engine = R13Engine()
    raw1d = pd.read_csv(DATA1D)
    scored = s2.build(raw1d)
    ep = pd.read_csv(EP)
    sig = pd.read_csv(SIG)
    truth = pd.read_csv(TRUTH)
    r12_state = pd.read_csv(R12_STATE)
    r12_legs = pd.read_csv(R12_LEGS)
    h1raw = pd.read_csv(H1_RAW)
    h4raw = pd.read_csv(H4_RAW)

    ep["start_date"] = pd.to_datetime(ep.start_date, utc=True)
    sig["date"] = pd.to_datetime(sig.date, utc=True)
    h1raw["time"] = pd.to_datetime(h1raw.time, utc=True)
    h4raw["time"] = pd.to_datetime(h4raw.time, utc=True)
    h1 = enrich_tf(h1raw, 1)
    h4 = prepare_h4(h4raw, h1)

    states, legs = [], []
    for _, e in ep.iterrows():
        g = sig[(sig.family_id.astype(str) == str(e.family_id)) & (sig.engine.astype(str) == str(e.engine))].copy()
        if g.empty:
            raise RuntimeError(f"Episode without signals: {e.independent_episode_id}")
        st, lg = replay_episode(engine, e, g, scored, h1, h4)
        states.append(st)
        legs.extend(lg)

    r13_state = pd.DataFrame(states)
    r13_legs = pd.DataFrame(legs)
    if r13_legs.empty:
        r13_legs = pd.DataFrame(columns=["episode_id","family_id","engine","direction","leg","route","risk_state","reaction_bar_time","confirm_time","entry","stop","target","risk","quality_score","independent_reasons","safety_score","outcome","realized_unit_R","resolved_time"])
    r13_state.to_csv(OUT / "r13_episode_state_replay.csv", index=False)
    r13_legs.to_csv(OUT / "r13_trade_legs.csv", index=False)

    r12_m, r12 = reconcile_metrics(ep, truth, sig, scored, r12_state, r12_legs)
    r13_m, r13 = reconcile_metrics(ep, truth, sig, scored, r13_state, r13_legs)
    r12_m.to_csv(OUT / "r12_episode_metrics_recomputed.csv", index=False)
    r13_m.to_csv(OUT / "r13_episode_metrics.csv", index=False)

    # Baseline identity gate. If these fail, R1.3 comparison is invalid.
    baseline_checks = {
        "oos_episodes_136": r12["episodes"] == 136,
        "r12_first_entries_64": r12["first_entries"] == 64,
        "r12_all_legs_119": r12["all_legs"]["legs"] == 119,
        "r12_all_leg_mean_R_exact": math.isclose(float(r12["all_legs"]["mean_unit_R"]), 0.07563025210084033, rel_tol=0, abs_tol=1e-12),
        "r12_first_mean_R_exact": math.isclose(float(r12["first_1h"]["mean_unit_R"]), 0.0625, rel_tol=0, abs_tol=1e-12),
        "r12_mcr90_identity": math.isclose(float(r12["mcr"]["90d"]["mean"]), 0.0711657, rel_tol=0, abs_tol=5e-7),
        "r12_mcr365_identity": math.isclose(float(r12["mcr"]["365d"]["mean"]), 0.0378854, rel_tol=0, abs_tol=5e-7),
        "r12_medium_missed_35": r12["missed_trend"]["medium_no_entry_missed"] == 35,
        "r12_long_missed_33": r12["missed_trend"]["long_no_entry_missed"] == 33,
    }
    if not all(baseline_checks.values()):
        raise RuntimeError(f"R1.2 baseline identity mismatch: {baseline_checks}")

    def delta(a, b):
        if a is None or b is None:
            return None
        return float(b - a)

    comparison = {
        "first_entries": {"r12": r12["first_entries"], "r13": r13["first_entries"], "delta": r13["first_entries"] - r12["first_entries"]},
        "all_leg_mean_R": {"r12": r12["all_legs"]["mean_unit_R"], "r13": r13["all_legs"]["mean_unit_R"], "delta": delta(r12["all_legs"]["mean_unit_R"], r13["all_legs"]["mean_unit_R"])},
        "first_1h_mean_R": {"r12": r12["first_1h"]["mean_unit_R"], "r13": r13["first_1h"]["mean_unit_R"], "delta": delta(r12["first_1h"]["mean_unit_R"], r13["first_1h"]["mean_unit_R"])},
        "mcr90_mean": {"r12": r12["mcr"]["90d"]["mean"], "r13": r13["mcr"]["90d"]["mean"], "delta": delta(r12["mcr"]["90d"]["mean"], r13["mcr"]["90d"]["mean"])},
        "mcr365_mean": {"r12": r12["mcr"]["365d"]["mean"], "r13": r13["mcr"]["365d"]["mean"], "delta": delta(r12["mcr"]["365d"]["mean"], r13["mcr"]["365d"]["mean"])},
        "medium_missed_rate": {"r12": r12["missed_trend"]["medium_missed_rate"], "r13": r13["missed_trend"]["medium_missed_rate"], "delta": delta(r12["missed_trend"]["medium_missed_rate"], r13["missed_trend"]["medium_missed_rate"])},
        "long_missed_rate": {"r12": r12["missed_trend"]["long_missed_rate"], "r13": r13["missed_trend"]["long_missed_rate"], "delta": delta(r12["missed_trend"]["long_missed_rate"], r13["missed_trend"]["long_missed_rate"])},
        "median_entry_lag_h": {"r12": r12["localization"]["entry_lag_hours"]["median"], "r13": r13["localization"]["entry_lag_hours"]["median"], "delta": delta(r12["localization"]["entry_lag_hours"]["median"], r13["localization"]["entry_lag_hours"]["median"])},
        "confirmed_false_start_rate": {"r12": r12["false_start"]["confirmed_false_start_rate"], "r13": r13["false_start"]["confirmed_false_start_rate"], "delta": delta(r12["false_start"]["confirmed_false_start_rate"], r13["false_start"]["confirmed_false_start_rate"])},
    }

    report = {
        "model": "MASTER_BTC_TREND_V3_R1_3",
        "status": "HISTORICAL_DIAGNOSTIC_ONLY_NOT_UNTOUCHED_OOS",
        "period": {"start": OOS_START.isoformat(), "end": OOS_END.isoformat()},
        "baseline_identity_gate": baseline_checks,
        "baseline_identity_pass": bool(all(baseline_checks.values())),
        "r12": r12,
        "r13": r13,
        "comparison": comparison,
        "interpretation_guardrails": {
            "r13_rules_changed_after_seeing_this_replay": False,
            "this_replay_can_promote_to_production": False,
            "forward_untouched_oos_starts": "2026-09-05T00:00:00Z",
            "severe_event_risk_history": "N/A_NOT_RECONSTRUCTED__DIAGNOSTIC_ONLY",
            "portfolio_mdd": "N/A_ALLOCATION_MODEL_NOT_DEFINED",
            "probability": "확률 산출보류",
        },
    }
    (OUT / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
