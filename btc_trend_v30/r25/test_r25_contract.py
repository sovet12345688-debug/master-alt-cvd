from __future__ import annotations

from btc_trend_v30.r24.r24_engine import R24Engine
from btc_trend_v30.r25.r25_engine import R25Engine


def main() -> None:
    e = R25Engine()

    # R2.5 may not redefine parent state/stop/exit methods.
    protected = [
        "long_confirmed", "short_confirmed", "long_core_allowed",
        "short_core_allowed_at", "long_exit_flags", "short_exit_flags",
        "classify_short_maturity", "short_risk_plan_at_seed",
    ]
    for name in protected:
        assert name not in R25Engine.__dict__, f"R25_FORBIDDEN_OVERRIDE:{name}"
        assert getattr(R25Engine, name) is getattr(R24Engine, name)

    lp = e.risk_plan("LONG")
    assert (lp.seed_risk_R, lp.confirm_risk_add_R, lp.core_risk_add_R, lp.max_episode_risk_R) == (0.35, 0.0, 0.65, 1.0)

    sp = e.risk_plan("SHORT", "NOT_MATURE_BEAR")
    assert (sp.seed_risk_R, sp.confirm_risk_add_R, sp.core_risk_add_R, sp.max_episode_risk_R) == (0.30, 0.0, 0.55, 0.85)

    mp = e.risk_plan("SHORT", "MATURE_BEAR")
    assert (mp.seed_risk_R, mp.confirm_risk_add_R, mp.core_risk_add_R, mp.max_episode_risk_R) == (0.30, 0.0, 0.0, 0.30)

    assert e.can_increase_risk("CONFIRMED", None, 0.65)
    assert not e.can_increase_risk("DERISK", object(), 0.65)
    assert not e.can_increase_risk("CONFIRMED", None, 0.0)

    cfg = e.r25_cfg
    assert cfg["single_change"]["id"] == "CAPITAL_AUTHORIZATION_ONLY_AT_CORE"
    assert cfg["evidence_firewall"]["pre_freeze_r25_historical_replay_count"] == 0
    assert cfg["governor_invariants"]["confirmed_state_has_no_incremental_capital_authority"] is True
    assert cfg["governor_invariants"]["only_core_may_authorize_incremental_risk"] is True

    print("R25_FULL_RISK_GOVERNOR_CONTRACT_PASS")


if __name__ == "__main__":
    main()
