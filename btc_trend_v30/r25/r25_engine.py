from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from btc_trend_v30.r24.r24_engine import R24Engine

HERE = Path(__file__).resolve().parent
CFG_PATH = HERE / "r25_frozen_config.json"


@dataclass(frozen=True)
class R25RiskPlan:
    direction: str
    classification: str
    seed_risk_R: float
    confirm_risk_add_R: float
    core_risk_add_R: float
    max_episode_risk_R: float

    @property
    def total_planned_R(self) -> float:
        return self.seed_risk_R + self.confirm_risk_add_R + self.core_risk_add_R


class R25Engine(R24Engine):
    """Full Risk Governor overlay on Frozen R2.4.

    Market eligibility and state transitions are inherited unchanged. R2.5 only
    moves incremental capital authority from CONFIRMED to CORE while preserving
    the original parent maximum episode caps. R2.4 MATURE_BEAR seed-only cap
    remains dominant.
    """

    def __init__(self, cfg_path: Path | None = None):
        super().__init__()
        self.r25_cfg = json.loads((cfg_path or CFG_PATH).read_text(encoding="utf-8"))
        if self.r25_cfg.get("model") != "MASTER_BTC_TREND_V3_R2_5":
            raise RuntimeError("R25_MODEL_IDENTITY_MISMATCH")
        if self.r25_cfg.get("status") != "FINAL_FROZEN_NO_REPLAY":
            raise RuntimeError("R25_NOT_FROZEN")

    def risk_plan(self, direction: str, maturity_class: str = "NOT_MATURE_BEAR") -> R25RiskPlan:
        direction = str(direction)
        sched = self.r25_cfg["risk_schedule"]
        if direction == "LONG":
            x = sched["long"]
            cls = "N_A"
        elif direction == "SHORT" and maturity_class == "MATURE_BEAR":
            x = sched["short_mature_bear"]
            cls = "MATURE_BEAR"
        elif direction == "SHORT":
            x = sched["short_not_mature_bear"]
            cls = "NOT_MATURE_BEAR"
        else:
            raise RuntimeError("R25_BAD_DIRECTION")
        plan = R25RiskPlan(
            direction=direction,
            classification=cls,
            seed_risk_R=float(x["seed_risk_R"]),
            confirm_risk_add_R=float(x["confirm_risk_add_R"]),
            core_risk_add_R=float(x["core_risk_add_R"]),
            max_episode_risk_R=float(x["max_episode_risk_R"]),
        )
        self.validate_plan(plan)
        return plan

    @staticmethod
    def validate_plan(plan: R25RiskPlan) -> None:
        vals = [plan.seed_risk_R, plan.confirm_risk_add_R, plan.core_risk_add_R, plan.max_episode_risk_R]
        if any(v < -1e-12 for v in vals):
            raise RuntimeError("R25_NEGATIVE_RISK")
        if abs(plan.confirm_risk_add_R) > 1e-12:
            raise RuntimeError("R25_CONFIRM_MUST_NOT_ADD_RISK")
        if plan.total_planned_R > plan.max_episode_risk_R + 1e-12:
            raise RuntimeError("R25_PLAN_EXCEEDS_CAP")
        expected_cap = 1.0 if plan.direction == "LONG" else (0.30 if plan.classification == "MATURE_BEAR" else 0.85)
        if abs(plan.max_episode_risk_R - expected_cap) > 1e-12:
            raise RuntimeError("R25_PARENT_CAP_CHANGED")
        if abs(plan.total_planned_R - plan.max_episode_risk_R) > 1e-12:
            raise RuntimeError("R25_CORE_DOES_NOT_REACH_PARENT_CAP")

    @staticmethod
    def can_increase_risk(max_state: str, derisk_time, requested_add_R: float) -> bool:
        if requested_add_R <= 1e-15:
            return False
        if derisk_time is not None:
            return False
        return str(max_state) != "DERISK"
