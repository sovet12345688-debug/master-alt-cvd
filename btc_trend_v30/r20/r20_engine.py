from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
CANDIDATE_CFG = HERE / "r20_candidate_config.json"


class R20EngineError(RuntimeError):
    pass


def _load_cfg(path: Path = CANDIDATE_CFG) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if cfg.get("model") != "MASTER_BTC_TREND_V3_R2_0":
        raise R20EngineError("MODEL_IDENTITY_MISMATCH")
    return cfg


def _require_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    req = ["open", "high", "low", "close", "volume"]
    missing = [c for c in req if c not in df.columns]
    if missing:
        raise R20EngineError(f"MISSING_OHLCV:{','.join(missing)}")
    out = df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        raise R20EngineError("DATETIME_INDEX_REQUIRED")
    if out.index.tz is None:
        out.index = out.index.tz_localize("UTC")
    else:
        out.index = out.index.tz_convert("UTC")
    out = out.sort_index()
    if out.index.has_duplicates:
        raise R20EngineError("DUPLICATE_TIMESTAMPS")
    for c in req:
        out[c] = pd.to_numeric(out[c], errors="raise").astype(float)
    if ((out["high"] < out[["open", "close", "low"]].max(axis=1)) | (out["low"] > out[["open", "close", "high"]].min(axis=1))).any():
        raise R20EngineError("OHLC_LOGIC_ERROR")
    return out


def feature_frame(df: pd.DataFrame, cfg: Optional[dict] = None) -> pd.DataFrame:
    """Causal completed-candle features.

    Current completed OHLCV may be used at its own close timestamp. Any comparator
    that represents a prior range/baseline is explicitly shifted by one bar.
    """
    cfg = cfg or _load_cfg()
    x = _require_ohlcv(df)
    f = cfg["features"]
    ema_fast = int(f["ema_fast"])
    ema_slow = int(f["ema_slow"])
    atr_n = int(f["atr_period"])
    lookback = int(f["breakout_lookback"])
    vol_n = int(f["volume_lookback"])

    x["ema20"] = x["close"].ewm(span=ema_fast, adjust=False, min_periods=ema_fast).mean()
    x["ema50"] = x["close"].ewm(span=ema_slow, adjust=False, min_periods=ema_slow).mean()

    prev_close = x["close"].shift(1)
    tr = pd.concat(
        [
            x["high"] - x["low"],
            (x["high"] - prev_close).abs(),
            (x["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    x["atr14"] = tr.ewm(alpha=1.0 / atr_n, adjust=False, min_periods=atr_n).mean()

    x["prior20_high"] = x["high"].rolling(lookback, min_periods=lookback).max().shift(1)
    x["prior20_low"] = x["low"].rolling(lookback, min_periods=lookback).min().shift(1)
    x["prior20_volume_mean"] = x["volume"].rolling(vol_n, min_periods=vol_n).mean().shift(1)
    x["volume_ratio"] = x["volume"] / x["prior20_volume_mean"].replace(0.0, np.nan)

    x["ema20_slope_5"] = x["ema20"] - x["ema20"].shift(int(f["ema20_slope_days"]))
    x["ema50_slope_10"] = x["ema50"] - x["ema50"].shift(int(f["ema50_slope_days"]))
    x["ema20_rising_10"] = x["ema20"] > x["ema20"].shift(10)

    rng = (x["high"] - x["low"]).replace(0.0, np.nan)
    x["close_location"] = (x["close"] - x["low"]) / rng

    # Deterministic causal swing reference: prior 3 completed bars, excluding current.
    x["causal_swing_low3"] = x["low"].rolling(3, min_periods=3).min().shift(1)
    x["causal_swing_high3"] = x["high"].rolling(3, min_periods=3).max().shift(1)

    # Causal two-step structure reversal: prior bar first establishes LH/HL,
    # current completed bar then confirms LL/HH.
    x["lh_then_ll"] = (x["high"].shift(1) < x["high"].shift(2)) & (x["low"] < x["low"].shift(1))
    x["hl_then_hh"] = (x["low"].shift(1) > x["low"].shift(2)) & (x["high"] > x["high"].shift(1))
    return x


def completed_weekly_features(daily: pd.DataFrame, cfg: Optional[dict] = None) -> pd.DataFrame:
    """Build only completed UTC Monday-Sunday weeks from completed daily bars."""
    cfg = cfg or _load_cfg()
    d = _require_ohlcv(daily)
    # W-SUN label/right: week becomes available only at its final Sunday daily close.
    w = d.resample("W-SUN", label="right", closed="right").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"), volume=("volume", "sum")
    )
    # Require 7 daily bars so partial first/last weeks cannot masquerade as completed weeks.
    cnt = d["close"].resample("W-SUN", label="right", closed="right").count()
    w = w[cnt == 7].dropna()
    return feature_frame(w, cfg)


def asof_context(intraday: pd.DataFrame, higher: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """Attach the latest completed higher-timeframe row available at each intraday close."""
    lo = intraday.sort_index().reset_index().rename(columns={intraday.index.name or "index": "timestamp"})
    hi = higher.sort_index().reset_index().rename(columns={higher.index.name or "index": "timestamp"})
    rename = {c: f"{prefix}_{c}" for c in hi.columns if c != "timestamp"}
    hi = hi.rename(columns=rename)
    m = pd.merge_asof(lo, hi, on="timestamp", direction="backward", allow_exact_matches=True)
    return m.set_index("timestamp")


def build_feature_bundle(daily: pd.DataFrame, h4: pd.DataFrame, h1: Optional[pd.DataFrame] = None, cfg: Optional[dict] = None) -> Dict[str, pd.DataFrame]:
    cfg = cfg or _load_cfg()
    d = feature_frame(daily, cfg)
    h = feature_frame(h4, cfg)
    w = completed_weekly_features(daily, cfg)
    h_ctx = asof_context(asof_context(h, d, "d"), w, "w")
    out = {"1D": d, "4H": h_ctx, "1W": w}
    if h1 is not None:
        out["1H"] = feature_frame(h1, cfg)
    return out


def _finite(*vals: float) -> bool:
    return all(pd.notna(v) and np.isfinite(float(v)) for v in vals)


def long_watch(row: pd.Series, cfg: dict) -> bool:
    c = cfg["long"]["watch"]
    need = [row.get("d_close"), row.get("d_ema20"), row.get("d_ema20_slope_5"), row.get("d_atr14"), row.get("d_prior20_high"), row.get("close"), row.get("ema20"), row.get("ema20_slope_5")]
    if not _finite(*need):
        return False
    dist_ok = row["d_close"] >= row["d_prior20_high"] - float(c["distance_to_20d_high_atr_max"]) * row["d_atr14"]
    return bool(row["d_close"] > row["d_ema20"] and row["d_ema20_slope_5"] > 0 and dist_ok and row["close"] > row["ema20"] and row["ema20_slope_5"] > 0)


def short_watch(row: pd.Series, cfg: dict) -> bool:
    c = cfg["short"]["watch"]
    need = [row.get("d_close"), row.get("d_ema20"), row.get("d_ema20_slope_5"), row.get("d_atr14"), row.get("d_prior20_low"), row.get("close"), row.get("ema20"), row.get("ema20_slope_5")]
    if not _finite(*need):
        return False
    dist_ok = row["d_close"] <= row["d_prior20_low"] + float(c["distance_to_20d_low_atr_max"]) * row["d_atr14"]
    return bool(row["d_close"] < row["d_ema20"] and row["d_ema20_slope_5"] < 0 and dist_ok and row["close"] < row["ema20"] and row["ema20_slope_5"] < 0)


def _long_reclaim_flag(daily: pd.DataFrame, ts: pd.Timestamp, cfg: dict) -> bool:
    if ts not in daily.index:
        # most recent completed daily context
        pos = daily.index.searchsorted(ts, side="right") - 1
        if pos < 0:
            return False
        ts = daily.index[pos]
    i = daily.index.get_loc(ts)
    if not isinstance(i, (int, np.integer)) or i < 1:
        return False
    row = daily.iloc[i]
    prev = daily.iloc[i - 1]
    lb = int(cfg["long"]["seed"]["reclaim"]["prior_daily_ema20_breach_lookback_days"])
    start = max(0, i - lb)
    hist = daily.iloc[start:i]
    prior_breach = bool((hist["low"] < hist["ema20"]).any())
    return bool(
        prior_breach
        and pd.notna(row["ema20"])
        and row["close"] > row["ema20"]
        and row["close"] > prev["high"]
        and row["volume_ratio"] >= float(cfg["long"]["seed"]["reclaim"]["daily_volume_ratio_min"])
    )


def _short_failed_retest_flag(h4_ctx: pd.DataFrame, pos: int, cfg: dict) -> bool:
    row = h4_ctx.iloc[pos]
    if not _finite(row.get("d_ema20"), row.get("d_prior20_low"), row.get("high"), row.get("close"), row.get("ema20"), row.get("volume_ratio")):
        return False
    # Deterministic causal reference: lower of current completed daily EMA20 and prior-20D low.
    # A valid failed retest requires a prior completed 4H close below that reference within
    # the configured daily lookback, then current high trades back to/above reference and
    # current completed close returns below it.
    reference = min(float(row["d_ema20"]), float(row["d_prior20_low"]))
    bars = int(cfg["short"]["seed"]["failed_retest"]["prior_breakdown_lookback_days"]) * 6
    start = max(0, pos - bars)
    prior = h4_ctx.iloc[start:pos]
    prior_breakdown = bool((prior["close"] < reference).any())
    return bool(
        prior_breakdown
        and row["high"] >= reference
        and row["close"] < reference
        and row["close"] < row["ema20"]
        and row["volume_ratio"] >= float(cfg["short"]["seed"]["failed_retest"]["volume_ratio_min"])
    )


def long_seed_candidate(h4_ctx: pd.DataFrame, daily: pd.DataFrame, pos: int, cfg: dict) -> Optional[dict]:
    row = h4_ctx.iloc[pos]
    if not long_watch(row, cfg):
        return None
    b = cfg["long"]["seed"]["breakout"]
    breakout = bool(
        _finite(row.get("close"), row.get("prior20_high"), row.get("volume_ratio"), row.get("close_location"), row.get("d_close"), row.get("d_ema20"))
        and row["close"] > row["prior20_high"]
        and row["volume_ratio"] >= float(b["volume_ratio_min"])
        and row["close_location"] >= 1.0 - float(b["close_location_upper_fraction"])
        and row["d_close"] >= row["d_ema20"]
    )
    reclaim = _long_reclaim_flag(daily, h4_ctx.index[pos], cfg)
    if not (breakout or reclaim):
        return None
    if not _finite(row.get("causal_swing_low3"), row.get("atr14"), row.get("close")):
        return None
    stop = min(float(row["causal_swing_low3"]), float(row["close"] - cfg["long"]["seed"]["stop"]["atr_multiple"] * row["atr14"]))
    dist = (row["close"] - stop) / row["close"]
    if dist <= 0 or dist > float(cfg["long"]["seed"]["stop"]["max_stop_distance_pct"]):
        return None
    route = "BREAKOUT" if breakout else "RECLAIM"
    return {"direction": "LONG", "route": route, "entry": float(row["close"]), "stop": float(stop), "reference": float(row["prior20_high"] if breakout else row["d_ema20"]), "timestamp": h4_ctx.index[pos]}


def short_seed_candidate(h4_ctx: pd.DataFrame, pos: int, cfg: dict) -> Optional[dict]:
    row = h4_ctx.iloc[pos]
    if not short_watch(row, cfg):
        return None
    if not _finite(row.get("w_close"), row.get("w_ema20"), row.get("w_ema20_slope_5"), row.get("d_close"), row.get("d_ema50")):
        return None
    regime = bool((row["w_close"] < row["w_ema20"] or row["w_ema20_slope_5"] < 0) and row["d_close"] < row["d_ema50"])
    if not regime:
        return None
    b = cfg["short"]["seed"]["breakdown"]
    breakdown = bool(
        _finite(row.get("close"), row.get("prior20_low"), row.get("volume_ratio"), row.get("close_location"))
        and row["close"] < row["prior20_low"]
        and row["volume_ratio"] >= float(b["volume_ratio_min"])
        and row["close_location"] <= float(b["close_location_lower_fraction"])
    )
    failed_retest = _short_failed_retest_flag(h4_ctx, pos, cfg)
    if not (breakdown or failed_retest):
        return None
    if not _finite(row.get("causal_swing_high3"), row.get("atr14"), row.get("close")):
        return None
    stop = max(float(row["causal_swing_high3"]), float(row["close"] + cfg["short"]["seed"]["stop"]["atr_multiple"] * row["atr14"]))
    dist = (stop - row["close"]) / row["close"]
    if dist <= 0 or dist > float(cfg["short"]["seed"]["stop"]["max_stop_distance_pct"]):
        return None
    route = "BREAKDOWN" if breakdown else "FAILED_RETEST"
    reference = float(row["prior20_low"] if breakdown else min(row["d_ema20"], row["d_prior20_low"]))
    return {"direction": "SHORT", "route": route, "entry": float(row["close"]), "stop": float(stop), "reference": reference, "timestamp": h4_ctx.index[pos]}


@dataclass
class TrendPosition:
    direction: str
    state: str = "NEUTRAL"
    route: Optional[str] = None
    seed_time: Optional[pd.Timestamp] = None
    seed_entry: Optional[float] = None
    seed_stop: Optional[float] = None
    reference: Optional[float] = None
    seed_day_high: Optional[float] = None
    seed_day_low: Optional[float] = None
    confirm_deadline_index: Optional[int] = None
    exposure_R: float = 0.0
    derisked: bool = False
    last_exit_day_index: Optional[int] = None
    exit_family_anchor: Optional[float] = None
    events: list = field(default_factory=list)


class R20Engine:
    """Deterministic R2.0 engine. It never sees future labels or outcome fields."""

    def __init__(self, cfg: Optional[dict] = None):
        self.cfg = cfg or _load_cfg()

    def scan_seed_candidates(self, daily: pd.DataFrame, h4: pd.DataFrame, h1: Optional[pd.DataFrame] = None) -> pd.DataFrame:
        bundle = build_feature_bundle(daily, h4, h1, self.cfg)
        d = bundle["1D"]
        h = bundle["4H"]
        rows = []
        for i in range(len(h)):
            l = long_seed_candidate(h, d, i, self.cfg)
            s = short_seed_candidate(h, i, self.cfg)
            if l:
                rows.append(l)
            if s:
                rows.append(s)
        return pd.DataFrame(rows)

    def long_confirmed(self, daily: pd.DataFrame, seed_day_index: int, current_day_index: int, seed_high: float, reference: float) -> bool:
        c = self.cfg["long"]["confirm"]
        if current_day_index <= seed_day_index or current_day_index - seed_day_index > int(c["window_days"]):
            return False
        seg = daily.iloc[seed_day_index + 1 : current_day_index + 1]
        row = daily.iloc[current_day_index]
        if seg.empty or not _finite(row.get("close"), row.get("ema20"), row.get("ema20_slope_5"), row.get("atr14")):
            return False
        no_ref_fail = bool((seg["close"] >= (reference - float(c["reference_failure_atr"]) * seg["atr14"])).all())
        return bool((seg["close"] > seg["ema20"]).all() and (seg["high"] > seed_high).any() and row["ema20_slope_5"] > 0 and no_ref_fail)

    def short_confirmed(self, daily: pd.DataFrame, seed_day_index: int, current_day_index: int, seed_low: float, reference: float) -> bool:
        c = self.cfg["short"]["confirm"]
        if current_day_index <= seed_day_index or current_day_index - seed_day_index > int(c["window_days"]):
            return False
        seg = daily.iloc[seed_day_index + 1 : current_day_index + 1]
        row = daily.iloc[current_day_index]
        if seg.empty or not _finite(row.get("close"), row.get("ema20"), row.get("ema20_slope_5"), row.get("atr14")):
            return False
        no_ref_fail = bool((seg["close"] <= (reference + float(c["reference_failure_atr"]) * seg["atr14"])).all())
        return bool((seg["close"] < seg["ema20"]).all() and (seg["low"] < seed_low).any() and row["ema20_slope_5"] < 0 and no_ref_fail)

    def long_core_allowed(self, drow: pd.Series, wrow: pd.Series) -> bool:
        if not _finite(drow.get("close"), drow.get("ema20"), drow.get("ema50"), wrow.get("close"), wrow.get("ema20"), wrow.get("ema20_slope_5")):
            return False
        return bool(
            (wrow["close"] > wrow["ema20"] or wrow["ema20_slope_5"] >= 0)
            and drow["close"] > drow["ema50"]
            and (drow["ema20"] > drow["ema50"] or (bool(drow.get("ema20_rising_10", False)) and drow["close"] > drow["ema50"]))
        )

    def short_core_allowed(self, drow: pd.Series, wrow: pd.Series) -> bool:
        if not _finite(drow.get("ema20"), drow.get("ema50"), drow.get("ema50_slope_10"), wrow.get("close"), wrow.get("ema20")):
            return False
        return bool(drow["ema20"] < drow["ema50"] and drow["ema50_slope_10"] <= 0 and wrow["close"] < wrow["ema20"])

    def long_exit_flags(self, daily: pd.DataFrame, h4_ctx: pd.DataFrame, day_i: int, h4_i: int, highest_close_since_seed: float) -> Tuple[bool, bool]:
        d = daily.iloc[day_i]
        h = h4_ctx.iloc[h4_i]
        if not _finite(d.get("close"), d.get("ema20"), d.get("ema20_slope_5"), d.get("ema50"), d.get("atr14")):
            return False, False
        derisk = bool(d["close"] < d["ema20"] and bool(h.get("lh_then_ll", False)))
        two_below = False
        if day_i >= 1:
            p = daily.iloc[day_i - 1]
            two_below = bool(p["close"] < p["ema20"] and d["close"] < d["ema20"] and d["ema20_slope_5"] <= 0)
        chandelier = highest_close_since_seed - float(self.cfg["holding"]["long"]["chandelier_atr_multiple"]) * d["atr14"]
        full_exit = bool(two_below or d["close"] < d["ema50"] or d["close"] < chandelier)
        return derisk, full_exit

    def short_exit_flags(self, daily: pd.DataFrame, h4_ctx: pd.DataFrame, day_i: int, h4_i: int, lowest_close_since_seed: float) -> Tuple[bool, bool]:
        d = daily.iloc[day_i]
        h = h4_ctx.iloc[h4_i]
        if not _finite(d.get("close"), d.get("ema20"), d.get("ema20_slope_5"), d.get("ema50"), d.get("atr14")):
            return False, False
        derisk = bool(d["close"] > d["ema20"] and bool(h.get("hl_then_hh", False)))
        two_above = False
        if day_i >= 1:
            p = daily.iloc[day_i - 1]
            two_above = bool(p["close"] > p["ema20"] and d["close"] > d["ema20"] and d["ema20_slope_5"] >= 0)
        chandelier = lowest_close_since_seed + float(self.cfg["holding"]["short"]["chandelier_atr_multiple"]) * d["atr14"]
        full_exit = bool(two_above or d["close"] > d["ema50"] or d["close"] > chandelier)
        return derisk, full_exit


def prefix_invariance_check(df: pd.DataFrame, cfg: Optional[dict] = None, cut_points: Iterable[int] = (80, 120, 180)) -> dict:
    cfg = cfg or _load_cfg()
    full = feature_frame(df, cfg)
    cols = ["ema20", "ema50", "atr14", "prior20_high", "prior20_low", "volume_ratio", "ema20_slope_5", "ema50_slope_10", "causal_swing_low3", "causal_swing_high3", "lh_then_ll", "hl_then_hh"]
    checks = {}
    for n in cut_points:
        if n > len(df):
            continue
        part = feature_frame(df.iloc[:n].copy(), cfg)
        a = full.iloc[:n][cols]
        b = part[cols]
        numeric = [c for c in cols if c not in ("lh_then_ll", "hl_then_hh")]
        booleans = ["lh_then_ll", "hl_then_hh"]
        num_ok = all(np.allclose(a[c].to_numpy(float), b[c].to_numpy(float), equal_nan=True, rtol=0.0, atol=1e-12) for c in numeric)
        bool_ok = all(a[c].fillna(False).astype(bool).equals(b[c].fillna(False).astype(bool)) for c in booleans)
        checks[str(n)] = bool(num_ok and bool_ok)
    return {"pit_prefix_invariance": "PASS" if checks and all(checks.values()) else "FAIL", "checks": checks}


if __name__ == "__main__":
    cfg = _load_cfg()
    print(json.dumps({"model": cfg["model"], "status": cfg["status"], "engine": "IMPLEMENTATION_LOAD_PASS", "historical_replay_performed": False}, indent=2))
