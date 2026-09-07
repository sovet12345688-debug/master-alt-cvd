from r20_contract import R20Contract, LongState, ShortState, ContractError


def expect_error(fn):
    try:
        fn()
    except ContractError:
        return True
    raise AssertionError("expected ContractError")


def main():
    c = R20Contract()

    # Risk-budget invariants.
    assert abs(c.long_plan.total_R - 1.00) < 1e-12
    assert abs(c.short_plan.total_R - 0.85) < 1e-12
    assert c.long_plan.total_R <= c.long_plan.cap_R
    assert c.short_plan.total_R <= c.short_plan.cap_R

    # LONG legal path.
    s = LongState.NEUTRAL
    s = c.long_next(s, "WATCH_PASS"); assert s == LongState.WATCH
    s = c.long_next(s, "SEED_PASS"); assert s == LongState.SEED
    assert abs(c.exposure_after_state("long", s.value) - 0.35) < 1e-12
    s = c.long_next(s, "CONFIRM_PASS"); assert s == LongState.CONFIRMED
    assert abs(c.exposure_after_state("long", s.value) - 0.70) < 1e-12
    s = c.long_next(s, "CORE_PASS"); assert s == LongState.CORE
    assert abs(c.exposure_after_state("long", s.value) - 1.00) < 1e-12
    s = c.long_next(s, "HOLD"); assert s == LongState.HOLD
    s = c.long_next(s, "DERISK"); assert s == LongState.DERISK
    assert abs(c.exposure_after_state("long", s.value) - 0.50) < 1e-12
    s = c.long_next(s, "FULL_EXIT"); assert s == LongState.NEUTRAL

    # SHORT legal path and asymmetric lower cap.
    s2 = ShortState.NEUTRAL
    s2 = c.short_next(s2, "WATCH_PASS"); assert s2 == ShortState.WATCH
    s2 = c.short_next(s2, "SEED_PASS"); assert s2 == ShortState.SEED
    assert abs(c.exposure_after_state("short", s2.value) - 0.30) < 1e-12
    s2 = c.short_next(s2, "CONFIRM_PASS"); assert s2 == ShortState.CONFIRMED
    assert abs(c.exposure_after_state("short", s2.value) - 0.60) < 1e-12
    s2 = c.short_next(s2, "CORE_PASS"); assert s2 == ShortState.CORE
    assert abs(c.exposure_after_state("short", s2.value) - 0.85) < 1e-12

    # Failed thesis must reset, not auto re-enter.
    assert c.long_next(LongState.SEED, "INVALIDATED") == LongState.NEUTRAL
    assert c.short_next(ShortState.CONFIRMED, "INVALIDATED") == ShortState.NEUTRAL
    assert not c.reset_allowed(completed_daily_sessions_since_exit=2, new_causal_20d_family=True)
    assert not c.reset_allowed(completed_daily_sessions_since_exit=5, new_causal_20d_family=False)
    assert c.reset_allowed(completed_daily_sessions_since_exit=3, new_causal_20d_family=True)

    # Illegal jump / revenge re-entry style transition rejected.
    expect_error(lambda: c.long_next(LongState.NEUTRAL, "SEED_PASS"))
    expect_error(lambda: c.short_next(ShortState.NEUTRAL, "CORE_PASS"))
    expect_error(lambda: c.long_next(LongState.DERISK, "CORE_PASS"))

    # No fixed full TP, no stop widening.
    assert c.cfg["holding"]["fixed_profit_target"] is None
    assert c.cfg["holding"]["stop_may_widen"] is False

    print("R20_CONTRACT_TEST_PASS")


if __name__ == "__main__":
    main()
