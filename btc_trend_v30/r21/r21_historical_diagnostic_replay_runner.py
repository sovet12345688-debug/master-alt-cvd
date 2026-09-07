from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
BTC_ROOT = HERE.parent
sys.path.insert(0, str(BTC_ROOT))

from r20 import r20_historical_diagnostic_replay as base
from r20.r20_engine import R20Engine
from r21.r21_engine import R21Engine

R21_FROZEN = HERE / "r21_frozen_config.json"
R21_MANIFEST = HERE / "r21_freeze_manifest.json"
OUT = HERE / "output/historical_replay"
OUT.mkdir(parents=True, exist_ok=True)
ORIG_SIMULATE = base.simulate_episode

PARENT = {
    "run_id": 34109485827,
    "artifact_id": 10013917179,
    "mcr90_mean": 0.2335090946928862,
    "mcr365_mean": 0.36509239718884234,
    "long_expectancy_mean_R": 0.8346097626774698,
    "short_expectancy_mean_R": -0.014833179477069398,
    "median_seed_lag_days": 10.166666666666668,
}


class ReplayEngine(R21Engine):
    def __init__(self, cfg: dict):
        R20Engine.__init__(self, cfg)
        self.r21_cfg = json.loads(R21_FROZEN.read_text(encoding="utf-8"))


def ohlcv_tf_mixed_iso(raw: pd.DataFrame) -> pd.DataFrame:
    x = raw.copy()
    x["time"] = pd.to_datetime(x["time"], utc=True, format="mixed")
    x = x.sort_values("time").drop_duplicates("time").set_index("time")
    return x[["open", "high", "low", "close", "volume"]].astype(float)


def simulate_episode_r21(seed, serial, engine, daily, h4, weekly, h1, cfg):
    direction = str(seed.direction)
    if direction != "SHORT":
        return ORIG_SIMULATE(seed, serial, engine, daily, h4, weekly, h1, cfg)

    seed_time = pd.Timestamp(seed.timestamp)
    episode_id = f"R21-SHORT-{serial:04d}"
    stop = float(seed.stop)
    seed_i = base.daily_source_index_at(daily, seed_time, cfg)
    if seed_i < 1:
        return None, []
    dseed = daily.iloc[seed_i]
    legs = [base.open_leg("SEED", seed_time, float(seed.entry), stop, float(cfg["short"]["seed"]["risk_R"]))]
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
            base.close_all(legs, "SHORT", davail, float(drow.close), "PRECORE_INVALIDATION" if core_time is None else "FULL_EXIT", tx_rows, episode_id)
            exit_time, exit_reason = davail, "PRECORE_INVALIDATION" if core_time is None else "FULL_EXIT"
            break

        if core_time is not None and derisk_time is None and derisk:
            base.close_fraction(legs, "SHORT", davail, float(drow.close), float(cfg["holding"]["derisk_fraction"]), "DERISK_50", tx_rows, episode_id)
            derisk_time = davail
            max_state = "DERISK"

        days_after_seed = day_i - seed_i
        if confirmed_time is None and days_after_seed <= confirm_window:
            ok = engine.short_confirmed(daily, seed_i, day_i, float(dseed.low), float(seed.reference))
            if ok and float(drow.close) < stop:
                legs.append(base.open_leg("CONFIRM", davail, float(drow.close), stop, float(cfg["short"]["confirm"]["risk_add_R"])))
                confirmed_time = davail
                confirmed_day_i = day_i
                max_state = "CONFIRMED"

        if confirmed_time is not None and core_time is None:
            wrow = base.latest_weekly_row(weekly, davail)
            if wrow is not None:
                completed_sessions = max(0, int(day_i - confirmed_day_i))
                ok_core = engine.short_core_allowed_at(
                    drow, wrow, confirmed_time, davail, completed_sessions
                )
                if ok_core and float(drow.close) < stop:
                    legs.append(base.open_leg("CORE", davail, float(drow.close), stop, float(cfg["short"]["core"]["risk_add_R"])))
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
        "legs": legs,
    }
    return pos, tx_rows


def postprocess() -> None:
    audit_path = OUT / "audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    manifest = json.loads(R21_MANIFEST.read_text(encoding="utf-8"))
    pos_path = OUT / "r20_positions.csv"
    pos = pd.read_csv(pos_path)
    if not pos.empty:
        pos["confirmed_time"] = pd.to_datetime(pos["confirmed_time"], utc=True, errors="coerce", format="mixed")
        pos["core_time"] = pd.to_datetime(pos["core_time"], utc=True, errors="coerce", format="mixed")
        short = pos[pos["direction"] == "SHORT"].copy()
        simultaneous_short_core = int(((short["confirmed_time"].notna()) & (short["core_time"].notna()) & (short["confirmed_time"] == short["core_time"])).sum())
        delayed_short_core = int(((short["confirmed_time"].notna()) & (short["core_time"].notna()) & (short["core_time"] > short["confirmed_time"])).sum())
    else:
        simultaneous_short_core = delayed_short_core = 0

    audit["model"] = "MASTER_BTC_TREND_V3_R2_1"
    audit["freeze_revision"] = "SHORT_CORE_SEPARATE_DAILY_CONFIRMATION"
    audit["freeze_identity"] = {
        "frozen_config_sha256": manifest["frozen_config_sha256"],
        "frozen_config_git_blob_sha1": manifest["frozen_config_git_blob_sha1"],
        "engine_git_blob_sha1": manifest["r21_engine_git_blob_sha1"],
        "contract_git_blob_sha1": manifest["r21_contract_git_blob_sha1"],
        "single_change_id": manifest["single_change_id"],
        "parent_r20_frozen_config_blob_sha1": manifest["parent_r20_frozen_config_blob_sha1"],
        "parent_r20_engine_blob_sha1": manifest["parent_r20_engine_blob_sha1"],
    }
    counts = audit.get("counts", {})
    if "executed_r20_episodes" in counts:
        counts["executed_r21_episodes"] = counts.pop("executed_r20_episodes")
    if "resolved_r20_episodes" in counts:
        counts["resolved_r21_episodes"] = counts.pop("resolved_r20_episodes")
    audit["counts"] = counts
    s = audit["summary"]
    audit["single_change_evaluation"] = {
        "short_confirm_core_same_timestamp": simultaneous_short_core,
        "short_core_strictly_after_confirm": delayed_short_core,
        "parent_r20_reference": PARENT,
        "deltas": {
            "mcr90_mean": s["mcr90_mean"] - PARENT["mcr90_mean"],
            "mcr365_mean": s["mcr365_mean"] - PARENT["mcr365_mean"],
            "long_expectancy_mean_R": s["long_expectancy"]["mean_R"] - PARENT["long_expectancy_mean_R"],
            "short_expectancy_mean_R": s["short_expectancy"]["mean_R"] - PARENT["short_expectancy_mean_R"],
            "median_seed_lag_days": s["median_seed_lag_days_90d_truth"] - PARENT["median_seed_lag_days"],
        },
    }
    audit["methodology_notes"] = [
        "R2.1 is a single-change overlay on frozen R2.0 REV2.",
        "LONG replay path is the unchanged R2.0 simulate_episode implementation.",
        "SHORT SEED, CONFIRM, CORE market conditions, risk, stop, holding and exit rules are unchanged.",
        "Only SHORT CORE timing changed: same-timestamp CONFIRM+CORE is forbidden and at least one later completed Daily session is required.",
        "Historical 2021-2026 results are diagnostic-only and cannot promote R2.1 or alter the frozen rule.",
    ]
    audit_path.write_text(json.dumps(audit, indent=2, default=str), encoding="utf-8")

    for old, new in [
        ("r20_positions.csv", "r21_positions.csv"),
        ("r20_leg_transactions.csv", "r21_leg_transactions.csv"),
        ("r20_seed_candidates.csv", "r21_seed_candidates.csv"),
    ]:
        p = OUT / old
        if p.exists():
            p.replace(OUT / new)

    print(json.dumps(audit, indent=2, default=str))


def main() -> None:
    # Frozen R2.0 feature/seed engine stays unchanged; only the R2.1 short-core timing overlay is injected.
    base.ohlcv_tf = ohlcv_tf_mixed_iso
    base.R20Engine = ReplayEngine
    base.simulate_episode = simulate_episode_r21
    base.OUT = OUT
    base.OUT.mkdir(parents=True, exist_ok=True)
    base.main()
    postprocess()


if __name__ == "__main__":
    main()
