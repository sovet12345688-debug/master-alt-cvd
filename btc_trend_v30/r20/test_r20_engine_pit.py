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
    # SOURCE timestamps are candle OPEN times, matching Binance matrices.
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
            idx.append(ts + pd.Timedelta(hours=4 * k))
            rows.append((o, h, l, c, v))
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex(idx))


def make_h1(daily: pd.DataFrame) -> pd.DataFrame:
    rows = []
    idx = []
    for ts, r in daily.iloc[:5].iterrows():
        for k in range(24):
            o = float(r.open) + (float(r.close) - float(r.open)) * (k / 24.0)
            c = float(r.open) + (float(r.close) - float(r.open)) * ((k + 1) / 24.0)
            rows.append((o, max(o, c) + 5, min(o, c) - 5, c, float(r.volume) / 24.0))
            idx.append(ts + pd.Timedelta(hours=k))
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
    for c in ["close", "open", "high", "low"]:
        mutated.iloc[cut:, mutated.columns.get_loc(c)] *= 3.0
    mutated.iloc[cut:, mutated.columns.get_loc("volume")] *= 9.0
    alt = feature_frame(mutated, CFG)
    cols = ["ema20", "ema50", "atr14", "prior20_high", "prior20_low", "volume_ratio", "ema20_slope_5", "ema50_slope_10", "causal_swing_low3", "causal_swing_high3"]
    for c in cols:
        assert np.allclose(base.iloc[:cut][c].to_numpy(float), alt.iloc[:cut][c].to_numpy(float), equal_nan=True, rtol=0.0, atol=1e-12), c


def test_prior_ranges_exclude_current_bar() -> None:
    d = make_daily(100)
    f = feature_frame(d, CFG)
    i = 70
    assert abs(f["prior20_high"].iloc[i] - d["high"].iloc[i - 20 : i].max()) < 1e-12
    assert abs(f["prior20_low"].iloc[i] - d["low"].iloc[i - 20 : i].min()) < 1e-12
    assert abs(f["prior20_volume_mean"].iloc[i] - d["volume"].iloc[i - 20 : i].mean()) < 1e-12


def test_source_open_timestamps_shift_to_close_availability() -> None:
    d = make_daily(160)
    h4 = make_h4(d)
    h1 = make_h1(d)
    b = build_feature_bundle(d, h4, h1, CFG)
    assert b["4H"].index[0] == h4.index[0] + pd.Timedelta(hours=4)
    assert b["1H"].index[0] == h1.index[0] + pd.Timedelta(hours=1)


def test_intraday_never_uses_same_day_unclosed_daily_bar() -> None:
    d = make_daily(180)
    h4 = make_h4(d)
    b = build_feature_bundle(d, h4, None, CFG)
    h = b["4H"]
    # At any 4H close during source day i, latest Daily context must be source day i-1.
    i = 100
    source_day = d.index[i]
    prior_close = float(d.iloc[i - 1].close)
    q = h[(h.index > source_day) & (h.index < source_day + pd.Timedelta(days=1))]
    assert len(q) == 5
    assert np.allclose(q["d_close"].to_numpy(float), prior_close, rtol=0.0, atol=1e-12)
    # The final 4H close at next midnight may use the just-completed Daily bar exactly then.
    midnight = source_day + pd.Timedelta(days=1)
    assert abs(float(h.loc[midnight, "d_close"]) - float(d.iloc[i].close)) < 1e-12


def test_mutating_current_daily_bar_cannot_change_intraday_before_daily_close() -> None:
    d = make_daily(180)
    h4 = make_h4(d)
    i = 100
    source_day = d.index[i]
    base = build_feature_bundle(d, h4, None, CFG)["4H"]
    dm = d.copy()
    dm.iloc[i, dm.columns.get_loc("close")] *= 7.0
    dm.iloc[i, dm.columns.get_loc("high")] *= 7.0
    dm.iloc[i, dm.columns.get_loc("low")] *= 0.2
    dm.iloc[i, dm.columns.get_loc("volume")] *= 11.0
    alt = build_feature_bundle(dm, h4, None, CFG)["4H"]
    mask = (base.index > source_day) & (base.index < source_day + pd.Timedelta(days=1))
    cols = ["d_close", "d_ema20", "d_ema50", "d_atr14", "d_prior20_high", "d_prior20_low"]
    for c in cols:
        assert np.allclose(base.loc[mask, c].to_numpy(float), alt.loc[mask, c].to_numpy(float), equal_nan=True, rtol=0.0, atol=1e-12), c


def test_weekly_context_available_only_monday_utc() -> None:
    d = make_daily(220)
    h4 = make_h4(d)
    b = build_feature_bundle(d, h4, None, CFG)
    h = b["4H"]
    w = b["1W"]
    assert len(w) > 5
    assert all(ts.weekday() == 0 and ts.hour == 0 for ts in w.index)
    valid = h["w_close"].notna()
    hts = h.loc[valid].index.to_series().rename("timestamp").reset_index(drop=True).to_frame()
    wdf = w.reset_index()
    wdf = wdf.rename(columns={wdf.columns[0]: "timestamp"})[["timestamp", "close"]].sort_values("timestamp")
    expected = pd.merge_asof(hts.sort_values("timestamp"), wdf, on="timestamp", direction="backward", allow_exact_matches=True)["close"].to_numpy(float)
    actual = h.loc[valid, "w_close"].to_numpy(float)
    assert np.allclose(actual, expected, equal_nan=True, rtol=0.0, atol=1e-12)


def run_all() -> dict:
    tests = [
        test_prefix_invariance,
        test_future_mutation_cannot_change_past_features,
        test_prior_ranges_exclude_current_bar,
        test_source_open_timestamps_shift_to_close_availability,
        test_intraday_never_uses_same_day_unclosed_daily_bar,
        test_mutating_current_daily_bar_cannot_change_intraday_before_daily_close,
        test_weekly_context_available_only_monday_utc,
    ]
    for t in tests:
        t()
    return {"pit_test": "PASS", "tests": len(tests), "historical_replay_performed": False, "availability_semantics": "CANDLE_OPEN_PLUS_TF_CLOSE_OFFSET"}


if __name__ == "__main__":
    print(json.dumps(run_all(), indent=2))
