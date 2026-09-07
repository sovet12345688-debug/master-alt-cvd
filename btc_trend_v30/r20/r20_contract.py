from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

HERE = Path(__file__).resolve().parent
CFG_PATH = HERE / "r20_candidate_config.json"


class ContractError(RuntimeError):
    pass


class LongState(str, Enum):
    NEUTRAL = "L0_NEUTRAL"
    WATCH = "L1_WATCH"
    SEED = "L2_SEED"
    CONFIRMED = "L3_CONFIRMED"
    CORE = "L4_CORE"
    HOLD = "L5_HOLD"
    DERISK = "L6_DERISK"


class ShortState(str, Enum):
    NEUTRAL = "S0_NEUTRAL"
    WATCH = "S1_WATCH"
    SEED = "S2_SEED"
    CONFIRMED = "S3_CONFIRMED"
    CORE = "S4_CORE"
    HOLD = "S5_HOLD"
    DERISK = "S6_DERISK"


@dataclass(frozen=True)
class ExposurePlan:
    seed_R: float
    confirm_R: float
    core_R: float
    cap_R: float

    @property
    def total_R(self) -> float:
        return self.seed_R + self.confirm_R + self.core_R


class R20Contract:
    def __init__(self, config_path: Path = CFG_PATH):
        self.cfg = json.loads(config_path.read_text(encoding="utf-8"))
        if self.cfg.get("model") != "MASTER_BTC_TREND_V3_R2_0":
            raise ContractError("MODEL_IDENTITY_MISMATCH")
        if self.cfg.get("status") != "PRE_FREEZE_CANDIDATE_NO_REPLAY":
            raise ContractError("UNEXPECTED_STATUS")
        self.long_plan = ExposurePlan(
            seed_R=float(self.cfg["long"]["seed"]["risk_R"]),
            confirm_R=float(self.cfg["long"]["confirm"]["risk_add_R"]),
            core_R=float(self.cfg["long"]["core"]["risk_add_R"]),
            cap_R=float(self.cfg["long"]["core"]["max_total_episode_risk_R"]),
        )
        self.short_plan = ExposurePlan(
            seed_R=float(self.cfg["short"]["seed"]["risk_R"]),
            confirm_R=float(self.cfg["short"]["confirm"]["risk_add_R"]),
            core_R=float(self.cfg["short"]["core"]["risk_add_R"]),
            cap_R=float(self.cfg["short"]["core"]["max_total_episode_risk_R"]),
        )
        self._validate_invariants()

    def _validate_invariants(self) -> None:
        if self.long_plan.total_R > self.long_plan.cap_R + 1e-12:
            raise ContractError("LONG_RISK_CAP_BREACH")
        if self.short_plan.total_R > self.short_plan.cap_R + 1e-12:
            raise ContractError("SHORT_RISK_CAP_BREACH")
        if self.long_plan.cap_R > 1.0 + 1e-12:
            raise ContractError("LONG_CAP_GT_1R")
        if self.short_plan.cap_R > 1.0 + 1e-12:
            raise ContractError("SHORT_CAP_GT_1R")
        if self.cfg["holding"]["fixed_profit_target"] is not None:
            raise ContractError("FIXED_FULL_TP_FORBIDDEN")
        if self.cfg["holding"]["stop_may_widen"] is not False:
            raise ContractError("STOP_WIDENING_FORBIDDEN")
        forbidden = self.cfg["forbidden"]
        required_true = [
            "r1x_4h_add_layer",
            "fixed_3R_full_exit",
            "shared_long_short_score",
            "automatic_same_episode_reentry_after_failure",
            "threshold_grid_before_freeze",
            "best_variant_adoption_from_historical_replay",
        ]
        if not all(bool(forbidden.get(k)) for k in required_true):
            raise ContractError("FORBIDDEN_POLICY_NOT_LOCKED")

    def long_next(self, state: LongState, event: str) -> LongState:
        table = {
            (LongState.NEUTRAL, "WATCH_PASS"): LongState.WATCH,
            (LongState.WATCH, "WATCH_FAIL"): LongState.NEUTRAL,
            (LongState.WATCH, "SEED_PASS"): LongState.SEED,
            (LongState.SEED, "CONFIRM_PASS"): LongState.CONFIRMED,
            (LongState.SEED, "INVALIDATED"): LongState.NEUTRAL,
            (LongState.CONFIRMED, "CORE_PASS"): LongState.CORE,
            (LongState.CONFIRMED, "INVALIDATED"): LongState.NEUTRAL,
            (LongState.CORE, "HOLD"): LongState.HOLD,
            (LongState.CORE, "DERISK"): LongState.DERISK,
            (LongState.HOLD, "DERISK"): LongState.DERISK,
            (LongState.HOLD, "FULL_EXIT"): LongState.NEUTRAL,
            (LongState.DERISK, "RECOVER"): LongState.HOLD,
            (LongState.DERISK, "FULL_EXIT"): LongState.NEUTRAL,
        }
        key = (state, event)
        if key not in table:
            raise ContractError(f"ILLEGAL_LONG_TRANSITION:{state}:{event}")
        return table[key]

    def short_next(self, state: ShortState, event: str) -> ShortState:
        table = {
            (ShortState.NEUTRAL, "WATCH_PASS"): ShortState.WATCH,
            (ShortState.WATCH, "WATCH_FAIL"): ShortState.NEUTRAL,
            (ShortState.WATCH, "SEED_PASS"): ShortState.SEED,
            (ShortState.SEED, "CONFIRM_PASS"): ShortState.CONFIRMED,
            (ShortState.SEED, "INVALIDATED"): ShortState.NEUTRAL,
            (ShortState.CONFIRMED, "CORE_PASS"): ShortState.CORE,
            (ShortState.CONFIRMED, "INVALIDATED"): ShortState.NEUTRAL,
            (ShortState.CORE, "HOLD"): ShortState.HOLD,
            (ShortState.CORE, "DERISK"): ShortState.DERISK,
            (ShortState.HOLD, "DERISK"): ShortState.DERISK,
            (ShortState.HOLD, "FULL_EXIT"): ShortState.NEUTRAL,
            (ShortState.DERISK, "RECOVER"): ShortState.HOLD,
            (ShortState.DERISK, "FULL_EXIT"): ShortState.NEUTRAL,
        }
        key = (state, event)
        if key not in table:
            raise ContractError(f"ILLEGAL_SHORT_TRANSITION:{state}:{event}")
        return table[key]

    def exposure_after_state(self, direction: str, state: str) -> float:
        if direction == "long":
            p = self.long_plan
            mapping = {
                LongState.NEUTRAL.value: 0.0,
                LongState.WATCH.value: 0.0,
                LongState.SEED.value: p.seed_R,
                LongState.CONFIRMED.value: p.seed_R + p.confirm_R,
                LongState.CORE.value: p.total_R,
                LongState.HOLD.value: p.total_R,
                LongState.DERISK.value: p.total_R * (1.0 - float(self.cfg["holding"]["derisk_fraction"])),
            }
        elif direction == "short":
            p = self.short_plan
            mapping = {
                ShortState.NEUTRAL.value: 0.0,
                ShortState.WATCH.value: 0.0,
                ShortState.SEED.value: p.seed_R,
                ShortState.CONFIRMED.value: p.seed_R + p.confirm_R,
                ShortState.CORE.value: p.total_R,
                ShortState.HOLD.value: p.total_R,
                ShortState.DERISK.value: p.total_R * (1.0 - float(self.cfg["holding"]["derisk_fraction"])),
            }
        else:
            raise ContractError("UNKNOWN_DIRECTION")
        if state not in mapping:
            raise ContractError("UNKNOWN_STATE")
        return float(mapping[state])

    def reset_allowed(self, *, completed_daily_sessions_since_exit: int, new_causal_20d_family: bool) -> bool:
        required = int(self.cfg["reset"]["minimum_completed_daily_sessions"])
        return completed_daily_sessions_since_exit >= required and bool(new_causal_20d_family)


if __name__ == "__main__":
    c = R20Contract()
    print({
        "model": c.cfg["model"],
        "status": c.cfg["status"],
        "long_total_R": c.long_plan.total_R,
        "short_total_R": c.short_plan.total_R,
        "replay_performed": False,
    })
