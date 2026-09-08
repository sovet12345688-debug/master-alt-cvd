from __future__ import annotations

import inspect

from btc_trend_v30.r20.r20_engine import R20Engine
from btc_trend_v30.r25.r25_engine import R25Engine
from btc_trend_v30.r26.r26_engine import R26Engine, _priority_extreme_near


def test_execution_seed_identity():
    assert R26Engine.scan_seed_candidates is R25Engine.scan_seed_candidates
    assert R25Engine.scan_seed_candidates is R20Engine.scan_seed_candidates


def test_risk_plan_identity():
    assert R26Engine.risk_plan is R25Engine.risk_plan
    assert R26Engine.can_increase_risk is R25Engine.can_increase_risk


def test_no_execution_overrides():
    forbidden = ["long_confirmed", "short_confirmed", "long_core_allowed", "short_core_allowed_at", "long_exit_flags", "short_exit_flags", "scan_seed_candidates", "risk_plan"]
    own = R26Engine.__dict__
    assert all(name not in own for name in forbidden)


def test_detector_only_addition():
    assert "detect_states" in R26Engine.__dict__
    src = inspect.getsource(R26Engine.detect_states)
    assert '"detector_can_execute": False' in src
    assert '"execution_authority": "FROZEN_R2_5_ONLY"' in src
    assert '"capital_authority": "FROZEN_R2_5_ONLY"' in src


def test_priority_threshold_frozen_at_one_atr():
    sig = inspect.signature(_priority_extreme_near)
    assert sig.parameters["threshold_atr"].default == 1.0


def test_execution_ready_requires_exact_r25_seed():
    src = inspect.getsource(R26Engine.detect_states)
    assert 'seed_present = (direction, ts) in official_keys' in src
    assert 'state = "EXECUTION_READY" if seed_present' in src


def test_parent_execution_cap_schedule():
    e = R26Engine()
    lp = e.risk_plan("LONG")
    sp = e.risk_plan("SHORT", "NOT_MATURE_BEAR")
    mp = e.risk_plan("SHORT", "MATURE_BEAR")
    assert (lp.seed_risk_R, lp.confirm_risk_add_R, lp.core_risk_add_R, lp.max_episode_risk_R) == (0.35, 0.0, 0.65, 1.0)
    assert (sp.seed_risk_R, sp.confirm_risk_add_R, sp.core_risk_add_R, sp.max_episode_risk_R) == (0.30, 0.0, 0.55, 0.85)
    assert (mp.seed_risk_R, mp.confirm_risk_add_R, mp.core_risk_add_R, mp.max_episode_risk_R) == (0.30, 0.0, 0.0, 0.30)


def main():
    tests = [
        test_execution_seed_identity,
        test_risk_plan_identity,
        test_no_execution_overrides,
        test_detector_only_addition,
        test_priority_threshold_frozen_at_one_atr,
        test_execution_ready_requires_exact_r25_seed,
        test_parent_execution_cap_schedule,
    ]
    for t in tests:
        t()
    print("R26_FINAL_INTEGRATION_CONTRACT_PASS")
    print(f"tests={len(tests)}")


if __name__ == "__main__":
    main()
