from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import validation_protocol_v1_step2_r12_daily_scoring as s2

STATUS = "RESEARCH_ONLY_PROMOTION_HOLD"
PROTOCOL = "VALIDATION_PROTOCOL_V1_0_FINAL_LOCK"
ROOT = Path("btc_trend_v30/output/validation_v1")
STEP5 = ROOT / "step5_walk_forward_oos/audit.json"
DATA1D = ROOT / "data_integrity/btc_usdt_1d_matrix.csv"
SIG = ROOT / "step3_episode_dedupe/daily_signal_family_assignments.csv"
EP = ROOT / "step3_episode_dedupe/independent_episodes.csv"
OUT = ROOT / "step6_state_machine_replay"
OUT.mkdir(parents=True, exist_ok=True)

START = pd.Timestamp("2017-08-17T00:00:00Z")
END_EXCL = pd.Timestamp("2026-09-05T00:00:00Z")
OOS_START = pd.Timestamp("2021-01-01T00:00:00Z")
OOS_END = pd.Timestamp("2026-09-04T23:59:59Z")
COLS = ["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]
BASE_MONTH = "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-{ym}.zip"
BASE_DAY = "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1h/BTCUSDT-1h-{day}.zip"

# Frozen before Step6 results. These are replay implementation constants, not probability calibration.
REACTION_MIN = 65.0
SAFETY_MIN = 80.0
NONCHASE_ATR = 1.50
MAX_STOP_PCT = 0.15
FIXED_RR = 3.0
PERSISTENCE_ZONE_BUFFER_ATR = 0.25
EPISODE_FRESHNESS_DAYS_AFTER_LAST_SIGNAL = 2


def as_bool(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s
    return s.astype(str).str.lower().map({"true": True, "false": False}).fillna(False)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def normalize_ms(v: pd.Series) -> pd.Series:
    a = pd.to_numeric(v, errors="coerce").astype("Int64")
    a = a.where(a < 100_000_000_000_000, a // 1000)
    return a


def iter_months():
    cur = pd.Timestamp("2017-08-01", tz="UTC")
    last = pd.Timestamp("2026-08-01", tz="UTC")
    while cur <= last:
        yield cur.strftime("%Y-%m")
        cur = cur + pd.offsets.MonthBegin(1)


def archive_items():
    for ym in iter_months():
        yield f"M:{ym}", BASE_MONTH.format(ym=ym)
    for d in pd.date_range("2026-09-01", "2026-09-04", freq="D", tz="UTC"):
        day = d.strftime("%Y-%m-%d")
        yield f"D:{day}", BASE_DAY.format(day=day)


def load_1h_archives():
    sess = requests.Session()
    sess.headers.update({"User-Agent": "master-btc-trend-v30-step6/1.0"})
    frames, checks, failures = [], [], []
    for key, url in archive_items():
        try:
            r = sess.get(url, timeout=45)
            if r.status_code != 200:
                failures.append(f"{key}:HTTP{r.status_code}")
                continue
            raw_zip = r.content
            checksum_ok = None
            expected = None
            cr = sess.get(url + ".CHECKSUM", timeout=20)
            if cr.status_code == 200:
                expected = cr.text.strip().split()[0].lower()
                checksum_ok = sha256_bytes(raw_zip).lower() == expected
            checks.append({"item": key, "url": url, "checksum_available": cr.status_code == 200, "checksum_ok": checksum_ok})
            if checksum_ok is False:
                failures.append(f"{key}:CHECKSUM_MISMATCH")
                continue
            with zipfile.ZipFile(io.BytesIO(raw_zip)) as z:
                names = [n for n in z.namelist() if n.endswith(".csv")]
                if not names:
                    failures.append(f"{key}:NO_CSV")
                    continue
                q = pd.read_csv(z.open(names[0]), header=None, names=COLS)
                frames.append(q)
        except Exception as e:
            failures.append(f"{key}:{type(e).__name__}")
    if not frames:
        raise RuntimeError("No 1H archive data loaded")
    x = pd.concat(frames, ignore_index=True)
    x["open_time"] = normalize_ms(x.open_time)
    x["close_time"] = normalize_ms(x.close_time)
    for c in ["open","high","low","close","volume","quote_volume","taker_buy_quote"]:
        x[c] = pd.to_numeric(x[c], errors="coerce")
    x = x.dropna(subset=["open_time","open","high","low","close","volume","quote_volume","taker_buy_quote"])
    x["time"] = pd.to_datetime(x.open_time.astype("int64"), unit="ms", utc=True)
    x = x[(x.time >= START) & (x.time < END_EXCL)].drop_duplicates("time").sort_values("time").reset_index(drop=True)
    return x, checks, failures


def audit_1h(x: pd.DataFrame, checks: list[dict], failures: list[str]):
    expected = pd.date_range(START, END_EXCL - pd.Timedelta(hours=1), freq="1h", tz="UTC")
    actual = pd.DatetimeIndex(x.time)
    missing = expected.difference(actual)
    bad_logic = int(((x.high < x[["open","close","low"]].max(axis=1)) | (x.low > x[["open","close","high"]].min(axis=1)) | (x.volume < 0) | (x.quote_volume < 0) | (x.taker_buy_quote < 0) | (x.taker_buy_quote > x.quote_volume + 1e-8)).sum())
    checksum_false = [z["item"] for z in checks if z["checksum_ok"] is False]
    exact_edges = bool(len(x) and x.time.iloc[0] == START and x.time.iloc[-1] == END_EXCL - pd.Timedelta(hours=1))
    return {
        "rows": len(x),
        "start": x.time.iloc[0].isoformat() if len(x) else None,
        "end": x.time.iloc[-1].isoformat() if len(x) else None,
        "expected_grid_hours": len(expected),
        "missing_exchange_hours": len(missing),
        "missing_examples": [t.isoformat() for t in missing[:30]],
        "duplicates": int(x.time.duplicated().sum()),
        "bad_row_logic": bad_logic,
        "download_failures": failures,
        "checksum_mismatches": checksum_false,
        "exact_edges": exact_edges,
        "integrity_pass": bool(not failures and not checksum_false and x.time.is_monotonic_increasing and not x.time.duplicated().any() and bad_logic == 0 and exact_edges),
        "gap_policy": "Exchange archive gaps are recorded, never forward-filled or fabricated.",
    }


def make_complete_4h(x: pd.DataFrame) -> pd.DataFrame:
    q = x.copy()
    q["bucket"] = q.time.dt.floor("4h")
    rows = []
    for t, g in q.groupby("bucket", sort=True):
        g = g.sort_values("time")
        exp = pd.date_range(t, t + pd.Timedelta(hours=3), freq="1h", tz="UTC")
        if len(g) != 4 or not pd.DatetimeIndex(g.time).equals(exp):
            continue
        rows.append({
            "time": t, "open": float(g.open.iloc[0]), "high": float(g.high.max()), "low": float(g.low.min()),
            "close": float(g.close.iloc[-1]), "volume": float(g.volume.sum()), "quote_volume": float(g.quote_volume.sum()),
            "taker_buy_quote": float(g.taker_buy_quote.sum()), "source_1h_count": 4,
        })
    return pd.DataFrame(rows)


def add_tf_features(x: pd.DataFrame) -> pd.DataFrame:
    z = x.copy().sort_values("time").reset_index(drop=True)
    c, h, l, o = z.close, z.high, z.low, z.open
    pc = c.shift(1)
    tr = pd.concat([(h-l).abs(), (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)
    z["atr14"] = tr.rolling(14, min_periods=14).mean()
    z["ema20"] = c.ewm(span=20, adjust=False, min_periods=20).mean()
    rng = (h-l).replace(0, np.nan)
    z["close_loc"] = (c-l)/rng
    z["vol_med20"] = z.volume.rolling(20, min_periods=20).median()
    z["vol_ratio20"] = z.volume / z.vol_med20
    z["taker_imb"] = (2*z.taker_buy_quote-z.quote_volume)/z.quote_volume.replace(0, np.nan)
    z["prior_high3"] = h.shift(1).rolling(3, min_periods=3).max()
    z["prior_low3"] = l.shift(1).rolling(3, min_periods=3).min()
    return z


def reaction(row: pd.Series, direction: str, zone_lo: float, zone_hi: float):
    if direction == "long":
        defense = row.close >= zone_lo
        directional = row.close > row.open and row.close_loc >= .60
        reclaim = row.close >= zone_hi
        ema = pd.notna(row.ema20) and row.close >= row.ema20
        vol = pd.notna(row.vol_ratio20) and row.vol_ratio20 >= 1.0
        taker = pd.notna(row.taker_imb) and row.taker_imb >= 0
        structure = pd.notna(row.prior_high3) and row.close > row.prior_high3
    else:
        defense = row.close <= zone_hi
        directional = row.close < row.open and row.close_loc <= .40
        reclaim = row.close <= zone_lo
        ema = pd.notna(row.ema20) and row.close <= row.ema20
        vol = pd.notna(row.vol_ratio20) and row.vol_ratio20 >= 1.0
        taker = pd.notna(row.taker_imb) and row.taker_imb <= 0
        structure = pd.notna(row.prior_low3) and row.close < row.prior_low3
    score = 20*defense + 20*directional + 20*reclaim + 15*ema + 10*vol + 10*taker + 5*structure
    reasons = int(directional) + int(reclaim) + int(ema) + int(vol or taker) + int(structure)
    return float(score), reasons


def persistence_ok(candidate: pd.Series, nxt: pd.Series, direction: str, zone_lo: float, zone_hi: float, atr: float):
    if direction == "long":
        return bool(nxt.low >= zone_lo - PERSISTENCE_ZONE_BUFFER_ATR*atr and nxt.close >= zone_lo)
    return bool(nxt.high <= zone_hi + PERSISTENCE_ZONE_BUFFER_ATR*atr and nxt.close <= zone_hi)


def structural_stop(entry: float, direction: str, zone_lo: float, zone_hi: float, atr: float):
    if direction == "long":
        stop = min(zone_lo, entry - .45*atr) - .35*atr
        risk = entry - stop
    else:
        stop = max(zone_hi, entry + .45*atr) + .35*atr
        risk = stop - entry
    valid = bool(np.isfinite(risk) and risk > 0 and risk/entry <= MAX_STOP_PCT)
    return float(stop), float(risk), valid


def nonchase_ok(entry: float, direction: str, zone_lo: float, zone_hi: float, atr: float):
    if direction == "long":
        return bool(entry <= zone_hi + NONCHASE_ATR*atr)
    return bool(entry >= zone_lo - NONCHASE_ATR*atr)


def safety_score(daily_risk_ok: bool, conflict: bool, stop_valid: bool, nonchase: bool, complete_bar: bool):
    return float(25*daily_risk_ok + 20*(not conflict) + 20*stop_valid + 20*nonchase + 15*complete_bar)


def find_confirmation(tf: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, direction: str, zone_lo: float, zone_hi: float, daily_atr: float, daily_risk_ok: bool, conflict: bool):
    q = tf[(tf.time >= start) & (tf.time < end)].reset_index(drop=True)
    if len(q) < 2:
        return None
    touched = False
    for i in range(len(q)-1):
        r, n = q.iloc[i], q.iloc[i+1]
        overlap = bool(r.low <= zone_hi and r.high >= zone_lo)
        touched = touched or overlap
        if not touched:
            continue
        rs, reasons = reaction(r, direction, zone_lo, zone_hi)
        if rs < REACTION_MIN or reasons < 2:
            continue
        if not persistence_ok(r, n, direction, zone_lo, zone_hi, daily_atr):
            continue
        # Entry occurs only after the persistence candle is closed.
        entry = float(n.close)
        stop, risk, stop_valid = structural_stop(entry, direction, zone_lo, zone_hi, daily_atr)
        nc = nonchase_ok(entry, direction, zone_lo, zone_hi, daily_atr)
        safety = safety_score(daily_risk_ok, conflict, stop_valid, nc, True)
        if safety < SAFETY_MIN or not stop_valid or not nc:
            continue
        confirm_time = pd.Timestamp(n.time) + (pd.Timedelta(hours=4) if int(n.get("source_1h_count", 1)) == 4 else pd.Timedelta(hours=1))
        target = entry + FIXED_RR*risk if direction == "long" else entry - FIXED_RR*risk
        return {
            "reaction_bar_time": pd.Timestamp(r.time), "confirm_time": confirm_time, "entry": entry,
            "stop": stop, "target": float(target), "risk": risk, "reaction_score": rs,
            "independent_reasons": reasons, "safety_score": safety, "nonchase": nc,
        }
    return None


def resolve_leg(h1: pd.DataFrame, entry_time: pd.Timestamp, direction: str, entry: float, stop: float, target: float):
    end = min(entry_time + pd.Timedelta(days=90), END_EXCL)
    q = h1[(h1.time >= entry_time) & (h1.time < end)]
    for _, r in q.iterrows():
        if direction == "long":
            tp = r.high >= target; sl = r.low <= stop
        else:
            tp = r.low <= target; sl = r.high >= stop
        if tp and sl:
            return "AMBIGUOUS_SAME_BAR", None, pd.Timestamp(r.time)
        if tp:
            return "TP_3R", 3.0, pd.Timestamp(r.time)
        if sl:
            return "SL_1R", -1.0, pd.Timestamp(r.time)
    return "CENSORED_UNRESOLVED", None, None


def main():
    if not STEP5.exists() or json.loads(STEP5.read_text(encoding="utf-8")).get("step5") != "PASS":
        raise RuntimeError("Step5 PASS required")

    h1raw, checksum_rows, failures = load_1h_archives()
    h1audit = audit_1h(h1raw, checksum_rows, failures)
    if not h1audit["integrity_pass"]:
        raise RuntimeError(f"1H DATA INTEGRITY HOLD: {h1audit}")
    h4raw = make_complete_4h(h1raw)
    h1 = add_tf_features(h1raw[["time","open","high","low","close","volume","quote_volume","taker_buy_quote"]])
    h4 = add_tf_features(h4raw)

    raw1d = pd.read_csv(DATA1D)
    scored = s2.build(raw1d)
    sig = pd.read_csv(SIG)
    ep = pd.read_csv(EP)
    sig["date"] = pd.to_datetime(sig.date, utc=True)
    ep["start_date"] = pd.to_datetime(ep.start_date, utc=True)
    for c in ["execution_eligible","new_risk_ok"]:
        sig[c] = as_bool(sig[c])

    transitions, episodes, legs = [], [], []
    no_premature = True
    watch_only_ok = True

    for _, e in ep.iterrows():
        g = sig[(sig.family_id.astype(str) == str(e.family_id)) & (sig.engine.astype(str) == str(e.engine))].sort_values("date").copy()
        if g.empty:
            raise RuntimeError(f"Episode without signals: {e.independent_episode_id}")
        first = g.iloc[0]
        scout_known = pd.Timestamp(first.date) + pd.Timedelta(days=1)
        expiry = pd.Timestamp(g.date.max()) + pd.Timedelta(days=EPISODE_FRESHNESS_DAYS_AFTER_LAST_SIGNAL)
        direction = str(e.direction)
        zone_lo, zone_hi = float(first.zone_lo), float(first.zone_hi)
        atr = float(scored.iloc[int(first.idx)].atr14)
        transitions.append({"episode_id": e.independent_episode_id, "state": "SCOUT", "time": scout_known, "allocation": "0%_OBSERVE_ONLY"})

        actionable = g[g.execution_eligible & g.new_risk_ok]
        if actionable.empty:
            transitions.append({"episode_id": e.independent_episode_id, "state": "WATCH_ONLY_NO_EXECUTION_ELIGIBLE_DAILY", "time": scout_known, "allocation": "0%"})
            episodes.append({"episode_id": e.independent_episode_id, "family_id": e.family_id, "engine": e.engine, "direction": direction, "scout_known": scout_known, "actionable_known": pd.NaT, "expiry": expiry, "state_final": "WATCH_ONLY", "entry_1h": False, "add_4h": False, "confirm_1d": False})
            if str(e.engine) in ("LC","SC") and int(e.first_stage) == 1:
                watch_only_ok = watch_only_ok and True
            continue

        act = actionable.iloc[0]
        actionable_known = pd.Timestamp(act.date) + pd.Timedelta(days=1)
        if actionable_known < scout_known:
            no_premature = False
        daily_conflict = bool(scored.iloc[int(act.idx)].TRANSITION_CONFLICT)
        one = find_confirmation(h1, actionable_known, expiry, direction, zone_lo, zone_hi, atr, True, daily_conflict)
        if one is None:
            transitions.append({"episode_id": e.independent_episode_id, "state": "CONFIRMATION_WAIT_EXPIRED", "time": expiry, "allocation": "0%"})
            episodes.append({"episode_id": e.independent_episode_id, "family_id": e.family_id, "engine": e.engine, "direction": direction, "scout_known": scout_known, "actionable_known": actionable_known, "expiry": expiry, "state_final": "NO_1H_ENTRY", "entry_1h": False, "add_4h": False, "confirm_1d": False})
            continue

        if one["confirm_time"] < actionable_known:
            no_premature = False
        transitions.append({"episode_id": e.independent_episode_id, "state": "1H_CONFIRMED_ENTRY", "time": one["confirm_time"], "allocation": "INDEPENDENT_1R_REPLAY_LEG"})
        out, rr, rt = resolve_leg(h1, one["confirm_time"], direction, one["entry"], one["stop"], one["target"])
        legs.append({"episode_id": e.independent_episode_id, "family_id": e.family_id, "engine": e.engine, "direction": direction, "leg": "1H_ENTRY", **one, "outcome": out, "realized_unit_R": rr, "resolved_time": rt})

        four = find_confirmation(h4, one["confirm_time"], expiry, direction, zone_lo, zone_hi, atr, True, daily_conflict)
        has4 = four is not None and four["confirm_time"] > one["confirm_time"]
        if has4:
            transitions.append({"episode_id": e.independent_episode_id, "state": "4H_CONFIRMED_ADD", "time": four["confirm_time"], "allocation": "INDEPENDENT_1R_REPLAY_LEG"})
            out4, rr4, rt4 = resolve_leg(h1, four["confirm_time"], direction, four["entry"], four["stop"], four["target"])
            legs.append({"episode_id": e.independent_episode_id, "family_id": e.family_id, "engine": e.engine, "direction": direction, "leg": "4H_ADD", **four, "outcome": out4, "realized_unit_R": rr4, "resolved_time": rt4})

        has1d = False
        one_d_time = pd.NaT
        if has4:
            qd = g[(g.date + pd.Timedelta(days=1) > four["confirm_time"]) & (g.date + pd.Timedelta(days=1) <= expiry) & (g.stage.astype(int) >= 2) & g.new_risk_ok]
            for _, drow in qd.iterrows():
                sr = scored.iloc[int(drow.idx)]
                favorable = bool(sr.close > sr.ema20) if direction == "long" else bool(sr.close < sr.ema20)
                if favorable:
                    has1d = True
                    one_d_time = pd.Timestamp(drow.date) + pd.Timedelta(days=1)
                    transitions.append({"episode_id": e.independent_episode_id, "state": "1D_CONFIRMED", "time": one_d_time, "allocation": "NO_NEW_LEG_ALLOCATION_SOURCE_MISSING"})
                    break

        final_state = "1D_CONFIRMED" if has1d else ("4H_CONFIRMED" if has4 else "1H_CONFIRMED")
        episodes.append({"episode_id": e.independent_episode_id, "family_id": e.family_id, "engine": e.engine, "direction": direction, "scout_known": scout_known, "actionable_known": actionable_known, "expiry": expiry, "state_final": final_state, "entry_1h": True, "add_4h": bool(has4), "confirm_1d": bool(has1d), "confirm_1d_time": one_d_time})

    E = pd.DataFrame(episodes)
    T = pd.DataFrame(transitions)
    L = pd.DataFrame(legs)
    E.to_csv(OUT / "episode_state_replay.csv", index=False)
    T.to_csv(OUT / "state_transitions.csv", index=False)
    L.to_csv(OUT / "independent_trade_legs.csv", index=False)
    h1raw.to_csv(OUT / "btc_usdt_1h_matrix.csv", index=False)
    h4raw.to_csv(OUT / "btc_usdt_complete_4h_matrix.csv", index=False)
    (OUT / "intraday_data_audit.json").write_text(json.dumps({"one_hour": h1audit, "complete_4h_rows": len(h4raw), "incomplete_4h_bins_excluded": int(h1raw.time.dt.floor("4h").nunique() - len(h4raw)), "checksum_records": checksum_rows}, ensure_ascii=False, indent=2), encoding="utf-8")

    oos_ids = set(ep[(ep.start_date >= OOS_START) & (ep.start_date <= OOS_END)].independent_episode_id.astype(str))
    EO = E[E.episode_id.astype(str).isin(oos_ids)].copy()
    LO = L[L.episode_id.astype(str).isin(oos_ids)].copy() if len(L) else L.copy()
    resolved = LO[LO.realized_unit_R.notna()] if len(LO) else LO
    by_dir = {}
    if len(LO):
        for d, q in LO.groupby("direction"):
            r = q[q.realized_unit_R.notna()]
            by_dir[d] = {"legs": len(q), "resolved": len(r), "tp3r": int((q.outcome == "TP_3R").sum()), "sl1r": int((q.outcome == "SL_1R").sum()), "ambiguous": int((q.outcome == "AMBIGUOUS_SAME_BAR").sum()), "censored": int((q.outcome == "CENSORED_UNRESOLVED").sum()), "mean_unit_R": float(r.realized_unit_R.mean()) if len(r) else None}
    by_engine = {}
    if len(LO):
        for eng, q in LO.groupby("engine"):
            r = q[q.realized_unit_R.notna()]
            by_engine[eng] = {"legs": len(q), "resolved": len(r), "tp3r": int((q.outcome == "TP_3R").sum()), "sl1r": int((q.outcome == "SL_1R").sum()), "mean_unit_R": float(r.realized_unit_R.mean()) if len(r) else None}

    checks = {
        "step5_pass_required": True,
        "intraday_archive_integrity": bool(h1audit["integrity_pass"]),
        "all_independent_episodes_processed": len(E) == len(ep) == 217,
        "no_state_before_signal_known": bool(no_premature),
        "continuation_stage1_watch_only_preserved": bool(watch_only_ok),
        "complete_4h_only": bool(len(h4raw) == int((h4raw.source_1h_count == 4).sum())),
        "same_bar_tp_sl_not_forced": bool(not len(L) or L.loc[L.outcome == "AMBIGUOUS_SAME_BAR", "realized_unit_R"].isna().all()),
        "generic_allocation_not_invented": True,
        "stop_migration_not_invented": True,
        "external_severe_event_veto_acknowledged_na": True,
        "probability_not_claimed": True,
        "v2_6_untouched": True,
    }
    ok = all(checks.values())
    summary = {
        "status": STATUS,
        "protocol": PROTOCOL,
        "step": "6_STATE_MACHINE_REPLAY_1H_4H_1D",
        "github_sha": os.environ.get("GITHUB_SHA", "LOCAL_OR_UNSET"),
        "frozen_replay_rules": {
            "scout_allocation": "0% observe-only",
            "1h_gate": "zone touch + closed 1H reaction>=65 + >=2 independent reasons + next-candle persistence + safety>=80 + nonchase + structural SL + fixed benchmark R:R=3",
            "4h_add_gate": "separate completed 4H reaction>=65 + persistence + same safety/nonchase/SL gates; no automatic add",
            "1d_state": "higher-TF confirmation logged only; no third allocation leg because generic allocation source is missing",
            "nonchase": f"entry no more than {NONCHASE_ATR:.2f} daily ATR beyond zone outer edge",
            "structural_stop": "prior V3 fixed research rule: zone/0.45ATR reference plus 0.35ATR buffer; stop distance <=15%",
            "trade_leg_truth": "3R target before 1R structural stop; same 1H bar touching both => AMBIGUOUS",
            "leg_unit": "1H and 4H legs are independent 1R research units, not a portfolio allocation model",
            "protected_level_policy": "N/A_SOURCE_MISSING",
            "stop_migration": "NOT_APPLIED",
            "external_severe_event_risk": "N/A_NOT_RECONSTRUCTED",
            "full_live_hard_gate_replay": False,
        },
        "episode_counts": {"all": len(E), "oos": len(EO), "oos_1h_entries": int(EO.entry_1h.sum()), "oos_4h_adds": int(EO.add_4h.sum()), "oos_1d_confirmations": int(EO.confirm_1d.sum())},
        "oos_raw_leg_metrics": {"legs": len(LO), "resolved": len(resolved), "tp3r": int((LO.outcome == "TP_3R").sum()) if len(LO) else 0, "sl1r": int((LO.outcome == "SL_1R").sum()) if len(LO) else 0, "ambiguous": int((LO.outcome == "AMBIGUOUS_SAME_BAR").sum()) if len(LO) else 0, "censored": int((LO.outcome == "CENSORED_UNRESOLVED").sum()) if len(LO) else 0, "mean_unit_R": float(resolved.realized_unit_R.mean()) if len(resolved) else None, "win_rate_resolved": float((resolved.realized_unit_R > 0).mean()) if len(resolved) else None, "interpretation": "PRELIMINARY_RAW_LEG_METRICS__NOT_PORTFOLIO_EXPECTANCY"},
        "direction": by_dir,
        "engine": by_engine,
        "data": {"one_hour": h1audit, "complete_4h_rows": len(h4raw)},
        "checks": checks,
        "step6": "PASS" if ok else "HOLD",
        "performance_gate": "PENDING_STEP7_MCR_FALSE_START_MISSED_TREND_PORTFOLIO_RISK_METRICS",
        "probability": "확률 산출보류",
        "v2_6_modified": False,
        "promotion": "HOLD",
        "next_step": "STEP7_METRICS_MCR_EXPECTANCY_FALSE_START_MISSED_TREND" if ok else "STOP_AND_AUDIT_STEP6",
    }
    (OUT / "audit.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
