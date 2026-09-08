from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd

from btc_trend_v30.r20.r20_engine import build_feature_bundle, _finite, long_watch, short_watch
from btc_trend_v30.r25.r25_engine import R25Engine

HERE = Path(__file__).resolve().parent
CFG_PATH = HERE / "r26_frozen_config.json"


def _early_long(row: pd.Series) -> bool:
    vals = [row.get("d_close"), row.get("d_ema20"), row.get("d_ema20_slope_5"), row.get("close"), row.get("ema20"), row.get("ema20_slope_5")]
    if not _finite(*vals):
        return False
    return bool(row["d_close"] > row["d_ema20"] and row["d_ema20_slope_5"] > 0 and row["close"] > row["ema20"] and row["ema20_slope_5"] > 0)


def _early_short(row: pd.Series) -> bool:
    vals = [row.get("d_close"), row.get("d_ema20"), row.get("d_ema20_slope_5"), row.get("close"), row.get("ema20"), row.get("ema20_slope_5")]
    if not _finite(*vals):
        return False
    return bool(row["d_close"] < row["d_ema20"] and row["d_ema20_slope_5"] < 0 and row["close"] < row["ema20"] and row["ema20_slope_5"] < 0)


def _priority_extreme_near(row: pd.Series, direction: str, threshold_atr: float = 1.0) -> bool:
    if direction == "LONG":
        vals = [row.get("d_close"), row.get("d_prior20_high"), row.get("d_atr14")]
        return bool(_finite(*vals) and row["d_close"] >= row["d_prior20_high"] - threshold_atr * row["d_atr14"])
    vals = [row.get("d_close"), row.get("d_prior20_low"), row.get("d_atr14")]
    return bool(_finite(*vals) and row["d_close"] <= row["d_prior20_low"] + threshold_atr * row["d_atr14"])


class R26Engine(R25Engine):
    """Final integration: frozen R2.3 awareness semantics + frozen R2.5 execution.

    R2.6 intentionally does not override seed generation, confirmation, core,
    stops, exits, reset, or risk_plan. detect_states is read-only awareness.
    """

    def __init__(self, cfg_path: Path | None = None):
        super().__init__()
        self.r26_cfg = json.loads((cfg_path or CFG_PATH).read_text(encoding="utf-8"))
        if self.r26_cfg.get("model") != "MASTER_BTC_TREND_V3_R2_6":
            raise RuntimeError("R26_MODEL_IDENTITY_MISMATCH")
        if self.r26_cfg.get("status") != "FINAL_FROZEN_NO_REPLAY":
            raise RuntimeError("R26_NOT_FROZEN")
        sc = self.r26_cfg["single_change"]
        if any(bool(sc[k]) for k in ["execution_rules_changed", "signal_rules_changed", "risk_rules_changed", "stop_rules_changed", "state_transition_rules_changed", "exit_rules_changed", "reset_rules_changed", "pit_rules_changed"]):
            raise RuntimeError("R26_EXECUTION_CHANGE_FORBIDDEN")
        if sc["detector_can_execute"] or sc["detector_can_modify_risk"] or sc["detector_can_modify_stop"] or sc["detector_can_modify_seed"]:
            raise RuntimeError("R26_DETECTOR_AUTHORITY_BROKEN")

    def detect_states(self, daily: pd.DataFrame, h4: pd.DataFrame, h1: Optional[pd.DataFrame] = None) -> pd.DataFrame:
        bundle = build_feature_bundle(daily, h4, h1, self.cfg)
        h = bundle["4H"]
        official = super().scan_seed_candidates(daily, h4, h1)
        official_keys: set[tuple[str, pd.Timestamp]] = set()
        if not official.empty:
            for _, r in official.iterrows():
                official_keys.add((str(r["direction"]), pd.Timestamp(r["timestamp"])))
        threshold = float(self.r26_cfg["single_change"]["priority_extreme_near_threshold_atr"])
        rows: list[dict] = []
        for i in range(len(h)):
            row = h.iloc[i]
            ts = pd.Timestamp(h.index[i])
            for direction, early_fn, watch_fn in [("LONG", _early_long, long_watch), ("SHORT", _early_short, short_watch)]:
                if not early_fn(row):
                    continue
                priority = _priority_extreme_near(row, direction, threshold)
                parent_watch = bool(watch_fn(row, self.cfg))
                seed_present = (direction, ts) in official_keys
                state = "EXECUTION_READY" if seed_present else ("PRIORITY_WATCH" if parent_watch else "EARLY_DETECT")
                rows.append({
                    "timestamp": ts,
                    "direction": direction,
                    "state": state,
                    "priority_extreme_near": priority,
                    "r25_parent_watch_pass": parent_watch,
                    "r25_seed_present": seed_present,
                    "detector_can_execute": False,
                    "execution_authority": "FROZEN_R2_5_ONLY",
                    "capital_authority": "FROZEN_R2_5_ONLY",
                })
        return pd.DataFrame(rows)


if __name__ == "__main__":
    e = R26Engine()
    print(json.dumps({"model": e.r26_cfg["model"], "status": e.r26_cfg["status"], "integration": "LOAD_PASS", "execution_engine": "FROZEN_R2_5_UNCHANGED", "historical_replay_performed": False}, indent=2))
