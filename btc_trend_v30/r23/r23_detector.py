from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd

from btc_trend_v30.r21.r21_engine import R21Engine
from btc_trend_v30.r20.r20_engine import build_feature_bundle, _finite, long_watch, short_watch

HERE = Path(__file__).resolve().parent
CFG_PATH = HERE / "r23_frozen_config.json"


def _early_long(row: pd.Series) -> bool:
    vals = [row.get("d_close"), row.get("d_ema20"), row.get("d_ema20_slope_5"), row.get("close"), row.get("ema20"), row.get("ema20_slope_5")]
    if not _finite(*vals):
        return False
    return bool(
        row["d_close"] > row["d_ema20"]
        and row["d_ema20_slope_5"] > 0
        and row["close"] > row["ema20"]
        and row["ema20_slope_5"] > 0
    )


def _early_short(row: pd.Series) -> bool:
    vals = [row.get("d_close"), row.get("d_ema20"), row.get("d_ema20_slope_5"), row.get("close"), row.get("ema20"), row.get("ema20_slope_5")]
    if not _finite(*vals):
        return False
    return bool(
        row["d_close"] < row["d_ema20"]
        and row["d_ema20_slope_5"] < 0
        and row["close"] < row["ema20"]
        and row["ema20_slope_5"] < 0
    )


def _priority_extreme_near(row: pd.Series, direction: str, threshold_atr: float = 1.0) -> bool:
    if direction == "LONG":
        vals = [row.get("d_close"), row.get("d_prior20_high"), row.get("d_atr14")]
        if not _finite(*vals):
            return False
        return bool(row["d_close"] >= row["d_prior20_high"] - threshold_atr * row["d_atr14"])
    vals = [row.get("d_close"), row.get("d_prior20_low"), row.get("d_atr14")]
    if not _finite(*vals):
        return False
    return bool(row["d_close"] <= row["d_prior20_low"] + threshold_atr * row["d_atr14"])


class R23Engine(R21Engine):
    """R2.3 = frozen R2.1 execution + non-execution early detector.

    This class intentionally does NOT override scan_seed_candidates.
    The detector can label awareness states only; execution remains the exact
    parent R2.1 seed output.
    """

    def __init__(self, cfg_path: Path | None = None):
        super().__init__()
        self.r23_cfg = json.loads((cfg_path or CFG_PATH).read_text(encoding="utf-8"))
        if self.r23_cfg.get("model") != "MASTER_BTC_TREND_V3_R2_3":
            raise RuntimeError("R23_MODEL_IDENTITY_MISMATCH")
        if self.r23_cfg.get("status") != "FINAL_FROZEN_NO_REPLAY":
            raise RuntimeError("R23_NOT_FROZEN")
        sc = self.r23_cfg["single_change"]
        if sc.get("early_detection_can_execute") is not False:
            raise RuntimeError("R23_EXECUTION_FIREWALL_BROKEN")

    def detect_states(self, daily: pd.DataFrame, h4: pd.DataFrame, h1: Optional[pd.DataFrame] = None) -> pd.DataFrame:
        bundle = build_feature_bundle(daily, h4, h1, self.cfg)
        h = bundle["4H"]
        official = super().scan_seed_candidates(daily, h4, h1)
        official_keys: set[tuple[str, pd.Timestamp]] = set()
        if not official.empty:
            for _, r in official.iterrows():
                official_keys.add((str(r["direction"]), pd.Timestamp(r["timestamp"])))

        threshold = float(self.r23_cfg["single_change"]["priority_extreme_near"]["threshold_atr"])
        rows: list[dict] = []
        for i in range(len(h)):
            row = h.iloc[i]
            ts = pd.Timestamp(h.index[i])
            for direction, early_fn, watch_fn in [
                ("LONG", _early_long, long_watch),
                ("SHORT", _early_short, short_watch),
            ]:
                if not early_fn(row):
                    continue
                priority = _priority_extreme_near(row, direction, threshold)
                parent_watch = bool(watch_fn(row, self.cfg))
                r21_seed_present = (direction, ts) in official_keys
                if r21_seed_present:
                    state = "EXECUTION_READY"
                elif parent_watch:
                    state = "PRIORITY_WATCH"
                else:
                    state = "EARLY_DETECT"
                rows.append({
                    "timestamp": ts,
                    "direction": direction,
                    "state": state,
                    "priority_extreme_near": priority,
                    "r21_parent_watch_pass": parent_watch,
                    "r21_seed_present": r21_seed_present,
                    "detector_can_execute": False,
                    "execution_authority": "FROZEN_R2_1_SEED_ONLY",
                })
        return pd.DataFrame(rows)


if __name__ == "__main__":
    e = R23Engine()
    print(json.dumps({
        "model": e.r23_cfg["model"],
        "status": e.r23_cfg["status"],
        "detector": "LOAD_PASS",
        "execution_engine": "FROZEN_R2_1_UNCHANGED",
        "historical_replay_performed": False,
    }, indent=2))
