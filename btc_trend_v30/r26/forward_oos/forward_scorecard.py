from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from btc_trend_v30 import validation_protocol_v1_step2_r12_daily_scoring as s2
from btc_trend_v30 import validation_protocol_v1_step3_episode_dedupe as s3
from btc_trend_v30 import validation_protocol_v1_step4_outcome_labels as s4
from btc_trend_v30.r20 import r20_historical_diagnostic_replay as base
from btc_trend_v30.r26.forward_oos import forward_tracker as ft

HERE = Path(__file__).resolve().parent
R26 = HERE.parent
ROOT = R26.parent
SPEC_PATH = HERE / "R26_FORWARD_PROMOTION_SPEC_V1.json"
STATE_PATH = HERE / "state.json"

EXPECTED_PROMOTION_SPEC_BLOB = "a01ed85bda4512326d42e1d3eccc0b9ac8ee3dce"
STRICT_FORWARD_START = pd.Timestamp("2026-09-08T04:00:00Z")
# Binance BTCUSDT starts in 2017. A long warm-up avoids regime/zone-family startup distortion.
TRUTH_WARMUP_START = pd.Timestamp("2017-08-17T00:00:00Z")
H1_TRUTH_START = STRICT_FORWARD_START - pd.Timedelta(days=2)

OUTPUTS = {
    "json": HERE / "scorecard.json",
    "md": HERE / "scorecard.md",
    "runs": HERE / "scorecard_runs.jsonl",
    "truth_episodes": HERE / "forward_truth_episodes.csv",
    "truth_mcr": HERE / "forward_truth_mcr.csv",
    "state": HERE / "state_monotonicity.csv",
    "cycle": HERE / "cycle_buckets.csv",
}


def clean(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        v = float(v)
    if isinstance(v, float):
        return None if not np.isfinite(v) else v
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    if v is pd.NaT:
        return None
    return v


def read_csv_safe(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size <= 1:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def bools(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s.fillna(False)
    return s.astype(str).str.lower().isin(["true", "1", "yes"])


def present(s: pd.Series) -> pd.Series:
    return s.notna() & (s.astype(str).str.len() > 0) & (~s.astype(str).str.lower().isin(["nat", "nan", "none"]))


def safe_mean(s: pd.Series) -> float | None:
    x = pd.to_numeric(s, errors="coerce").dropna()
    return float(x.mean()) if len(x) else None


def ratio(gp: float, gn: float) -> float | None:
    return float(gp / gn) if gn > 0 else None


def gate(value: float | None, op: str, threshold: float, n: int | None = None, min_n: int | None = None) -> str:
    if min_n is not None and (n is None or n < min_n):
        return "N/A_NOT_MATURE"
    if value is None or not np.isfinite(float(value)):
        return "N/A"
    x = float(value)
    if op == ">":
        return "PASS" if x > threshold else "FAIL"
    if op == ">=":
        return "PASS" if x >= threshold else "FAIL"
    if op == "<":
        return "PASS" if x < threshold else "FAIL"
    if op == "<=":
        return "PASS" if x <= threshold else "FAIL"
    raise ValueError(op)


def fetch_raw_klines(interval: str, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, list[str]]:
    cur = ft.ts_ms(start)
    end_ms = ft.ts_ms(end)
    step = ft.INTERVAL_MS[interval]
    rows: list = []
    sources: list[str] = []
    while cur < end_ms:
        page, source = ft.fetch_page(interval, cur, end_ms)
        sources.append(source)
        if not page:
            break
        rows.extend(page)
        nxt = int(page[-1][0]) + step
        if nxt <= cur:
            raise RuntimeError("SCORECARD_KLINE_PAGINATION_STALLED")
        cur = nxt
        if len(page) < 1000:
            break
    cols = [
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
    ]
    if not rows:
        raise RuntimeError(f"SCORECARD_NO_KLINES:{interval}")
    x = pd.DataFrame(rows, columns=cols)
    x = x.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    x = x[pd.to_numeric(x["close_time"]) < end_ms].reset_index(drop=True)
    for c in ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_quote"]:
        x[c] = pd.to_numeric(x[c], errors="raise")
    x["open_time"] = pd.to_numeric(x["open_time"], errors="raise").astype("int64")
    x["close_time"] = pd.to_numeric(x["close_time"], errors="raise").astype("int64")
    return x, sorted(set(sources))


def strict_phase_checks(df: pd.DataFrame, ts_col: str) -> dict:
    if df.empty:
        return {"rows": 0, "unknown_phase": 0, "strict_before_start": 0, "bridge_at_or_after_start": 0, "pass": True}
    q = df.copy()
    if "phase" not in q.columns or ts_col not in q.columns:
        return {"rows": int(len(q)), "missing_required_columns": True, "pass": False}
    t = pd.to_datetime(q[ts_col], utc=True, errors="coerce", format="mixed")
    phase = q["phase"].astype(str)
    unknown = ~phase.isin(["STRICT_FORWARD", "BRIDGE_HELDOUT_PRE_FREEZE"])
    strict_bad = phase.eq("STRICT_FORWARD") & (t < STRICT_FORWARD_START)
    bridge_bad = phase.eq("BRIDGE_HELDOUT_PRE_FREEZE") & (t >= STRICT_FORWARD_START)
    return {
        "rows": int(len(q)),
        "unknown_phase": int(unknown.sum()),
        "strict_before_start": int(strict_bad.sum()),
        "bridge_at_or_after_start": int(bridge_bad.sum()),
        "pass": bool((~unknown & ~strict_bad & ~bridge_bad & t.notna()).all()),
    }


def parse_positions(df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    if df.empty:
        return df.copy(), []
    q = df.copy()
    q["seed_time"] = pd.to_datetime(q["seed_time"], utc=True, errors="coerce", format="mixed")
    for c in ["confirmed_time", "core_time", "derisk_time", "exit_time"]:
        if c in q.columns:
            q[c] = pd.to_datetime(q[c], utc=True, errors="coerce", format="mixed")
    q["resolved"] = bools(q["resolved"])
    for c in ["realized_R", "marked_R_at_end"]:
        q[c] = pd.to_numeric(q[c], errors="coerce")
    strict = q[q["phase"].astype(str).eq("STRICT_FORWARD")].copy()
    records: list[dict] = []
    for _, r in strict.iterrows():
        d = r.to_dict()
        raw_legs = d.get("legs")
        try:
            legs = json.loads(raw_legs) if isinstance(raw_legs, str) and raw_legs else []
        except Exception:
            legs = []
        for leg in legs:
            if leg.get("open_time") is not None:
                leg["open_time"] = pd.Timestamp(leg["open_time"])
            for c in leg.get("closes", []):
                if c.get("time") is not None:
                    c["time"] = pd.Timestamp(c["time"])
        d["legs"] = legs
        d["seed_time"] = pd.Timestamp(d["seed_time"])
        d["exit_time"] = None if pd.isna(d.get("exit_time")) else pd.Timestamp(d["exit_time"])
        d["direction"] = str(d["direction"]).upper()
        records.append(d)
    return strict, records


def expectancy(strict_pos: pd.DataFrame, direction: str) -> dict:
    if strict_pos.empty:
        return {"episodes": 0, "resolved": 0, "mean_R": None, "wins": 0, "losses": 0}
    g = strict_pos[strict_pos["direction"].astype(str).eq(direction)].copy()
    r = pd.to_numeric(g.loc[g["resolved"], "realized_R"], errors="coerce").dropna()
    return {
        "episodes": int(len(g)),
        "resolved": int(len(r)),
        "mean_R": float(r.mean()) if len(r) else None,
        "wins": int((r > 0).sum()),
        "losses": int((r < 0).sum()),
    }


def performance_metrics(strict_pos: pd.DataFrame) -> dict:
    long = expectancy(strict_pos, "LONG")
    short = expectancy(strict_pos, "SHORT")
    if strict_pos.empty:
        return {
            "long": long, "short": short, "resolved_total": 0,
            "capture_to_loss_ratio": None, "episode_order_mdd_R": None,
        }
    resolved = strict_pos[strict_pos["resolved"]].sort_values("seed_time").copy()
    rr = pd.to_numeric(resolved["realized_R"], errors="coerce").dropna()
    gp = float(rr[rr > 0].sum()) if len(rr) else 0.0
    gn = float(-rr[rr < 0].sum()) if len(rr) else 0.0
    mdd = None
    if len(rr):
        eq = rr.cumsum()
        dd = eq - eq.cummax()
        mdd = float(dd.min())
    return {
        "long": long,
        "short": short,
        "resolved_total": int(len(rr)),
        "gross_positive_R": gp,
        "gross_negative_R": gn,
        "capture_to_loss_ratio": ratio(gp, gn),
        "episode_order_mdd_R": mdd,
    }


def state_monotonicity(strict_pos: pd.DataFrame, min_n: int) -> tuple[dict, pd.DataFrame]:
    states = ["SEED_REACHED", "CONFIRMED_REACHED", "CORE_REACHED"]
    segments = ["ALL", "LONG", "SHORT"]
    rows = []
    comps = []
    if strict_pos.empty:
        out = {"minimum_n": min_n, "all_required_eligible": False, "all_required_pass": None, "every_eligible_direction_pass": None, "result": "N/A_NOT_MATURE", "structural_invariants": {"all_pass": True}}
        return out, pd.DataFrame(columns=["row_type", "segment", "state", "from_state", "to_state", "n", "mean_terminal_R", "eligible", "delta_mean_terminal_R", "pass"])

    q = strict_pos.copy()
    resolved = q["resolved"]
    conf = present(q["confirmed_time"])
    core = present(q["core_time"])
    derisk = present(q["derisk_time"])
    seed_ts = pd.to_datetime(q["seed_time"], utc=True, errors="coerce")
    conf_ts = pd.to_datetime(q["confirmed_time"], utc=True, errors="coerce")
    core_ts = pd.to_datetime(q["core_time"], utc=True, errors="coerce")
    derisk_ts = pd.to_datetime(q["derisk_time"], utc=True, errors="coerce")
    inv = {
        "no_core_without_confirm": bool((~core | conf).all()),
        "no_derisk_without_core": bool((~derisk | core).all()),
        "confirm_time_not_before_seed": bool((~conf | (conf_ts >= seed_ts)).all()),
        "core_time_not_before_confirm": bool((~core | (conf & (core_ts >= conf_ts))).all()),
        "derisk_time_not_before_core": bool((~derisk | (core & (derisk_ts >= core_ts))).all()),
    }
    inv["all_pass"] = all(inv.values())

    lookup = {}
    for seg in segments:
        segmask = pd.Series(True, index=q.index) if seg == "ALL" else q["direction"].astype(str).eq(seg)
        for st in states:
            if st == "SEED_REACHED": sm = resolved
            elif st == "CONFIRMED_REACHED": sm = resolved & conf
            else: sm = resolved & core
            g = q[segmask & sm]
            n = int(len(g))
            mean_r = safe_mean(g["marked_R_at_end"]) if n else None
            row = {"row_type": "COHORT", "segment": seg, "state": st, "n": n, "mean_terminal_R": mean_r}
            rows.append(row)
            lookup[(seg, st)] = row

    all_eligible = True
    all_pass = True
    every_direction_pass = True
    for seg in segments:
        for a, b in zip(states[:-1], states[1:]):
            ra, rb = lookup[(seg, a)], lookup[(seg, b)]
            eligible = ra["n"] >= min_n and rb["n"] >= min_n
            delta = None if not eligible else float(rb["mean_terminal_R"] - ra["mean_terminal_R"])
            passed = None if not eligible else bool(delta >= 0.0)
            if seg == "ALL":
                all_eligible = all_eligible and eligible
                all_pass = all_pass and bool(passed) if eligible else False
            elif eligible:
                every_direction_pass = every_direction_pass and bool(passed)
            comps.append({"row_type": "COMPARISON", "segment": seg, "from_state": a, "to_state": b, "eligible": eligible, "delta_mean_terminal_R": delta, "pass": passed})
    if not inv["all_pass"]:
        result = "FAIL"
    elif not all_eligible:
        result = "N/A_NOT_MATURE"
    else:
        result = "PASS" if all_pass and every_direction_pass else "FAIL"
    out = {
        "minimum_n": min_n,
        "all_required_eligible": bool(all_eligible),
        "all_required_pass": bool(all_pass) if all_eligible else None,
        "every_eligible_direction_pass": bool(every_direction_pass),
        "result": result,
        "structural_invariants": inv,
    }
    table = pd.DataFrame(rows + comps)
    return out, table


def daily_regime(raw_daily: pd.DataFrame) -> pd.DataFrame:
    d = raw_daily.copy()
    d.index = pd.to_datetime(d["open_time"], unit="ms", utc=True)
    d = d[["open", "high", "low", "close", "volume"]].astype(float).sort_index()
    d["ema200"] = d["close"].ewm(span=200, adjust=False, min_periods=200).mean()
    d["ema200_slope20"] = d["ema200"] - d["ema200"].shift(20)
    return d


def regime_at(d: pd.DataFrame, seed_time: pd.Timestamp, cfg: dict) -> str:
    i = base.daily_source_index_at(d, pd.Timestamp(seed_time), cfg)
    if i < 0:
        return "TRANSITION"
    r = d.iloc[i]
    vals = [r.get("close"), r.get("ema200"), r.get("ema200_slope20")]
    if not all(pd.notna(v) and np.isfinite(float(v)) for v in vals):
        return "TRANSITION"
    if float(r.close) > float(r.ema200) and float(r.ema200_slope20) > 0:
        return "BULL"
    if float(r.close) < float(r.ema200) and float(r.ema200_slope20) < 0:
        return "BEAR"
    return "TRANSITION"


def cycle_metrics(strict_pos: pd.DataFrame, raw_daily: pd.DataFrame, cfg: dict, min_n: int, floor_r: float) -> tuple[dict, pd.DataFrame]:
    cols = ["direction", "regime", "n", "mean_R", "eligible", "pass_floor"]
    if strict_pos.empty:
        return {"minimum_n": min_n, "floor_R": floor_r, "failed_eligible_regime_buckets": [], "result": "N/A_NOT_MATURE"}, pd.DataFrame(columns=cols)
    d = daily_regime(raw_daily)
    q = strict_pos.copy()
    q["regime"] = [regime_at(d, t, cfg) for t in q["seed_time"]]
    rows = []
    failed = []
    for (direction, regime), g in q.groupby(["direction", "regime"], dropna=False):
        n = int(len(g))
        mean_r = safe_mean(g["marked_R_at_end"])
        eligible = n >= min_n
        passed = None if not eligible or mean_r is None else bool(mean_r >= floor_r)
        row = {"direction": str(direction), "regime": str(regime), "n": n, "mean_R": mean_r, "eligible": eligible, "pass_floor": passed}
        rows.append(row)
        if eligible and passed is False:
            failed.append(row)
    result = "FAIL" if failed else ("PASS" if any(r["eligible"] for r in rows) else "N/A_NOT_MATURE")
    return {"minimum_n": min_n, "floor_R": floor_r, "failed_eligible_regime_buckets": failed, "result": result}, pd.DataFrame(rows, columns=cols)


def mature_bear_metrics(strict_pos: pd.DataFrame, min_n: int, floor_r: float) -> dict:
    if strict_pos.empty or "r25_maturity_class" not in strict_pos.columns:
        return {"n": 0, "mean_R": None, "minimum_n": min_n, "floor_R": floor_r, "result": "N/A_NOT_MATURE"}
    q = strict_pos[(strict_pos["direction"].astype(str) == "SHORT") & (strict_pos["r25_maturity_class"].astype(str) == "MATURE_BEAR")]
    n = int(len(q))
    mean_r = safe_mean(q["marked_R_at_end"])
    return {"n": n, "mean_R": mean_r, "minimum_n": min_n, "floor_R": floor_r, "result": gate(mean_r, ">=", floor_r, n, min_n)}


def early_quality(detections: pd.DataFrame, seeds: pd.DataFrame, strict_pos: pd.DataFrame, min_lead_n: int, min_median_h: float) -> dict:
    if detections.empty:
        return {
            "strict_chains": 0, "early_chains": 0, "priority_chains": 0,
            "raw_seed_chains": 0, "executed_chains": 0,
            "early_to_seed_conversion": None, "early_to_executed_conversion": None,
            "priority_to_seed_conversion": None, "noise_chain_rate": None,
            "explicit_early_to_executed_n": 0, "median_explicit_early_lead_hours": None,
            "lead_result": "N/A_NOT_MATURE",
        }
    d = detections.copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True, errors="coerce", format="mixed")
    d = d[d["phase"].astype(str).eq("STRICT_FORWARD")].copy()
    if d.empty:
        return early_quality(pd.DataFrame(), seeds, strict_pos, min_lead_n, min_median_h)
    if seeds.empty:
        s = pd.DataFrame(columns=["direction", "timestamp", "phase"])
    else:
        s = seeds.copy()
        s["timestamp"] = pd.to_datetime(s["timestamp"], utc=True, errors="coerce", format="mixed")
        s = s[s["phase"].astype(str).eq("STRICT_FORWARD")].copy()
    pos_pairs = set()
    if not strict_pos.empty:
        pos_pairs = {(str(r.direction), pd.Timestamp(r.seed_time)) for _, r in strict_pos.iterrows()}

    chains = []
    leads = []
    for (direction, streak), g in d.groupby(["direction", "streak_id"], sort=False):
        g = g.sort_values("timestamp")
        first_early = g.loc[g["state"].astype(str).eq("EARLY_DETECT"), "timestamp"]
        first_priority = g.loc[g["state"].astype(str).eq("PRIORITY_WATCH"), "timestamp"]
        sg = s[s["direction"].astype(str).eq(str(direction))]
        seed_times = set(pd.to_datetime(sg["timestamp"], utc=True, errors="coerce")) if len(sg) else set()
        chain_times = set(pd.to_datetime(g["timestamp"], utc=True, errors="coerce"))
        chain_seed_times = sorted(seed_times.intersection(chain_times))
        executed_times = sorted([t for t in chain_seed_times if (str(direction), pd.Timestamp(t)) in pos_pairs])
        had_seed = bool(chain_seed_times)
        had_exec = bool(executed_times)
        if len(first_early) and had_exec:
            lead = (pd.Timestamp(executed_times[0]) - pd.Timestamp(first_early.min())).total_seconds() / 3600.0
            if lead >= 0:
                leads.append(float(lead))
        chains.append({
            "direction": str(direction), "streak_id": int(streak),
            "has_early": bool(len(first_early)), "has_priority": bool(len(first_priority)),
            "had_raw_seed": had_seed, "had_executed_entry": had_exec,
        })
    c = pd.DataFrame(chains)
    n = int(len(c))
    early = c[c.has_early]
    pri = c[c.has_priority]
    med = float(np.median(leads)) if leads else None
    return {
        "strict_chains": n,
        "early_chains": int(len(early)),
        "priority_chains": int(len(pri)),
        "raw_seed_chains": int(c.had_raw_seed.sum()) if n else 0,
        "executed_chains": int(c.had_executed_entry.sum()) if n else 0,
        "early_to_seed_conversion": float(early.had_raw_seed.mean()) if len(early) else None,
        "early_to_executed_conversion": float(early.had_executed_entry.mean()) if len(early) else None,
        "priority_to_seed_conversion": float(pri.had_raw_seed.mean()) if len(pri) else None,
        "noise_chain_rate": float((~c.had_raw_seed).mean()) if n else None,
        "explicit_early_to_executed_n": int(len(leads)),
        "median_explicit_early_lead_hours": med,
        "lead_result": gate(med, ">=", min_median_h, len(leads), min_lead_n),
    }


def forward_truth_and_mcr(asof: pd.Timestamp, strict_positions_records: list[dict]) -> tuple[dict, pd.DataFrame, pd.DataFrame, pd.DataFrame | None]:
    try:
        raw_daily, sources = fetch_raw_klines("1d", TRUTH_WARMUP_START, asof)
        scored = s2.build(raw_daily)
        sig, _, _ = s3.build_families(scored)
        eps = s3.episodes(sig)
        if eps.empty:
            truth_df = pd.DataFrame()
        else:
            truth = pd.DataFrame([s4.episode_truth(r, raw_daily) for _, r in eps.iterrows()])
            truth_df = pd.concat([eps.reset_index(drop=True), truth], axis=1)
            truth_df["start_date"] = pd.to_datetime(truth_df["start_date"], utc=True, errors="coerce")
            truth_df["known_start"] = truth_df["start_date"] + pd.Timedelta(days=1)
            truth_df["phase"] = np.where(truth_df["known_start"] >= STRICT_FORWARD_START, "STRICT_FORWARD", "WARMUP_PRE_STRICT")
            truth_df = truth_df[truth_df["phase"] == "STRICT_FORWARD"].copy()

        h1 = None
        mrows = []
        need_h1 = False
        if not truth_df.empty:
            need_h1 = bool((truth_df.get("available_90d", False).astype(bool) | truth_df.get("available_365d", False).astype(bool)).any())
        if need_h1:
            h1, _ = ft.fetch_ohlcv("1h", H1_TRUTH_START, asof)
            base.END_EXCL = pd.Timestamp(asof)
        for _, r in truth_df.iterrows():
            direction = str(r.direction).upper()
            known = pd.Timestamp(r.known_start)
            med_pos = bool(r.available_90d and str(r.medium_truth_status) in ("MEDIUM_SUCCESS_20", "MEDIUM_SUCCESS_30"))
            long_pos = bool(r.available_365d and str(r.long_truth_status) in ("LONG_SUCCESS_PRIMARY", "LONG_SUCCESS_EXTENSION"))
            cap90 = part90 = lag90 = None
            cap365 = part365 = lag365 = None
            mcr90 = mcr365 = None
            if h1 is not None and bool(r.available_90d):
                cap90, part90, lag90 = base.captured_return_for_window(strict_positions_records, direction, known, known + pd.Timedelta(days=90), float(r.start_close), h1)
                mfe90 = float(r.MFE_90d) if pd.notna(r.MFE_90d) else None
                if med_pos and mfe90 is not None and np.isfinite(mfe90) and mfe90 > 0:
                    mcr90 = min(1.0, float(cap90) / mfe90)
            if h1 is not None and bool(r.available_365d):
                cap365, part365, lag365 = base.captured_return_for_window(strict_positions_records, direction, known, known + pd.Timedelta(days=365), float(r.start_close), h1)
                mfe365 = float(r.MFE_365d) if pd.notna(r.MFE_365d) else None
                if long_pos and mfe365 is not None and np.isfinite(mfe365) and mfe365 > 0:
                    mcr365 = min(1.0, float(cap365) / mfe365)
            mrows.append({
                "independent_episode_id": str(r.independent_episode_id),
                "direction": direction,
                "start_date": r.start_date,
                "known_start": known,
                "medium_truth_status": str(r.medium_truth_status),
                "long_truth_status": str(r.long_truth_status),
                "medium_truth_positive": med_pos,
                "long_truth_positive": long_pos,
                "MFE_90d": float(r.MFE_90d) if pd.notna(r.get("MFE_90d")) else None,
                "MFE_365d": float(r.MFE_365d) if pd.notna(r.get("MFE_365d")) else None,
                "capture90_return": cap90,
                "capture365_return": cap365,
                "participated_90d": part90,
                "participated_365d": part365,
                "lag90_days": lag90,
                "lag365_days": lag365,
                "mcr90": mcr90,
                "mcr365": mcr365,
            })
        mdf = pd.DataFrame(mrows)
        med = mdf[mdf.get("medium_truth_positive", pd.Series(dtype=bool)).astype(bool)] if len(mdf) else pd.DataFrame()
        lng = mdf[mdf.get("long_truth_positive", pd.Series(dtype=bool)).astype(bool)] if len(mdf) else pd.DataFrame()
        m90vals = pd.to_numeric(med.get("mcr90", pd.Series(dtype=float)), errors="coerce").dropna() if len(med) else pd.Series(dtype=float)
        m365vals = pd.to_numeric(lng.get("mcr365", pd.Series(dtype=float)), errors="coerce").dropna() if len(lng) else pd.Series(dtype=float)
        summary = {
            "status": "PASS",
            "daily_sources": sources,
            "strict_truth_episodes": int(len(truth_df)),
            "mature_mcr90_truth_windows": int(len(m90vals)),
            "mature_mcr365_truth_windows": int(len(m365vals)),
            "mcr90_mean": float(m90vals.mean()) if len(m90vals) else None,
            "mcr365_mean": float(m365vals.mean()) if len(m365vals) else None,
            "formula_source": "validation_protocol_v1_step3_episode_dedupe + step4_outcome_labels + r20 captured_return_for_window",
        }
        return summary, truth_df, mdf, raw_daily
    except Exception as e:
        return {
            "status": "N/A_DATA_ERROR",
            "error": repr(e),
            "strict_truth_episodes": 0,
            "mature_mcr90_truth_windows": 0,
            "mature_mcr365_truth_windows": 0,
            "mcr90_mean": None,
            "mcr365_mean": None,
        }, pd.DataFrame(), pd.DataFrame(), None


def integrity_checks(state: dict, detections: pd.DataFrame, seeds: pd.DataFrame, positions: pd.DataFrame) -> dict:
    freeze_ok = False
    freeze_detail = None
    try:
        freeze_detail = ft.verify_freeze()
        freeze_ok = bool(all(freeze_detail.values()))
    except Exception as e:
        freeze_detail = {"error": repr(e)}
        freeze_ok = False
    spec_blob = ft.git_blob(SPEC_PATH) if SPEC_PATH.exists() else None
    spec_ok = spec_blob == EXPECTED_PROMOTION_SPEC_BLOB
    precheck = os.environ.get("R26_FORWARD_PRECHECK_PASS") == "1"
    closed = bool(state.get("data", {}).get("closed_candles_only") is True)
    det_phase = strict_phase_checks(detections, "timestamp")
    seed_phase = strict_phase_checks(seeds, "timestamp")
    pos_phase = strict_phase_checks(positions, "seed_time")
    detector_ok = True
    if not detections.empty:
        detector_ok = bool((~bools(detections["detector_can_execute"])).all())
        detector_ok = detector_ok and bool(detections["execution_authority"].astype(str).eq("FROZEN_R2_5_ONLY").all())
        detector_ok = detector_ok and bool(detections["capital_authority"].astype(str).eq("FROZEN_R2_5_ONLY").all())
    checks = {
        "frozen_identity": {"pass": freeze_ok, "detail": freeze_detail},
        "promotion_spec_identity": {"pass": spec_ok, "expected_blob": EXPECTED_PROMOTION_SPEC_BLOB, "actual_blob": spec_blob},
        "pit_and_contract_precheck": {"pass": precheck, "source": "GitHub Actions Frozen identity and PIT contract step"},
        "closed_candles_only": {"pass": closed},
        "phase_firewall_detections": det_phase,
        "phase_firewall_seeds": seed_phase,
        "phase_firewall_positions": pos_phase,
        "detector_zero_execution_authority": {"pass": detector_ok},
        "frozen_execution_authority_unchanged": {"pass": bool(freeze_ok and precheck)},
    }
    hard_fail = [k for k, v in checks.items() if not bool(v.get("pass"))]
    return {"checks": checks, "hard_failures": hard_fail, "pass": len(hard_fail) == 0}


def write_markdown(score: dict) -> None:
    g = score["gates"]
    e = score["eligibility"]
    lines = [
        "# MASTER BTC TREND V3 R2.6 — Forward Scorecard",
        "",
        f"- As-of UTC: `{score['asof_utc']}`",
        f"- Strict Forward days: **{e['strict_calendar_days']} / {e['minimum_strict_calendar_days']}**",
        f"- Automated assessment: **{score['automated_assessment']}**",
        f"- Production decision: **{score['production_decision']}** (automatic promotion prohibited)",
        "",
        "## Eligibility",
        "| Item | Current | Minimum | Status |",
        "|---|---:|---:|---|",
        f"| Calendar days | {e['strict_calendar_days']} | {e['minimum_strict_calendar_days']} | {e['items']['calendar']['status']} |",
        f"| Resolved total | {e['resolved_total']} | {e['minimum_resolved_total']} | {e['items']['resolved_total']['status']} |",
        f"| Resolved LONG | {e['resolved_long']} | {e['minimum_resolved_long']} | {e['items']['resolved_long']['status']} |",
        f"| Resolved SHORT | {e['resolved_short']} | {e['minimum_resolved_short']} | {e['items']['resolved_short']['status']} |",
        f"| MCR90 truth windows | {e['mcr90_windows']} | {e['minimum_mcr90_windows']} | {e['items']['mcr90_windows']['status']} |",
        f"| MCR365 truth windows | {e['mcr365_windows']} | {e['minimum_mcr365_windows']} | {e['items']['mcr365_windows']['status']} |",
        f"| State ALL cohorts | {e['state_all_min_n']} | {e['minimum_state_n']} | {e['items']['state_all']['status']} |",
        "",
        "## Primary gates",
        "| Gate | Value | Rule | Status |",
        "|---|---:|---|---|",
        f"| LONG expectancy | {g['long_expectancy']['value']} | > 0R | {g['long_expectancy']['status']} |",
        f"| SHORT expectancy | {g['short_expectancy']['value']} | > 0R | {g['short_expectancy']['status']} |",
        f"| Capture/Loss | {g['capture_to_loss']['value']} | > 1.10 | {g['capture_to_loss']['status']} |",
        f"| MCR90 | {g['mcr90']['value']} | >= 0.20 | {g['mcr90']['status']} |",
        f"| MCR365 | {g['mcr365']['value']} | >= 0.20 | {g['mcr365']['status']} |",
        f"| MDD | {g['mdd']['value']} | > -6R | {g['mdd']['status']} |",
        f"| State monotonicity | - | non-decreasing | {g['state_monotonicity']['status']} |",
        f"| Cycle safety | - | eligible bucket >= -0.15R | {g['cycle_safety']['status']} |",
        f"| MATURE_BEAR SHORT | {g['mature_bear_short']['value']} | eligible mean >= -0.15R | {g['mature_bear_short']['status']} |",
        "",
        "## Early detection (diagnostic only; zero execution authority)",
        f"- Explicit EARLY→executed samples: {score['early_detection']['explicit_early_to_executed_n']}",
        f"- Median explicit EARLY lead: {score['early_detection']['median_explicit_early_lead_hours']} h",
        f"- Lead gate: {score['early_detection']['lead_result']}",
        f"- EARLY→Seed conversion: {score['early_detection']['early_to_seed_conversion']}",
        f"- Noise-chain rate: {score['early_detection']['noise_chain_rate']}",
        "",
        "## Integrity",
        f"- Hard gate: **{'PASS' if score['integrity']['pass'] else 'FAIL'}**",
        f"- Failures: {score['integrity']['hard_failures']}",
        "",
        "> This scorecard cannot promote Production. A separately executed formal review is required by R26_FORWARD_PROMOTION_SPEC_V1.",
    ]
    OUTPUTS["md"].write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    asof = pd.Timestamp(state["asof_utc"])
    if asof.tzinfo is None:
        asof = asof.tz_localize("UTC")
    else:
        asof = asof.tz_convert("UTC")

    detections = read_csv_safe(HERE / "detections.csv")
    seeds = read_csv_safe(HERE / "seeds.csv")
    positions = read_csv_safe(HERE / "positions.csv")
    strict_pos, position_records = parse_positions(positions)

    perf = performance_metrics(strict_pos)
    truth, truth_df, mcr_df, raw_daily = forward_truth_and_mcr(asof, position_records)

    state_min_n = int(spec["state_quality_gate"]["minimum_n_per_cohort"])
    state_result, state_table = state_monotonicity(strict_pos, state_min_n)
    cfg = json.loads((ROOT / "r20/r20_frozen_config_rev2.json").read_text(encoding="utf-8"))
    cycle_spec = spec["cycle_safety_gate"]
    if raw_daily is not None:
        cycle_result, cycle_table = cycle_metrics(strict_pos, raw_daily, cfg, int(cycle_spec["minimum_n_per_bucket"]), float(cycle_spec["mean_R_floor"]))
    else:
        cycle_result = {"minimum_n": int(cycle_spec["minimum_n_per_bucket"]), "floor_R": float(cycle_spec["mean_R_floor"]), "failed_eligible_regime_buckets": [], "result": "N/A_DATA_ERROR"}
        cycle_table = pd.DataFrame()

    mb = spec["mature_bear_short_safety"]
    mature_bear = mature_bear_metrics(strict_pos, int(mb["minimum_n"]), float(mb["mean_R_floor"]))
    ed = spec["early_detection_quality"]
    early = early_quality(detections, seeds, strict_pos, int(ed["minimum_explicit_early_to_executed_samples_for_lead_review"]), 4.0)
    integrity = integrity_checks(state, detections, seeds, positions)

    elig_spec = spec["review_eligibility"]
    strict_days = max(0, int((asof - STRICT_FORWARD_START).total_seconds() // 86400))
    resolved_total = int(perf["resolved_total"])
    resolved_long = int(perf["long"]["resolved"])
    resolved_short = int(perf["short"]["resolved"])
    m90n = int(truth["mature_mcr90_truth_windows"])
    m365n = int(truth["mature_mcr365_truth_windows"])
    if state_table.empty:
        state_all_min_n = 0
    else:
        c = state_table[(state_table.get("row_type") == "COHORT") & (state_table.get("segment") == "ALL")]
        state_all_min_n = int(c["n"].min()) if len(c) else 0

    eligibility_items = {
        "calendar": {"value": strict_days, "minimum": int(elig_spec["minimum_strict_calendar_days"]), "status": "PASS" if strict_days >= int(elig_spec["minimum_strict_calendar_days"]) else "WAIT"},
        "resolved_total": {"value": resolved_total, "minimum": int(elig_spec["minimum_resolved_episodes_total"]), "status": "PASS" if resolved_total >= int(elig_spec["minimum_resolved_episodes_total"]) else "WAIT"},
        "resolved_long": {"value": resolved_long, "minimum": int(elig_spec["minimum_resolved_long_episodes"]), "status": "PASS" if resolved_long >= int(elig_spec["minimum_resolved_long_episodes"]) else "WAIT"},
        "resolved_short": {"value": resolved_short, "minimum": int(elig_spec["minimum_resolved_short_episodes"]), "status": "PASS" if resolved_short >= int(elig_spec["minimum_resolved_short_episodes"]) else "WAIT"},
        "mcr90_windows": {"value": m90n, "minimum": int(elig_spec["minimum_mature_mcr90_truth_windows"]), "status": "PASS" if m90n >= int(elig_spec["minimum_mature_mcr90_truth_windows"]) else "WAIT"},
        "mcr365_windows": {"value": m365n, "minimum": int(elig_spec["minimum_mature_mcr365_truth_windows"]), "status": "PASS" if m365n >= int(elig_spec["minimum_mature_mcr365_truth_windows"]) else "WAIT"},
        "state_all": {"value": state_all_min_n, "minimum": state_min_n, "status": "PASS" if state_all_min_n >= state_min_n else "WAIT"},
    }
    eligibility_complete = all(v["status"] == "PASS" for v in eligibility_items.values())

    gates = {
        "long_expectancy": {"value": perf["long"]["mean_R"], "n": resolved_long, "status": gate(perf["long"]["mean_R"], ">", 0.0, resolved_long, int(elig_spec["minimum_resolved_long_episodes"]))},
        "short_expectancy": {"value": perf["short"]["mean_R"], "n": resolved_short, "status": gate(perf["short"]["mean_R"], ">", 0.0, resolved_short, int(elig_spec["minimum_resolved_short_episodes"]))},
        "capture_to_loss": {"value": perf["capture_to_loss_ratio"], "n": resolved_total, "status": gate(perf["capture_to_loss_ratio"], ">", 1.10, resolved_total, int(elig_spec["minimum_resolved_episodes_total"]))},
        "mcr90": {"value": truth["mcr90_mean"], "n": m90n, "status": gate(truth["mcr90_mean"], ">=", 0.20, m90n, int(elig_spec["minimum_mature_mcr90_truth_windows"]))},
        "mcr365": {"value": truth["mcr365_mean"], "n": m365n, "status": gate(truth["mcr365_mean"], ">=", 0.20, m365n, int(elig_spec["minimum_mature_mcr365_truth_windows"]))},
        "mdd": {"value": perf["episode_order_mdd_R"], "n": resolved_total, "status": gate(perf["episode_order_mdd_R"], ">", -6.0, resolved_total, 1)},
        "state_monotonicity": {"value": None, "status": state_result["result"]},
        "cycle_safety": {"value": None, "status": cycle_result["result"]},
        "mature_bear_short": {"value": mature_bear["mean_R"], "n": mature_bear["n"], "status": mature_bear["result"]},
    }

    mature_failures = [k for k, v in gates.items() if v["status"] == "FAIL"]
    if not integrity["pass"]:
        assessment = "INTEGRITY_FAIL"
    elif strict_days < int(elig_spec["minimum_strict_calendar_days"]):
        assessment = "HOLD_COLLECTING"
    elif not eligibility_complete:
        assessment = "EXTEND_CANDIDATE"
    elif mature_failures:
        assessment = "FAIL_CANDIDATE_FOR_FORMAL_REVIEW"
    else:
        assessment = "PASS_READY_FOR_FORMAL_REVIEW"

    score = {
        "model": "MASTER_BTC_TREND_V3_R2_6",
        "scorecard": "R26_FORWARD_SCORECARD_V1",
        "promotion_spec": "R26_FORWARD_PROMOTION_SPEC_V1",
        "asof_utc": asof.isoformat(),
        "strict_forward_start": STRICT_FORWARD_START.isoformat(),
        "automated_assessment": assessment,
        "production_decision": "HOLD",
        "automatic_production_promotion": False,
        "formal_review_required": True,
        "eligibility": {
            "complete": eligibility_complete,
            "strict_calendar_days": strict_days,
            "minimum_strict_calendar_days": int(elig_spec["minimum_strict_calendar_days"]),
            "resolved_total": resolved_total,
            "minimum_resolved_total": int(elig_spec["minimum_resolved_episodes_total"]),
            "resolved_long": resolved_long,
            "minimum_resolved_long": int(elig_spec["minimum_resolved_long_episodes"]),
            "resolved_short": resolved_short,
            "minimum_resolved_short": int(elig_spec["minimum_resolved_short_episodes"]),
            "mcr90_windows": m90n,
            "minimum_mcr90_windows": int(elig_spec["minimum_mature_mcr90_truth_windows"]),
            "mcr365_windows": m365n,
            "minimum_mcr365_windows": int(elig_spec["minimum_mature_mcr365_truth_windows"]),
            "state_all_min_n": state_all_min_n,
            "minimum_state_n": state_min_n,
            "items": eligibility_items,
        },
        "performance": perf,
        "truth_mcr": truth,
        "state_quality": state_result,
        "cycle_safety": cycle_result,
        "mature_bear_short": mature_bear,
        "early_detection": early,
        "gates": gates,
        "mature_gate_failures": mature_failures,
        "integrity": integrity,
        "governance": {
            "strict_forward_only": True,
            "bridge_counts_for_promotion": False,
            "historical_counts_for_promotion": False,
            "no_threshold_tuning_from_forward": True,
            "scorecard_cannot_auto_promote": True,
            "formal_review_required_for_pass_extend_fail": True,
        },
    }
    score = clean(score)

    OUTPUTS["json"].write_text(json.dumps(score, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    write_markdown(score)
    truth_df.to_csv(OUTPUTS["truth_episodes"], index=False)
    mcr_df.to_csv(OUTPUTS["truth_mcr"], index=False)
    state_table.to_csv(OUTPUTS["state"], index=False)
    cycle_table.to_csv(OUTPUTS["cycle"], index=False)

    run_line = clean({
        "asof_utc": score["asof_utc"],
        "assessment": score["automated_assessment"],
        "production_decision": "HOLD",
        "strict_days": strict_days,
        "resolved_total": resolved_total,
        "resolved_long": resolved_long,
        "resolved_short": resolved_short,
        "mcr90_n": m90n,
        "mcr365_n": m365n,
        "long_expectancy_R": perf["long"]["mean_R"],
        "short_expectancy_R": perf["short"]["mean_R"],
        "capture_to_loss": perf["capture_to_loss_ratio"],
        "mdd_R": perf["episode_order_mdd_R"],
        "integrity_pass": integrity["pass"],
    })
    with OUTPUTS["runs"].open("a", encoding="utf-8") as f:
        f.write(json.dumps(run_line, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n")

    print(json.dumps(score, indent=2, ensure_ascii=False, allow_nan=False))
    print("R26_FORWARD_SCORECARD_PASS")


if __name__ == "__main__":
    main()
