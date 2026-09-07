from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
BTC_ROOT = HERE.parent
sys.path.insert(0, str(BTC_ROOT))

from r20 import r20_historical_diagnostic_replay as base
from r20.r20_engine import R20Engine
from r21 import r21_historical_diagnostic_replay_runner as r21run
from r24.r24_engine import R24Engine

R21_FROZEN = BTC_ROOT / "r21/r21_frozen_config.json"
R24_FROZEN = HERE / "r24_frozen_config.json"
R24_MANIFEST = HERE / "r24_freeze_manifest.json"
OUT = HERE / "output/historical_replay"
OUT.mkdir(parents=True, exist_ok=True)

R21_BASELINE = {
    "run_id": 34116514316,
    "artifact_id": 10016474113,
    "mcr90_mean": 0.2363081812721231,
    "mcr365_mean": 0.3656408642162458,
    "long_expectancy_mean_R": 0.8346097626774698,
    "short_expectancy_mean_R": 0.03469379995835538,
    "capture_to_loss_ratio": 2.300161761460656,
    "episode_order_mdd_R": -4.670518860647492,
    "median_seed_lag_days": 10.166666666666668,
}


class ReplayEngine(R24Engine):
    def __init__(self, cfg: dict):
        R20Engine.__init__(self, cfg)
        self.r21_cfg = json.loads(R21_FROZEN.read_text(encoding="utf-8"))
        self.r24_cfg = json.loads(R24_FROZEN.read_text(encoding="utf-8"))


def ohlcv_tf_mixed_iso(raw: pd.DataFrame) -> pd.DataFrame:
    return r21run.ohlcv_tf_mixed_iso(raw)


def simulate_episode_r24(seed, serial, engine, daily, h4, weekly, h1, cfg):
    direction = str(seed.direction)
    if direction != "SHORT":
        return r21run.ORIG_SIMULATE(seed, serial, engine, daily, h4, weekly, h1, cfg)

    seed_time = pd.Timestamp(seed.timestamp)
    episode_id = f"R24-SHORT-{serial:04d}"
    stop = float(seed.stop)
    seed_i = base.daily_source_index_at(daily, seed_time, cfg)
    if seed_i < 1:
        return None, []

    daily_maturity = engine.augment_daily_maturity_features(daily)
    dseed = daily_maturity.iloc[seed_i]
    risk_plan = engine.short_risk_plan_at_seed(dseed)
    engine.validate_latched_plan(risk_plan)

    legs = [base.open_leg("SEED", seed_time, float(seed.entry), stop, float(risk_plan.seed_risk_R))]
    tx_rows = []
    confirmed_time = None
    confirmed_day_i = None
    core_time = None
    derisk_time = None
    exit_time = None
    exit_reason = None
    max_state = "SEED"
    cursor = seed_time
    low_close = float(dseed.close)
    confirm_window = int(cfg["short"]["confirm"]["window_days"])

    for day_i in range(seed_i + 1, len(daily)):
        davail = pd.Timestamp(daily.index[day_i]) + pd.Timedelta(hours=int(cfg["data"]["timeframe_available_offset_hours"]["1D"]))
        if davail >= base.END_EXCL:
            break

        st = base.stop_hit_between(h1, cursor, davail, "SHORT", stop)
        if st is not None:
            base.close_all(legs, "SHORT", st, stop, "STRUCTURAL_STOP", tx_rows, episode_id)
            exit_time, exit_reason = st, "STRUCTURAL_STOP"
            break

        drow = daily.iloc[day_i]
        low_close = min(low_close, float(drow.close))
        h4i = base.latest_h4_index(h4, davail)
        if h4i < 0:
            cursor = davail
            continue

        derisk, full_exit = engine.short_exit_flags(daily, h4, day_i, h4i, low_close)
        if full_exit:
            reason = "PRECORE_INVALIDATION" if core_time is None else "FULL_EXIT"
            base.close_all(legs, "SHORT", davail, float(drow.close), reason, tx_rows, episode_id)
            exit_time, exit_reason = davail, reason
            break

        if core_time is not None and derisk_time is None and derisk:
            base.close_fraction(legs, "SHORT", davail, float(drow.close), float(cfg["holding"]["derisk_fraction"]), "DERISK_50", tx_rows, episode_id)
            derisk_time = davail
            max_state = "DERISK"

        days_after_seed = day_i - seed_i
        if confirmed_time is None and days_after_seed <= confirm_window:
            ok = engine.short_confirmed(daily, seed_i, day_i, float(dseed.low), float(seed.reference))
            if ok and float(drow.close) < stop:
                add_risk = float(risk_plan.confirm_risk_add_R)
                if add_risk > 1e-15:
                    legs.append(base.open_leg("CONFIRM", davail, float(drow.close), stop, add_risk))
                confirmed_time = davail
                confirmed_day_i = day_i
                max_state = "CONFIRMED"

        if confirmed_time is not None and core_time is None:
            wrow = base.latest_weekly_row(weekly, davail)
            if wrow is not None:
                completed_sessions = max(0, int(day_i - confirmed_day_i))
                ok_core = engine.short_core_allowed_at(drow, wrow, confirmed_time, davail, completed_sessions)
                if ok_core and float(drow.close) < stop:
                    add_risk = float(risk_plan.core_risk_add_R)
                    if add_risk > 1e-15:
                        legs.append(base.open_leg("CORE", davail, float(drow.close), stop, add_risk))
                    core_time = davail
                    max_state = "CORE"

        cursor = davail

    if exit_time is None:
        st = base.stop_hit_between(h1, cursor, base.END_EXCL - pd.Timedelta(microseconds=1), "SHORT", stop)
        if st is not None:
            base.close_all(legs, "SHORT", st, stop, "STRUCTURAL_STOP", tx_rows, episode_id)
            exit_time, exit_reason = st, "STRUCTURAL_STOP"

    realized = float(sum(float(x["weighted_R"]) for x in tx_rows)) if tx_rows else 0.0
    remaining = float(sum(float(x["remaining_weight_R"]) for x in legs))
    mark = base.price_at(h1, base.END_EXCL - pd.Timedelta(microseconds=1))
    mark_r = realized
    if mark is not None:
        for leg in legs:
            rem = float(leg["remaining_weight_R"])
            if rem > 1e-15:
                mark_r += rem * base.leg_unit_r("SHORT", leg["entry"], leg["stop"], mark)
    if remaining <= 1e-12 and exit_time is None:
        exit_time = max((pd.Timestamp(c["time"]) for leg in legs for c in leg["closes"]), default=pd.NaT)
        exit_reason = "CLOSED"

    pos = {
        "episode_id": episode_id,
        "direction": "SHORT",
        "route": str(seed.route),
        "family_key": base.family_key(seed),
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
        "r24_maturity_class": risk_plan.classification,
        "r24_seed_risk_R": risk_plan.seed_risk_R,
        "r24_confirm_risk_add_R": risk_plan.confirm_risk_add_R,
        "r24_core_risk_add_R": risk_plan.core_risk_add_R,
        "r24_max_episode_risk_R": risk_plan.max_episode_risk_R,
        "legs": legs,
    }
    return pos, tx_rows


def daily_with_regime() -> pd.DataFrame:
    raw = pd.read_csv(base.DATA1D)
    d = base.ohlcv_daily(raw)
    d = d.copy()
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


def cycle_recheck(pos: pd.DataFrame, cfg: dict) -> dict:
    d = daily_with_regime()
    q = pos.copy()
    q["seed_time"] = pd.to_datetime(q["seed_time"], utc=True, format="mixed")
    q["marked_R_at_end"] = pd.to_numeric(q["marked_R_at_end"], errors="raise")
    q["regime"] = [regime_at(d, t, cfg) for t in q["seed_time"]]
    q["year"] = q["seed_time"].dt.year.astype(int)
    rows = []
    floor = -0.15
    min_n = 10
    primary_pass = True
    for bucket_type, col in [("REGIME", "regime"), ("YEAR", "year")]:
        for (direction, bucket), g in q.groupby(["direction", col], dropna=False):
            n = int(len(g))
            mean_r = float(g["marked_R_at_end"].mean())
            eligible = n >= min_n
            passed = (mean_r >= floor) if eligible else None
            if bucket_type == "REGIME" and eligible and not passed:
                primary_pass = False
            rows.append({"bucket_type": bucket_type, "direction": str(direction), "bucket": str(bucket), "n": n, "mean_R": mean_r, "eligible": eligible, "pass_floor": passed})
    table = pd.DataFrame(rows)
    table.to_csv(OUT / "r24_cycle_bucket_summary.csv", index=False)
    bear_short = table[(table.bucket_type == "REGIME") & (table.direction == "SHORT") & (table.bucket == "BEAR")]
    bs = bear_short.iloc[0].to_dict() if len(bear_short) else None
    failed = table[(table.bucket_type == "REGIME") & (table.eligible) & (table.pass_floor == False)].to_dict("records")
    return {"primary_gate": "PASS" if primary_pass else "FAIL", "minimum_n": min_n, "floor_R": floor, "bear_short_bucket": bs, "failed_eligible_regime_buckets": failed}


def postprocess() -> None:
    audit_path = OUT / "audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    manifest = json.loads(R24_MANIFEST.read_text(encoding="utf-8"))
    cfg = json.loads((BTC_ROOT / "r20/r20_frozen_config_rev2.json").read_text(encoding="utf-8"))
    pos_path = OUT / "r20_positions.csv"
    pos = pd.read_csv(pos_path)
    short = pos[pos["direction"] == "SHORT"].copy()
    mature = short[short.get("r24_maturity_class", pd.Series(index=short.index, dtype=str)) == "MATURE_BEAR"].copy()
    non_mature = short[short.get("r24_maturity_class", pd.Series(index=short.index, dtype=str)) != "MATURE_BEAR"].copy()

    audit["model"] = "MASTER_BTC_TREND_V3_R2_4"
    audit["freeze_revision"] = "MATURE_BEAR_SEED_ONLY_RISK_CAP"
    audit["freeze_identity"] = {
        "frozen_config_sha256": manifest["frozen_config_sha256"],
        "frozen_config_git_blob_sha1": manifest["frozen_config_git_blob_sha1"],
        "engine_git_blob_sha1": manifest["r24_engine_git_blob_sha1"],
        "contract_git_blob_sha1": manifest["r24_contract_git_blob_sha1"],
        "single_change_id": manifest["single_change_id"],
        "parent_r21_engine_git_blob_sha1": manifest["parent_r21_engine_git_blob_sha1"],
    }
    counts = audit.get("counts", {})
    if "executed_r20_episodes" in counts:
        counts["executed_r24_episodes"] = counts.pop("executed_r20_episodes")
    if "resolved_r20_episodes" in counts:
        counts["resolved_r24_episodes"] = counts.pop("resolved_r20_episodes")
    counts["r24_mature_bear_short_episodes"] = int(len(mature))
    counts["r24_not_mature_bear_short_episodes"] = int(len(non_mature))
    audit["counts"] = counts

    s = audit["summary"]
    audit["single_change_evaluation"] = {
        "parent_r21_reference": R21_BASELINE,
        "mature_bear_short_episodes": int(len(mature)),
        "mature_bear_short_mean_R": float(mature["marked_R_at_end"].mean()) if len(mature) else None,
        "not_mature_bear_short_mean_R": float(non_mature["marked_R_at_end"].mean()) if len(non_mature) else None,
        "deltas_vs_r21": {
            "mcr90_mean": float(s["mcr90_mean"]) - R21_BASELINE["mcr90_mean"],
            "mcr365_mean": float(s["mcr365_mean"]) - R21_BASELINE["mcr365_mean"],
            "long_expectancy_mean_R": float(s["long_expectancy"]["mean_R"]) - R21_BASELINE["long_expectancy_mean_R"],
            "short_expectancy_mean_R": float(s["short_expectancy"]["mean_R"]) - R21_BASELINE["short_expectancy_mean_R"],
            "capture_to_loss_ratio": float(s["capture_to_loss_ratio"]) - R21_BASELINE["capture_to_loss_ratio"],
            "episode_order_mdd_R": float(s["episode_order_mdd_R"]) - R21_BASELINE["episode_order_mdd_R"],
            "median_seed_lag_days": float(s["median_seed_lag_days_90d_truth"]) - R21_BASELINE["median_seed_lag_days"],
        },
    }
    audit["cycle_recheck"] = cycle_recheck(pos, cfg)
    audit["methodology_notes"] = [
        "R2.4 is a single risk-allocation overlay on frozen R2.1.",
        "LONG path, SHORT Seed eligibility, Stop, Exit and R2.1 separate-Daily CORE timing are unchanged.",
        "MATURE_BEAR classification is latched causally at SHORT Seed using EMA20<EMA50<EMA200 plus negative/nonpositive slopes; no forensic distance/duration/volume cutoffs are used.",
        "MATURE_BEAR keeps Seed 0.30R but suppresses Confirm/Core risk adds; state transitions still form and parent holding/exit logic remains active.",
        "Cycle recheck reuses the predeclared R2.1 causal EMA200 regime formula, n>=10, floor -0.15R.",
        "Historical 2021-2026 results are diagnostic only and cannot modify frozen R2.4 or promote production."
    ]
    audit_path.write_text(json.dumps(audit, indent=2, default=str), encoding="utf-8")

    for old, new in [("r20_positions.csv", "r24_positions.csv"), ("r20_leg_transactions.csv", "r24_leg_transactions.csv"), ("r20_seed_candidates.csv", "r24_seed_candidates.csv")]:
        p = OUT / old
        if p.exists():
            p.replace(OUT / new)
    print(json.dumps(audit, indent=2, default=str))


def main() -> None:
    base.ohlcv_tf = ohlcv_tf_mixed_iso
    base.R20Engine = ReplayEngine
    base.simulate_episode = simulate_episode_r24
    base.OUT = OUT
    base.OUT.mkdir(parents=True, exist_ok=True)
    base.main()
    postprocess()


if __name__ == "__main__":
    main()
