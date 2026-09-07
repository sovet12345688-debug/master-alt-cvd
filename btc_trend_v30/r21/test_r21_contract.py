from __future__ import annotations

import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from btc_trend_v30.r20.r20_engine import R20Engine
from btc_trend_v30.r21.r21_engine import R21Engine

HERE = Path(__file__).resolve().parent
cfg = json.loads((HERE / "r21_candidate_config.json").read_text(encoding="utf-8"))

assert cfg["model"] == "MASTER_BTC_TREND_V3_R2_1"
assert cfg["status"] == "PRE_FREEZE_CANDIDATE_NO_REPLAY"
assert cfg["parent"]["model"] == "MASTER_BTC_TREND_V3_R2_0"
assert cfg["parent"]["revision"] == "REV2_PIT_AVAILABILITY_HOTFIX"
assert cfg["parent"]["frozen_config_git_blob_sha1"] == "9f1a54edb0312bd86f230f025f539bd4255a016f"
assert cfg["parent"]["engine_git_blob_sha1"] == "cf5c5bf7a10ba699fe695964b7e7f435dead016b"
assert cfg["single_change"]["id"] == "SHORT_CORE_REQUIRES_SEPARATE_COMPLETED_DAILY_CONFIRMATION"
assert cfg["single_change"]["applies_to"] == "SHORT_CORE_ONLY"
assert cfg["single_change"]["minimum_completed_daily_sessions_after_confirm"] == 1
assert cfg["risk_identity"] == {
    "long_max_episode_R": 1.0,
    "short_max_episode_R": 0.85,
    "short_seed_R": 0.3,
    "short_confirm_add_R": 0.3,
    "short_core_add_R": 0.25,
}
assert cfg["evidence_firewall"]["r21_historical_replay_performed_before_freeze"] is False

eng = R21Engine()
c = pd.Timestamp("2025-01-02T00:00:00Z")
assert eng.short_core_timing_allowed(c, c, 1) is False
assert eng.short_core_timing_allowed(c, c + pd.Timedelta(hours=23), 0) is False
assert eng.short_core_timing_allowed(c, c + pd.Timedelta(days=1), 0) is False
assert eng.short_core_timing_allowed(c, c + pd.Timedelta(days=1), 1) is True
assert eng.short_core_timing_allowed(c, c + pd.Timedelta(days=2), 2) is True

# No LONG override is allowed in R2.1.
assert R21Engine.long_core_allowed is R20Engine.long_core_allowed
assert R21Engine.long_confirmed is R20Engine.long_confirmed
assert R21Engine.long_exit_flags is R20Engine.long_exit_flags

# Existing SHORT market-condition rule itself is inherited unchanged.
assert R21Engine.short_core_allowed is R20Engine.short_core_allowed
assert R21Engine.short_confirmed is R20Engine.short_confirmed
assert R21Engine.short_exit_flags is R20Engine.short_exit_flags

print("R21_SINGLE_CHANGE_CONTRACT_PASS")
