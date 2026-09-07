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
from r21 import r21_historical_diagnostic_replay_runner as r21replay
from r22.r22_engine import R22Engine

R21_FROZEN = BTC_ROOT / "r21/r21_frozen_config.json"
R21_AUDIT = BTC_ROOT / "r21/output/historical_replay/audit.json"
R22_FROZEN = HERE / "r22_frozen_config.json"
R22_MANIFEST = HERE / "r22_freeze_manifest.json"
OUT = HERE / "output/historical_replay"
OUT.mkdir(parents=True, exist_ok=True)


class ReplayEngine(R22Engine):
    def __init__(self, cfg: dict):
        R20Engine.__init__(self, cfg)
        self.r21_cfg = json.loads(R21_FROZEN.read_text(encoding="utf-8"))
        self.r22_cfg = json.loads(R22_FROZEN.read_text(encoding="utf-8"))
        if self.r22_cfg.get("status") != "FINAL_FROZEN_NO_REPLAY":
            raise RuntimeError("R22_NOT_FROZEN")


def ohlcv_tf_mixed_iso(raw: pd.DataFrame) -> pd.DataFrame:
    x = raw.copy()
    x["time"] = pd.to_datetime(x["time"], utc=True, format="mixed")
    x = x.sort_values("time").drop_duplicates("time").set_index("time")
    return x[["open", "high", "low", "close", "volume"]].astype(float)


def postprocess() -> None:
    audit_path = OUT / "audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    parent = json.loads(R21_AUDIT.read_text(encoding="utf-8"))
    manifest = json.loads(R22_MANIFEST.read_text(encoding="utf-8"))

    seed_path = OUT / "r20_seed_candidates.csv"
    seeds = pd.read_csv(seed_path) if seed_path.exists() else pd.DataFrame()
    priority = {}
    if not seeds.empty and "priority_extreme_near" in seeds.columns:
        z = seeds.copy()
        z["priority_extreme_near"] = z["priority_extreme_near"].astype(str).str.lower().eq("true")
        for direction in ["LONG", "SHORT"]:
            q = z[z["direction"] == direction]
            priority[direction] = {
                "raw_seed_candidates": int(len(q)),
                "priority_extreme_near_true": int(q["priority_extreme_near"].sum()),
                "priority_extreme_near_false": int((~q["priority_extreme_near"]).sum()),
            }

    audit["model"] = "MASTER_BTC_TREND_V3_R2_2"
    audit["freeze_revision"] = "EARLY_WATCH_PRIORITY_ONLY"
    audit["freeze_identity"] = {
        "frozen_config_sha256": manifest["frozen_config"]["sha256"],
        "frozen_config_git_blob_sha1": manifest["frozen_config"]["git_blob_sha1"],
        "engine_git_blob_sha1": manifest["engine"]["git_blob_sha1"],
        "contract_git_blob_sha1": manifest["contract"]["git_blob_sha1"],
        "single_change_id": manifest["single_change_id"],
        "parent_r21_frozen_config_sha256": manifest["parent_r21"]["frozen_config_sha256"],
        "parent_r21_engine_git_blob_sha1": manifest["parent_r21"]["engine_git_blob_sha1"],
        "parent_r21_contract_git_blob_sha1": manifest["parent_r21"]["contract_git_blob_sha1"],
        "pit_tests": manifest["pit_tests"],
        "r22_contract_tests": manifest["r22_contract_tests"],
    }

    counts = audit.get("counts", {})
    if "executed_r20_episodes" in counts:
        counts["executed_r22_episodes"] = counts.pop("executed_r20_episodes")
    if "resolved_r20_episodes" in counts:
        counts["resolved_r22_episodes"] = counts.pop("resolved_r20_episodes")
    audit["counts"] = counts

    s = audit["summary"]
    ps = parent["summary"]
    audit["single_change_evaluation"] = {
        "parent_r21_run_id": 34116514316,
        "parent_r21_artifact_id": 10016474113,
        "parent_r21_reference": {
            "mcr90_mean": ps["mcr90_mean"],
            "mcr365_mean": ps["mcr365_mean"],
            "trend_recall90": ps["trend_recall90"],
            "trend_recall365": ps["trend_recall365"],
            "median_seed_lag_days": ps["median_seed_lag_days_90d_truth"],
            "confirmed_false_start_rate": ps["confirmed_false_start_rate"],
            "long_expectancy_mean_R": ps["long_expectancy"]["mean_R"],
            "short_expectancy_mean_R": ps["short_expectancy"]["mean_R"],
            "capture_to_loss_ratio": ps["capture_to_loss_ratio"],
            "episode_order_mdd_R": ps["episode_order_mdd_R"],
        },
        "deltas": {
            "mcr90_mean": s["mcr90_mean"] - ps["mcr90_mean"],
            "mcr365_mean": s["mcr365_mean"] - ps["mcr365_mean"],
            "trend_recall90": s["trend_recall90"] - ps["trend_recall90"],
            "trend_recall365": s["trend_recall365"] - ps["trend_recall365"],
            "median_seed_lag_days": s["median_seed_lag_days_90d_truth"] - ps["median_seed_lag_days_90d_truth"],
            "confirmed_false_start_rate": s["confirmed_false_start_rate"] - ps["confirmed_false_start_rate"],
            "long_expectancy_mean_R": s["long_expectancy"]["mean_R"] - ps["long_expectancy"]["mean_R"],
            "short_expectancy_mean_R": s["short_expectancy"]["mean_R"] - ps["short_expectancy"]["mean_R"],
            "capture_to_loss_ratio": s["capture_to_loss_ratio"] - ps["capture_to_loss_ratio"],
            "episode_order_mdd_R": s["episode_order_mdd_R"] - ps["episode_order_mdd_R"],
        },
        "priority_metadata": priority,
    }

    audit["methodology_notes"] = [
        "R2.2 is a single-change overlay on frozen R2.1.",
        "Only the Daily prior-20D-extreme distance within 1 ATR moved from WATCH hard gate to priority-only metadata.",
        "The 1.0 ATR value itself was not tuned.",
        "LONG/SHORT EMA direction rules, SHORT Weekly/Daily regime, route triggers, volume, close-location, stop, risk, confirm, core, hold, exit and reset remain inherited.",
        "R2.1 SHORT CORE separate-Daily-confirmation timing remains inherited unchanged.",
        "Historical 2021-2026 results are diagnostic-only and cannot promote or modify frozen R2.2.",
    ]

    audit_path.write_text(json.dumps(audit, indent=2, default=str), encoding="utf-8")

    for old, new in [
        ("r20_positions.csv", "r22_positions.csv"),
        ("r20_leg_transactions.csv", "r22_leg_transactions.csv"),
        ("r20_seed_candidates.csv", "r22_seed_candidates.csv"),
    ]:
        p = OUT / old
        if p.exists():
            p.replace(OUT / new)

    print(json.dumps(audit, indent=2, default=str))


def main() -> None:
    if not R21_AUDIT.exists():
        raise SystemExit("R21_PARENT_AUDIT_MISSING")
    base.ohlcv_tf = ohlcv_tf_mixed_iso
    base.R20Engine = ReplayEngine
    base.simulate_episode = r21replay.simulate_episode_r21
    base.OUT = OUT
    base.OUT.mkdir(parents=True, exist_ok=True)
    base.main()
    postprocess()


if __name__ == "__main__":
    main()
