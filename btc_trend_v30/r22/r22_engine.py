from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from btc_trend_v30.r21.r21_engine import R21Engine
from btc_trend_v30.r20.r20_engine import (
    build_feature_bundle,
    _finite,
    _long_reclaim_flag,
    _short_failed_retest_flag,
)

HERE = Path(__file__).resolve().parent
CFG_PATH = HERE / "r22_candidate_config.json"


def _priority_distance(row: pd.Series, direction: str, threshold_atr: float) -> bool:
    if direction == "LONG":
        vals = (row.get("d_close"), row.get("d_prior20_high"), row.get("d_atr14"))
        if not _finite(*vals):
            return False
        return bool(row["d_close"] >= row["d_prior20_high"] - threshold_atr * row["d_atr14"])
    vals = (row.get("d_close"), row.get("d_prior20_low"), row.get("d_atr14"))
    if not _finite(*vals):
        return False
    return bool(row["d_close"] <= row["d_prior20_low"] + threshold_atr * row["d_atr14"])


def _early_long_watch(row: pd.Series) -> bool:
    need = [row.get("d_close"), row.get("d_ema20"), row.get("d_ema20_slope_5"), row.get("close"), row.get("ema20"), row.get("ema20_slope_5")]
    if not _finite(*need):
        return False
    return bool(row["d_close"] > row["d_ema20"] and row["d_ema20_slope_5"] > 0 and row["close"] > row["ema20"] and row["ema20_slope_5"] > 0)


def _early_short_watch(row: pd.Series) -> bool:
    need = [row.get("d_close"), row.get("d_ema20"), row.get("d_ema20_slope_5"), row.get("close"), row.get("ema20"), row.get("ema20_slope_5")]
    if not _finite(*need):
        return False
    return bool(row["d_close"] < row["d_ema20"] and row["d_ema20_slope_5"] < 0 and row["close"] < row["ema20"] and row["ema20_slope_5"] < 0)


class R22Engine(R21Engine):
    """R2.2 single-change overlay on frozen R2.1.

    Only behavioral delta: prior-20D-extreme distance within 1 ATR is no longer
    a WATCH hard gate. The unchanged threshold is retained as priority metadata.
    Actual SEED route triggers, regime, stops, risk, confirm/core/hold/exit/reset
    and the R2.1 SHORT CORE timing rule remain unchanged.
    """

    def __init__(self, cfg_path: Path | None = None):
        super().__init__()
        self.r22_cfg = json.loads((cfg_path or CFG_PATH).read_text(encoding="utf-8"))
        if self.r22_cfg.get("model") != "MASTER_BTC_TREND_V3_R2_2":
            raise RuntimeError("R22_MODEL_IDENTITY_MISMATCH")
        if self.r22_cfg.get("status") not in {"PRE_FREEZE_NO_REPLAY", "FINAL_FROZEN_NO_REPLAY"}:
            raise RuntimeError("R22_BAD_STATUS")

    def scan_seed_candidates(self, daily: pd.DataFrame, h4: pd.DataFrame, h1: Optional[pd.DataFrame] = None) -> pd.DataFrame:
        bundle = build_feature_bundle(daily, h4, h1, self.cfg)
        d = bundle["1D"]
        h = bundle["4H"]
        rows = []
        threshold = float(self.r22_cfg["single_change"]["unchanged_distance_threshold_atr"])
        for i in range(len(h)):
            row = h.iloc[i]
            ts = h.index[i]

            if _early_long_watch(row):
                b = self.cfg["long"]["seed"]["breakout"]
                breakout = bool(
                    _finite(row.get("close"), row.get("prior20_high"), row.get("volume_ratio"), row.get("close_location"), row.get("d_close"), row.get("d_ema20"))
                    and row["close"] > row["prior20_high"]
                    and row["volume_ratio"] >= float(b["volume_ratio_min"])
                    and row["close_location"] >= 1.0 - float(b["close_location_upper_fraction"])
                    and row["d_close"] >= row["d_ema20"]
                )
                reclaim = _long_reclaim_flag(d, ts, self.cfg)
                if breakout or reclaim:
                    if _finite(row.get("causal_swing_low3"), row.get("atr14"), row.get("close")):
                        stop = min(float(row["causal_swing_low3"]), float(row["close"] - self.cfg["long"]["seed"]["stop"]["atr_multiple"] * row["atr14"]))
                        dist = (row["close"] - stop) / row["close"]
                        if 0 < dist <= float(self.cfg["long"]["seed"]["stop"]["max_stop_distance_pct"]):
                            route = "BREAKOUT" if breakout else "RECLAIM"
                            rows.append({"direction":"LONG","route":route,"entry":float(row["close"]),"stop":float(stop),"reference":float(row["prior20_high"] if breakout else row["d_ema20"]),"timestamp":ts,"priority_extreme_near":_priority_distance(row,"LONG",threshold)})

            if _early_short_watch(row):
                regime = bool(
                    _finite(row.get("w_close"), row.get("w_ema20"), row.get("w_ema20_slope_5"), row.get("d_close"), row.get("d_ema50"))
                    and (row["w_close"] < row["w_ema20"] or row["w_ema20_slope_5"] < 0)
                    and row["d_close"] < row["d_ema50"]
                )
                if regime:
                    b = self.cfg["short"]["seed"]["breakdown"]
                    breakdown = bool(
                        _finite(row.get("close"), row.get("prior20_low"), row.get("volume_ratio"), row.get("close_location"))
                        and row["close"] < row["prior20_low"]
                        and row["volume_ratio"] >= float(b["volume_ratio_min"])
                        and row["close_location"] <= float(b["close_location_lower_fraction"])
                    )
                    failed_retest = _short_failed_retest_flag(h, i, self.cfg)
                    if breakdown or failed_retest:
                        if _finite(row.get("causal_swing_high3"), row.get("atr14"), row.get("close")):
                            stop = max(float(row["causal_swing_high3"]), float(row["close"] + self.cfg["short"]["seed"]["stop"]["atr_multiple"] * row["atr14"]))
                            dist = (stop - row["close"]) / row["close"]
                            if 0 < dist <= float(self.cfg["short"]["seed"]["stop"]["max_stop_distance_pct"]):
                                route = "BREAKDOWN" if breakdown else "FAILED_RETEST"
                                reference = float(row["prior20_low"] if breakdown else min(row["d_ema20"], row["d_prior20_low"]))
                                rows.append({"direction":"SHORT","route":route,"entry":float(row["close"]),"stop":float(stop),"reference":reference,"timestamp":ts,"priority_extreme_near":_priority_distance(row,"SHORT",threshold)})
        return pd.DataFrame(rows)


if __name__ == "__main__":
    e = R22Engine()
    print(json.dumps({"model": e.r22_cfg["model"], "status": e.r22_cfg["status"], "engine": "LOAD_PASS", "historical_replay_performed": False}, indent=2))
