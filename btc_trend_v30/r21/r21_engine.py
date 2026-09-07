from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from btc_trend_v30.r20.r20_engine import R20Engine

HERE = Path(__file__).resolve().parent
CFG_PATH = HERE / "r21_frozen_config.json"


class R21Engine(R20Engine):
    """R2.1 single-change overlay on frozen R2.0 REV2.

    Only behavioral delta: SHORT CORE cannot be added on the same availability
    timestamp as SHORT CONFIRM. At least one later completed daily session is
    required, and the unchanged R2.0 short-core market conditions must still pass.
    """

    def __init__(self, cfg_path: Path | None = None):
        super().__init__()
        self.r21_cfg = json.loads((cfg_path or CFG_PATH).read_text(encoding="utf-8"))
        if self.r21_cfg.get("model") != "MASTER_BTC_TREND_V3_R2_1":
            raise RuntimeError("R21_MODEL_IDENTITY_MISMATCH")
        if self.r21_cfg.get("status") != "FINAL_FROZEN_NO_REPLAY":
            raise RuntimeError("R21_NOT_FROZEN")

    def short_core_timing_allowed(
        self,
        confirm_time: pd.Timestamp,
        candidate_time: pd.Timestamp,
        completed_daily_sessions_since_confirm: int,
    ) -> bool:
        c = pd.Timestamp(confirm_time)
        t = pd.Timestamp(candidate_time)
        if t <= c:
            return False
        minimum = int(self.r21_cfg["single_change"]["minimum_completed_daily_sessions_after_confirm"])
        return int(completed_daily_sessions_since_confirm) >= minimum

    def short_core_allowed_at(
        self,
        drow: pd.Series,
        wrow: pd.Series,
        confirm_time: pd.Timestamp,
        candidate_time: pd.Timestamp,
        completed_daily_sessions_since_confirm: int,
    ) -> bool:
        return bool(
            self.short_core_timing_allowed(
                confirm_time,
                candidate_time,
                completed_daily_sessions_since_confirm,
            )
            and super().short_core_allowed(drow, wrow)
        )
