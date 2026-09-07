from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from btc_trend_v30.r21.r21_engine import R21Engine

HERE = Path(__file__).resolve().parent
CFG_PATH = HERE / "r24_candidate_config.json"


@dataclass(frozen=True)
class R24ShortRiskPlan:
    classification: str
    seed_risk_R: float
    confirm_risk_add_R: float
    core_risk_add_R: float
    max_episode_risk_R: float
    classification_latched_at_seed: bool = True

    @property
    def total_planned_R(self) -> float:
        return self.seed_risk_R + self.confirm_risk_add_R + self.core_risk_add_R


class R24Engine(R21Engine):
    """R2.4 single-change risk overlay on Frozen R2.1.

    R2.4 never changes Seed eligibility, Stop, Exit, LONG logic, or the R2.1
    SHORT CORE timing rule. It only latches a causal Daily maturity class at
    SHORT Seed and caps additional risk when the bearish structure is fully
    aligned and mature by EMA hierarchy/slope.
    """

    def __init__(self, cfg_path: Path | None = None):
        super().__init__()
        self.r24_cfg = json.loads((cfg_path or CFG_PATH).read_text(encoding="utf-8"))
        if self.r24_cfg.get("model") != "MASTER_BTC_TREND_V3_R2_4":
            raise RuntimeError("R24_MODEL_IDENTITY_MISMATCH")
        if self.r24_cfg.get("status") not in {"PRE_FREEZE_CANDIDATE_NO_REPLAY", "FINAL_FROZEN_NO_REPLAY"}:
            raise RuntimeError("R24_INVALID_STATUS")

    @staticmethod
    def augment_daily_maturity_features(daily_features: pd.DataFrame) -> pd.DataFrame:
        """Add causal risk-only EMA200 features on source Daily rows.

        Input must contain a numeric `close` column and be chronologically
        ordered on source candle-open timestamps. No forward shift is applied
        here; the caller must continue using the parent PIT availability rules.
        """
        if "close" not in daily_features.columns:
            raise RuntimeError("R24_DAILY_CLOSE_REQUIRED")
        out = daily_features.copy().sort_index()
        close = pd.to_numeric(out["close"], errors="raise").astype(float)
        out["ema200"] = close.ewm(span=200, adjust=False, min_periods=200).mean()
        out["ema200_slope_20"] = out["ema200"] - out["ema200"].shift(20)
        return out

    @staticmethod
    def _finite(*vals: object) -> bool:
        return all(pd.notna(v) and np.isfinite(float(v)) for v in vals)

    def classify_short_maturity(self, drow: pd.Series) -> str:
        required = [
            drow.get("close"),
            drow.get("ema20"),
            drow.get("ema50"),
            drow.get("ema20_slope_5"),
            drow.get("ema50_slope_10"),
            drow.get("ema200"),
            drow.get("ema200_slope_20"),
        ]
        if not self._finite(*required):
            return "NOT_MATURE_BEAR"

        mature = bool(
            float(drow["close"]) < float(drow["ema200"])
            and float(drow["ema20"]) < float(drow["ema50"])
            and float(drow["ema50"]) < float(drow["ema200"])
            and float(drow["ema20_slope_5"]) < 0.0
            and float(drow["ema50_slope_10"]) <= 0.0
            and float(drow["ema200_slope_20"]) < 0.0
        )
        return "MATURE_BEAR" if mature else "NOT_MATURE_BEAR"

    def short_risk_plan_at_seed(self, drow: pd.Series) -> R24ShortRiskPlan:
        cls = self.classify_short_maturity(drow)
        action = self.r24_cfg["risk_action"]
        if cls == "MATURE_BEAR":
            x = action["mature_bear"]
            return R24ShortRiskPlan(
                classification=cls,
                seed_risk_R=float(x["seed_risk_R"]),
                confirm_risk_add_R=float(x["confirm_risk_add_R"]),
                core_risk_add_R=float(x["core_risk_add_R"]),
                max_episode_risk_R=float(x["max_episode_risk_R"]),
            )
        x = action["not_mature_bear"]
        return R24ShortRiskPlan(
            classification=cls,
            seed_risk_R=float(x["seed_risk_R"]),
            confirm_risk_add_R=float(x["confirm_risk_add_R"]),
            core_risk_add_R=float(x["core_risk_add_R"]),
            max_episode_risk_R=float(x["max_episode_risk_R"]),
        )

    @staticmethod
    def validate_latched_plan(plan: R24ShortRiskPlan) -> None:
        if plan.seed_risk_R < 0 or plan.confirm_risk_add_R < 0 or plan.core_risk_add_R < 0:
            raise RuntimeError("R24_NEGATIVE_RISK_COMPONENT")
        if plan.total_planned_R > plan.max_episode_risk_R + 1e-12:
            raise RuntimeError("R24_RISK_PLAN_EXCEEDS_CAP")
        if plan.max_episode_risk_R > 0.85 + 1e-12:
            raise RuntimeError("R24_PARENT_SHORT_RISK_CAP_EXCEEDED")
