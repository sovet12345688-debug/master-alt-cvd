from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from btc_trend_v30.r20.r20_engine import R20Engine, long_watch, short_watch
from btc_trend_v30.r21.r21_engine import R21Engine
from btc_trend_v30.r23.r23_detector import R23Engine, _early_long, _early_short, _priority_extreme_near

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "r23_frozen_config.json").read_text(encoding="utf-8"))


def row(**kwargs):
    return pd.Series(kwargs, dtype=object)


def test_execution_seed_method_is_parent_identity():
    assert R23Engine.scan_seed_candidates is R21Engine.scan_seed_candidates
    assert R21Engine.scan_seed_candidates is R20Engine.scan_seed_candidates


def test_early_long_can_exist_before_parent_watch():
    r = row(d_close=100.0,d_ema20=95.0,d_ema20_slope_5=1.0,d_atr14=5.0,d_prior20_high=120.0,close=102.0,ema20=100.0,ema20_slope_5=1.0)
    e = R23Engine()
    assert _early_long(r) is True
    assert _priority_extreme_near(r,"LONG",1.0) is False
    assert long_watch(r,e.cfg) is False


def test_early_short_can_exist_before_parent_watch():
    r = row(d_close=100.0,d_ema20=105.0,d_ema20_slope_5=-1.0,d_atr14=5.0,d_prior20_low=80.0,close=98.0,ema20=100.0,ema20_slope_5=-1.0)
    e = R23Engine()
    assert _early_short(r) is True
    assert _priority_extreme_near(r,"SHORT",1.0) is False
    assert short_watch(r,e.cfg) is False


def test_detector_has_no_execution_authority():
    sc = CFG["single_change"]
    assert CFG["status"] == "FINAL_FROZEN_NO_REPLAY"
    assert sc["early_detection_can_execute"] is False
    assert sc["early_detection_can_modify_seed"] is False
    assert sc["early_detection_can_modify_risk"] is False
    assert sc["early_detection_can_modify_stop"] is False
    assert sc["execution_ready_definition"] == "EXACT_PARENT_R21_SEED_CANDIDATE_ONLY"


def test_priority_threshold_not_tuned():
    p = CFG["single_change"]["priority_extreme_near"]
    assert p["threshold_atr"] == 1.0
    assert p["execution_authority"] is False


def test_r21_short_core_rule_inherited_exactly():
    assert R23Engine.short_core_timing_allowed is R21Engine.short_core_timing_allowed
    assert R23Engine.short_core_allowed_at is R21Engine.short_core_allowed_at


def test_states_are_awareness_then_parent_execution_only():
    assert CFG["single_change"]["states"] == ["EARLY_DETECT", "PRIORITY_WATCH", "EXECUTION_READY"]
    assert CFG["single_change"]["execution_engine"] == "FROZEN_R2_1_UNCHANGED"


if __name__ == "__main__":
    tests=[v for k,v in globals().copy().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(json.dumps({"r23_contract":"PASS","tests":len(tests),"historical_replay_performed":False},indent=2))
