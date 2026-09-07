from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from btc_trend_v30.r20.r20_engine import long_watch, short_watch, R20Engine
from btc_trend_v30.r21.r21_engine import R21Engine
from btc_trend_v30.r22.r22_engine import R22Engine, _early_long_watch, _early_short_watch, _priority_distance

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "r22_candidate_config.json").read_text(encoding="utf-8"))


def row(**kwargs):
    return pd.Series(kwargs, dtype=object)


def test_long_distance_only_removed_from_hard_gate():
    r = row(
        d_close=100.0,d_ema20=95.0,d_ema20_slope_5=1.0,d_atr14=5.0,d_prior20_high=120.0,
        close=102.0,ema20=100.0,ema20_slope_5=1.0,
    )
    e = R22Engine()
    assert _early_long_watch(r) is True
    assert long_watch(r, e.cfg) is False
    assert _priority_distance(r,"LONG",1.0) is False


def test_short_distance_only_removed_from_hard_gate():
    r = row(
        d_close=100.0,d_ema20=105.0,d_ema20_slope_5=-1.0,d_atr14=5.0,d_prior20_low=80.0,
        close=98.0,ema20=100.0,ema20_slope_5=-1.0,
    )
    e = R22Engine()
    assert _early_short_watch(r) is True
    assert short_watch(r, e.cfg) is False
    assert _priority_distance(r,"SHORT",1.0) is False


def test_other_watch_atoms_remain_hard_gates():
    long_bad = row(d_close=94.0,d_ema20=95.0,d_ema20_slope_5=1.0,close=102.0,ema20=100.0,ema20_slope_5=1.0)
    short_bad = row(d_close=106.0,d_ema20=105.0,d_ema20_slope_5=-1.0,close=98.0,ema20=100.0,ema20_slope_5=-1.0)
    assert _early_long_watch(long_bad) is False
    assert _early_short_watch(short_bad) is False


def test_threshold_is_not_tuned():
    assert CFG["single_change"]["unchanged_distance_threshold_atr"] == 1.0
    assert CFG["single_change"]["hard_gate_removed"] is True
    assert CFG["single_change"]["priority_flag_retained"] is True


def test_r21_short_core_timing_inherited_unchanged():
    assert R22Engine.short_core_timing_allowed is R21Engine.short_core_timing_allowed
    assert R22Engine.short_core_allowed_at is R21Engine.short_core_allowed_at


def test_confirm_core_exit_logic_not_overridden():
    assert R22Engine.long_confirmed is R20Engine.long_confirmed
    assert R22Engine.short_confirmed is R20Engine.short_confirmed
    assert R22Engine.long_core_allowed is R20Engine.long_core_allowed
    assert R22Engine.long_exit_flags is R20Engine.long_exit_flags
    assert R22Engine.short_exit_flags is R20Engine.short_exit_flags


if __name__ == "__main__":
    tests=[v for k,v in globals().copy().items() if k.startswith("test_") and callable(v)]
    for t in tests: t()
    print(json.dumps({"r22_contract":"PASS","tests":len(tests),"historical_replay_performed":False},indent=2))
