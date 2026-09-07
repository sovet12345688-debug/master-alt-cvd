from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from btc_trend_v30.r24.r24_engine import R24Engine

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "r24_candidate_config.json").read_text(encoding="utf-8"))


def row(**kw) -> pd.Series:
    base = {
        "close": 80.0,
        "ema20": 82.0,
        "ema50": 90.0,
        "ema20_slope_5": -1.0,
        "ema50_slope_10": -0.5,
        "ema200": 100.0,
        "ema200_slope_20": -0.25,
    }
    base.update(kw)
    return pd.Series(base)


def main() -> None:
    e = R24Engine()

    # 1) Fully aligned causal bearish hierarchy => MATURE_BEAR.
    assert e.classify_short_maturity(row()) == "MATURE_BEAR"

    # 2) The classifier is structural, not a blanket BEAR veto.
    assert e.classify_short_maturity(row(ema50=101.0)) == "NOT_MATURE_BEAR"
    assert e.classify_short_maturity(row(ema200_slope_20=0.1)) == "NOT_MATURE_BEAR"

    # 3) Mature-bear plan preserves Seed but forbids additional risk.
    p = e.short_risk_plan_at_seed(row())
    e.validate_latched_plan(p)
    assert p.classification == "MATURE_BEAR"
    assert p.seed_risk_R == 0.30
    assert p.confirm_risk_add_R == 0.0
    assert p.core_risk_add_R == 0.0
    assert p.max_episode_risk_R == 0.30

    # 4) Non-mature plan is exact R2.1 SHORT risk identity.
    q = e.short_risk_plan_at_seed(row(ema50=101.0))
    e.validate_latched_plan(q)
    assert q.classification == "NOT_MATURE_BEAR"
    assert q.seed_risk_R == 0.30
    assert q.confirm_risk_add_R == 0.30
    assert q.core_risk_add_R == 0.25
    assert q.max_episode_risk_R == 0.85

    # 5) R2.4 must not override parent execution eligibility / stop / exit methods.
    forbidden_overrides = {
        "short_core_timing_allowed",
        "short_core_allowed_at",
        "short_core_allowed",
        "scan_seed_candidates",
        "long_seed_candidate",
        "short_seed_candidate",
        "simulate_episode",
    }
    assert forbidden_overrides.isdisjoint(set(R24Engine.__dict__.keys()))

    # 6) EMA200 risk features are prefix invariant; future closes cannot change past rows.
    idx = pd.date_range("2024-01-01", periods=260, freq="D", tz="UTC")
    close = np.linspace(100.0, 70.0, len(idx))
    df = pd.DataFrame({"close": close}, index=idx)
    full = e.augment_daily_maturity_features(df)
    prefix = e.augment_daily_maturity_features(df.iloc[:240])
    pd.testing.assert_series_equal(full.loc[prefix.index, "ema200"], prefix["ema200"])
    pd.testing.assert_series_equal(full.loc[prefix.index, "ema200_slope_20"], prefix["ema200_slope_20"])

    # 7) No forensic winner/loser cutoff is allowed into candidate config.
    c = CFG["mature_bear_classifier"]
    assert c["explicitly_not_used"] == [
        "DISTANCE_BELOW_EMA200_THRESHOLD",
        "DAYS_BELOW_EMA200_THRESHOLD",
        "DAYS_SINCE_60D_HIGH_THRESHOLD",
        "NEW_VOLUME_THRESHOLD",
        "NEW_BREAK_DEPTH_THRESHOLD",
        "WINNER_LOSER_MEDIAN_CUTOFF",
    ]
    assert CFG["risk_action"]["mature_bear"]["hard_veto"] is False
    assert CFG["evidence_firewall"]["r24_historical_replay_performed_before_freeze"] is False

    print("R24_MATURE_BEAR_SINGLE_CHANGE_CONTRACT_PASS")


if __name__ == "__main__":
    main()
