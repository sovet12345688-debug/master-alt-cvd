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
from r24 import r24_historical_diagnostic_replay_runner as r24run
from r25.r25_engine import R25Engine

R21_FROZEN = BTC_ROOT / "r21/r21_frozen_config.json"
R25_FROZEN = HERE / "r25_frozen_config.json"
R25_MANIFEST = HERE / "r25_freeze_manifest.json"
OUT = HERE / "output/historical_replay"
OUT.mkdir(parents=True, exist_ok=True)

R24_BASELINE = {
    "run_id": 34170567445,
    "mcr90_mean": 0.24041115304814,
    "mcr365_mean": 0.3790848934096434,
    "long_expectancy_mean_R": 0.8346097626774698,
    "short_expectancy_mean_R": 0.16100864900887032,
    "capture_to_loss_ratio": 2.553275169877916,
    "episode_order_mdd_R": -4.0,
    "median_seed_lag_days": 10.166666666666668,
    "bear_short_mean_R": -0.069468011818486
}


class ReplayEngine(R25Engine):
    def __init__(self, cfg: dict):
        R20Engine.__init__(self, cfg)
        self.r21_cfg = json.loads(R21_FROZEN.read_text(encoding="utf-8"))
        self.r24_cfg = json.loads((BTC_ROOT / "r24/r24_frozen_config.json").read_text(encoding="utf-8"))
        self.r25_cfg = json.loads(R25_FROZEN.read_text(encoding="utf-8"))


def ohlcv_tf_mixed_iso(raw: pd.DataFrame) -> pd.DataFrame:
    return r21run.ohlcv_tf_mixed_iso(raw)


def simulate_episode_r25(seed, serial, engine, daily, h4, weekly, h1, cfg):
    direction = str(seed.direction)
    seed_time = pd.Timestamp(seed.timestamp)
    episode_id = f"R25-{direction}-{serial:04d}"
    stop = float(seed.stop)
    seed_i = base.daily_source_index_at(daily, seed_time, cfg)
    if seed_i < 1:
        return None, []
    dseed = daily.iloc[seed_i]

    maturity = "NOT_MATURE_BEAR"
    if direction == "SHORT":
        dm = engine.augment_daily_maturity_features(daily)
        maturity = engine.classify_short_maturity(dm.iloc[seed_i])
    plan = engine.risk_plan(direction, maturity)

    legs = [base.open_leg("SEED", seed_time, float(seed.entry), stop, float(plan.seed_risk_R))]
    tx_rows = []
    confirmed_time = None
    confirmed_day_i = None
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
        if davail >= base.END_EXCL:
            break

        st = base.stop_hit_between(h1, cursor, davail, direction, stop)
        if st is not None:
            base.close_all(legs, direction, st, stop, "STRUCTURAL_STOP", tx_rows, episode_id)
            exit_time, exit_reason = st, "STRUCTURAL_STOP"
            break

        drow = daily.iloc[day_i]
        high_close = max(high_close, float(drow.close))
        low_close = min(low_close, float(drow.close))
        h4i = base.latest_h4_index(h4, davail)
        if h4i < 0:
            cursor = davail
            continue

        if direction == "LONG":
            derisk, full_exit = engine.long_exit_flags(daily, h4, day_i, h4i, high_close)
        else:
            derisk, full_exit = engine.short_exit_flags(daily, h4, day_i, h4i, low_close)
        if full_exit:
            reason = "PRECORE_INVALIDATION" if core_time is None else "FULL_EXIT"
            base.close_all(legs, direction, davail, float(drow.close), reason, tx_rows, episode_id)
            exit_time, exit_reason = davail, reason
            break

        if core_time is not None and derisk_time is None and derisk:
            base.close_fraction(legs, direction, davail, float(drow.close), float(cfg["holding"]["derisk_fraction"]), "DERISK_50", tx_rows, episode_id)
            derisk_time = davail
            max_state = "DERISK"

        days_after_seed = day_i - seed_i
        if confirmed_time is None and days_after_seed <= confirm_window:
            if direction == "LONG":
                ok = engine.long_confirmed(daily, seed_i, day_i, float(dseed.high), float(seed.reference))
            else:
                ok = engine.short_confirmed(daily, seed_i, day_i, float(dseed.low), float(seed.reference))
            valid_side = (direction == "LONG" and float(drow.close) > stop) or (direction == "SHORT" and float(drow.close) < stop)
            if ok and valid_side:
                # R2.5 CONFIRMED is informational: no capital added.
                confirmed_time = davail
                confirmed_day_i = day_i
                max_state = "CONFIRMED"

        if confirmed_time is not None and core_time is None:
            wrow = base.latest_weekly_row(weekly, davail)
            if wrow is not None:
                if direction == "LONG":
                    ok_core = engine.long_core_allowed(drow, wrow)
                else:
                    completed_sessions = max(0, int(day_i - confirmed_day_i))
                    ok_core = engine.short_core_allowed_at(drow, wrow, confirmed_time, davail, completed_sessions)
                valid_side = (direction == "LONG" and float(drow.close) > stop) or (direction == "SHORT" and float(drow.close) < stop)
                if ok_core and valid_side:
                    add_risk = float(plan.core_risk_add_R)
                    if engine.can_increase_risk(max_state, derisk_time, add_risk):
                        legs.append(base.open_leg("CORE", davail, float(drow.close), stop, add_risk))
                    core_time = davail
                    max_state = "CORE"

        cursor = davail

    if exit_time is None:
        st = base.stop_hit_between(h1, cursor, base.END_EXCL - pd.Timedelta(microseconds=1), direction, stop)
        if st is not None:
            base.close_all(legs, direction, st, stop, "STRUCTURAL_STOP", tx_rows, episode_id)
            exit_time, exit_reason = st, "STRUCTURAL_STOP"

    realized = float(sum(float(x["weighted_R"]) for x in tx_rows)) if tx_rows else 0.0
    remaining = float(sum(float(x["remaining_weight_R"]) for x in legs))
    mark = base.price_at(h1, base.END_EXCL - pd.Timedelta(microseconds=1))
    mark_r = realized
    if mark is not None:
        for leg in legs:
            rem = float(leg["remaining_weight_R"])
            if rem > 1e-15:
                mark_r += rem * base.leg_unit_r(direction, leg["entry"], leg["stop"], mark)
    if remaining <= 1e-12 and exit_time is None:
        exit_time = max((pd.Timestamp(c["time"]) for leg in legs for c in leg["closes"]), default=pd.NaT)
        exit_reason = "CLOSED"

    pos = {
        "episode_id": episode_id, "direction": direction, "route": str(seed.route),
        "family_key": base.family_key(seed), "reference": float(seed.reference),
        "seed_time": seed_time, "seed_entry": float(seed.entry), "stop": stop,
        "seed_day_index": int(seed_i), "confirmed_time": confirmed_time,
        "core_time": core_time, "derisk_time": derisk_time, "exit_time": exit_time,
        "exit_reason": exit_reason or "CENSORED_OPEN", "max_state": max_state,
        "realized_R": realized, "marked_R_at_end": float(mark_r),
        "remaining_weight_R": remaining, "resolved": bool(remaining <= 1e-12),
        "invalidated_before_confirm": bool(exit_reason == "STRUCTURAL_STOP" and confirmed_time is None),
        "r25_maturity_class": maturity, "r25_seed_risk_R": plan.seed_risk_R,
        "r25_confirm_risk_add_R": plan.confirm_risk_add_R, "r25_core_risk_add_R": plan.core_risk_add_R,
        "r25_max_episode_risk_R": plan.max_episode_risk_R, "legs": legs,
    }
    return pos, tx_rows


def postprocess() -> None:
    p = OUT / "audit.json"
    a = json.loads(p.read_text())
    pos = pd.read_csv(OUT / "r20_positions.csv")
    pos["marked_R_at_end"] = pd.to_numeric(pos["marked_R_at_end"], errors="raise")
    resolved = pos[pos["resolved"].astype(str).str.lower().isin(["true", "1"])].copy()
    confirmed = pd.to_datetime(resolved["confirmed_time"], utc=True, errors="coerce", format="mixed").notna()
    core = pd.to_datetime(resolved["core_time"], utc=True, errors="coerce", format="mixed").notna()
    conf_only = resolved[confirmed & ~core]
    core_reached = resolved[core]

    cfg = json.loads((BTC_ROOT / "r20/r20_frozen_config_rev2.json").read_text())
    cycle = r24run.cycle_recheck(pos, cfg)
    m = json.loads(R25_MANIFEST.read_text())
    a["model"] = "MASTER_BTC_TREND_V3_R2_5"
    a["freeze_revision"] = "CAPITAL_AUTHORIZATION_ONLY_AT_CORE"
    a["freeze_identity"] = {
        "frozen_config_sha256": m["frozen_config_sha256"],
        "frozen_config_git_blob_sha1": m["frozen_config_git_blob_sha1"],
        "engine_git_blob_sha1": m["r25_engine_git_blob_sha1"],
        "contract_git_blob_sha1": m["r25_contract_git_blob_sha1"]
    }
    a["risk_governor_evaluation"] = {
        "parent_r24_reference": R24_BASELINE,
        "confirmed_no_core_n": int(len(conf_only)),
        "confirmed_no_core_mean_R": float(conf_only["marked_R_at_end"].mean()) if len(conf_only) else None,
        "core_reached_n": int(len(core_reached)),
        "core_reached_mean_R": float(core_reached["marked_R_at_end"].mean()) if len(core_reached) else None,
        "deltas_vs_r24": {
            "mcr90_mean": float(a["summary"]["mcr90_mean"]) - R24_BASELINE["mcr90_mean"],
            "mcr365_mean": float(a["summary"]["mcr365_mean"]) - R24_BASELINE["mcr365_mean"],
            "long_expectancy_mean_R": float(a["summary"]["long_expectancy"]["mean_R"]) - R24_BASELINE["long_expectancy_mean_R"],
            "short_expectancy_mean_R": float(a["summary"]["short_expectancy"]["mean_R"]) - R24_BASELINE["short_expectancy_mean_R"],
            "capture_to_loss_ratio": float(a["summary"]["capture_to_loss_ratio"]) - R24_BASELINE["capture_to_loss_ratio"],
            "episode_order_mdd_R": float(a["summary"]["episode_order_mdd_R"]) - R24_BASELINE["episode_order_mdd_R"],
            "median_seed_lag_days": float(a["summary"]["median_seed_lag_days_90d_truth"]) - R24_BASELINE["median_seed_lag_days"]
        }
    }
    a["cycle_recheck"] = cycle
    a["production_promotion_evidence"] = False
    a["promotion_decision"] = "HOLD"
    a.setdefault("methodology_notes", []).extend([
        "R2.5 changes risk authorization only: CONFIRMED adds zero risk; CORE receives remaining parent risk budget.",
        "Signal eligibility, state transitions, stops, exits, reset, PIT and R2.4 MATURE_BEAR classification are unchanged.",
        "Historical 2021-2026 diagnostic cannot modify Frozen R2.5 or promote production."
    ])
    p.write_text(json.dumps(a, indent=2, default=str))
    for old, new in [("r20_positions.csv", "r25_positions.csv"), ("r20_leg_transactions.csv", "r25_leg_transactions.csv"), ("r20_seed_candidates.csv", "r25_seed_candidates.csv")]:
        q = OUT / old
        if q.exists(): q.replace(OUT / new)
    print(json.dumps(a, indent=2, default=str))


def main() -> None:
    base.ohlcv_tf = ohlcv_tf_mixed_iso
    base.R20Engine = ReplayEngine
    base.simulate_episode = simulate_episode_r25
    base.OUT = OUT
    base.OUT.mkdir(parents=True, exist_ok=True)
    base.main()
    postprocess()


if __name__ == "__main__":
    main()
