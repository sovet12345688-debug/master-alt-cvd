from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from r20_engine import build_feature_bundle, feature_frame, prefix_invariance_check

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "r20_candidate_config.json").read_text(encoding="utf-8"))


def make_daily(n: int = 280) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    t = np.arange(n, dtype=float)
    close = 8000.0 + 18.0 * t + 180.0 * np.sin(t / 9.0)
    open_ = close - 12.0 * np.cos(t / 5.0)
    high = np.maximum(open_, close) + 80.0 + 5.0 * np.sin(t / 3.0)
    low = np.minimum(open_, close) - 75.0 - 5.0 * np.cos(t / 4.0)
    volume = 1000.0 + 30.0 * (1.0 + np.sin(t / 7.0)) + (t % 11) * 4.0
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)


def make_h4(daily: pd.DataFrame) -> pd.DataFrame:
    rows = []
    idx = []
    for ts, r in daily.iterrows():
        base = float(r["open"])
        end = float(r["close"])
        for k in range(6):
            frac0 = k / 6.0
            frac1 = (k + 1) / 6.0
            o = base + (end - base) * frac0
            c = base + (end - base) * frac1
            wiggle = 20.0 + (k % 3) * 3.0
            h = max(o, c) + wiggle
            l = min(o, c) - wiggle
            v = float(r["volume"]) / 6.0 * (1.0 + 0.02 * k)
            idx.append(ts + pd.Timedelta(hours=4 * (k + 1)))
            rows.append((o, h, l, c, v))
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex(idx))


def test_prefix_invariance() -> None:
    d = make_daily()
    audit = prefix_invariance_check(d, CFG, cut_points=(80, 120, 180, 240))
    assert audit["pit_prefix_invariance"] == "PASS", audit


def test_future_mutation_cannot_change_past_features() -> None:
    d = make_daily()
    cut = 190
    base = feature_frame(d, CFG)
    mutated = d.copy()
    mutated.iloc[cut:, mutated.columns.get_loc("close")] *= 3.0
    mutated.iloc[cut:, mutated.columns.get_loc("open")] *= 3.0
    mutated.iloc[cut:, mutated.columns.get_loc("high")] *= 3.0
    mutated.iloc[cut:, mutated.columns.get_loc("low")] *= 3.0
    mutated.iloc[cut:, mutated.columns.get_loc("volume")] *= 9.0
    alt = feature_frame(mutated, CFG)
    cols = ["ema20", "ema50", "atr14", "prior20_high", "prior20_low", "volume_ratio", "ema20_slope_5", "ema50_slope_10", "causal_swing_low3", "causal_swing_high3"]
    for c in cols:
        assert np.allclose(base.iloc[:cut][c].to_numpy(float), alt.iloc[:cut][c].to_numpy(float), equal_nan=True, rtol=0.0, atol=1e-12), c


def test_prior_ranges_exclude_current_bar() -> None:
    d = make_daily(100)
    f = feature_frame(d, CFG)
    i = 70
    expected_hi = d["high"].iloc[i - 20 : i].max()
    expected_lo = d["low"].iloc[i - 20 : i].min()
    expected_vol = d["volume"].iloc[i - 20 : i].mean()
    assert abs(f["prior20_high"].iloc[i] - expected_hi) < 1e-12
    assert abs(f["prior20_low"].iloc[i] - expected_lo) < 1e-12
    assert abs(f["prior20_volume_mean"].iloc[i] - expected_vol) < 1e-12


def test_higher_timeframe_asof_never_uses_future_week() -> None:
    d = make_daily(160)
    h4 = make_h4(d)
    b = build_feature_bundle(d, h4, None, CFG)
    h = b["4H"]
    w = b["1W"]
    assert len(w) > 5
    valid = h["w_close"].notna()
    h_times = h.loc[valid].reset_index(names="timestamp")[["timestamp"]].sort_values("timestamp")
    w_rows = w.reset_index(names="timestamp")[["timestamp", "close"]].sort_values("timestamp")
    expected = pd.merge_asof(
        h_times,
        w_rows,
        on="timestamp",
        direction="backward",
        allow_exact_matches=True,
    )["close"].to_numpy(float)
    actual = h.loc[valid, "w_close"].to_numpy(float)
    assert np.allclose(actual, expected, equal_nan=True, rtol=0.0, atol=1e-12)


def run_all() -> dict:
    tests = [
        test_prefix_invariance,
        test_future_mutation_cannot_change_past_features,
        test_prior_ranges_exclude_current_bar,
        test_higher_timeframe_asof_never_uses_future_week,
    ]
    for t in tests:
        t()
    return {"pit_test": "PASS", "tests": len(tests), "historical_replay_performed": False}


if __name__ == "__main__":
    print(json.dumps(run_all(), indent=2))
