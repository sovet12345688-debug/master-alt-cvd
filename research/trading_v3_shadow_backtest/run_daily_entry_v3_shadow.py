from __future__ import annotations

import io
import json
import math
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

# -----------------------------------------------------------------------------
# MASTER TRADING DAILY ENTRY ENGINE V3 — POINT-IN-TIME SHADOW BACKTEST
# Research only. Does not modify Production canonical/UI.
# -----------------------------------------------------------------------------

START = pd.Timestamp("2023-10-01T00:00:00Z")   # warmup + train
TRAIN_START = pd.Timestamp("2024-01-01T00:00:00Z")
TRAIN_END = pd.Timestamp("2025-01-01T00:00:00Z")
VAL_END = pd.Timestamp("2026-01-01T00:00:00Z")
END = pd.Timestamp("2026-09-01T00:00:00Z")
ASSETS = ["BTCUSDT", "ETHUSDT"]
OUT = Path("research/trading_v3_shadow_backtest/output")
OUT.mkdir(parents=True, exist_ok=True)

COLS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"
]
URL = "https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/15m/{symbol}-15m-{ym}.zip"
UA = "money-master-trading-v3-shadow/1.0"

# Research seeds from approved V3 design; NOT Production locks.
FINAL_SCORE_MIN = 78.0
PRE_SCORE_MIN = 72.0
MIN_COVERAGE = 0.70
TOUCH_SEARCH_HOURS = 12
TRIGGER_SEARCH_BARS = 4  # 1H after touch; consistent with fast daily-trading reaction preference
HORIZON_HOURS = 24
ATR_BUFFERS = [0.00, 0.10, 0.15, 0.20]


def months(start: pd.Timestamp, end: pd.Timestamp):
    cur = pd.Timestamp(start.year, start.month, 1, tz="UTC")
    last = pd.Timestamp(end.year, end.month, 1, tz="UTC")
    while cur < last:
        yield cur.strftime("%Y-%m")
        cur = cur + pd.offsets.MonthBegin(1)


def load_15m(symbol: str) -> Tuple[pd.DataFrame, List[str]]:
    frames, failures = [], []
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    for ym in months(START, END):
        try:
            r = s.get(URL.format(symbol=symbol, ym=ym), timeout=60)
            if r.status_code != 200:
                failures.append(f"{symbol}:{ym}:HTTP{r.status_code}")
                continue
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                names = [n for n in z.namelist() if n.endswith(".csv")]
                if not names:
                    failures.append(f"{symbol}:{ym}:NOCSV")
                    continue
                q = pd.read_csv(z.open(names[0]), header=None, names=COLS)
            ts = pd.to_numeric(q.open_time, errors="coerce")
            ts = np.where(ts > 1e14, ts / 1000.0, ts)
            q["time"] = pd.to_datetime(ts, unit="ms", utc=True, errors="coerce")
            for c in ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_quote"]:
                q[c] = pd.to_numeric(q[c], errors="coerce")
            frames.append(q[["time", "open", "high", "low", "close", "volume", "quote_volume", "taker_buy_quote"]])
        except Exception as e:
            failures.append(f"{symbol}:{ym}:{type(e).__name__}")
    if not frames:
        raise RuntimeError(f"no data for {symbol}")
    d = pd.concat(frames, ignore_index=True).dropna().drop_duplicates("time").sort_values("time")
    d = d[(d.time >= START) & (d.time < END)].set_index("time")
    return d, failures


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0)
    dn = -d.clip(upper=0)
    au = up.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    ad = dn.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    return (100 - 100/(1 + au/ad.replace(0, np.nan))).fillna(50)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df.close.shift(1)
    tr = pd.concat([(df.high-df.low).abs(), (df.high-pc).abs(), (df.low-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False, min_periods=n).mean()


def macd_hist(s: pd.Series) -> pd.Series:
    e12 = s.ewm(span=12, adjust=False).mean()
    e26 = s.ewm(span=26, adjust=False).mean()
    m = e12 - e26
    sig = m.ewm(span=9, adjust=False).mean()
    return m - sig


def kdj_k(df: pd.DataFrame, n: int = 9) -> pd.Series:
    lo = df.low.rolling(n).min()
    hi = df.high.rolling(n).max()
    rsv = 100 * (df.close-lo) / (hi-lo).replace(0, np.nan)
    return rsv.ewm(alpha=1/3, adjust=False).mean().fillna(50)


def resample(d: pd.DataFrame, rule: str) -> pd.DataFrame:
    x = pd.DataFrame({
        "open": d.open.resample(rule, label="right", closed="right").first(),
        "high": d.high.resample(rule, label="right", closed="right").max(),
        "low": d.low.resample(rule, label="right", closed="right").min(),
        "close": d.close.resample(rule, label="right", closed="right").last(),
        "volume": d.volume.resample(rule, label="right", closed="right").sum(),
        "quote_volume": d.quote_volume.resample(rule, label="right", closed="right").sum(),
        "taker_buy_quote": d.taker_buy_quote.resample(rule, label="right", closed="right").sum(),
    }).dropna()
    return x


def enrich(d: pd.DataFrame) -> pd.DataFrame:
    x = d.copy()
    x["ema20"] = x.close.ewm(span=20, adjust=False).mean()
    x["ma50"] = x.close.rolling(50).mean()
    x["ma200"] = x.close.rolling(200).mean()
    x["atr"] = atr(x)
    x["rsi"] = rsi(x.close)
    x["macdh"] = macd_hist(x.close)
    x["kdjk"] = kdj_k(x)
    x["rv"] = x.volume / x.volume.rolling(20).mean().replace(0, np.nan)
    x["net_taker_quote"] = 2*x.taker_buy_quote - x.quote_volume
    x["taker_imb"] = x.net_taker_quote / x.quote_volume.replace(0, np.nan)
    x["taker_imb4"] = x.net_taker_quote.rolling(4).sum() / x.quote_volume.rolling(4).sum().replace(0, np.nan)
    x["cvd12"] = x.net_taker_quote.rolling(12).sum()
    x["ema_slope"] = (x.ema20-x.ema20.shift(3)) / x.atr.replace(0, np.nan)
    x["atr_pct"] = x.atr / x.close
    x["atr_pct_rank"] = x.atr_pct.rolling(240).rank(pct=True)
    rg = (x.high-x.low).replace(0, np.nan)
    x["lower_wick"] = (np.minimum(x.open, x.close)-x.low)/rg
    x["upper_wick"] = (x.high-np.maximum(x.open, x.close))/rg
    x["close_loc"] = (x.close-x.low)/rg
    x["lo8"] = x.low.shift(1).rolling(8).min()
    x["hi8"] = x.high.shift(1).rolling(8).max()
    x["lo12"] = x.low.shift(1).rolling(12).min()
    x["hi12"] = x.high.shift(1).rolling(12).max()
    x["hi48"] = x.high.shift(1).rolling(48).max()
    x["lo48"] = x.low.shift(1).rolling(48).min()
    x["hi168"] = x.high.shift(1).rolling(168).max()
    x["lo168"] = x.low.shift(1).rolling(168).min()
    return x


def add_session_vwap(m15: pd.DataFrame) -> pd.DataFrame:
    x = m15.copy()
    typ = (x.high+x.low+x.close)/3
    day = x.index.floor("D")
    pv = typ*x.quote_volume
    x["session_vwap"] = pv.groupby(day).cumsum()/x.quote_volume.groupby(day).cumsum().replace(0, np.nan)
    return x


def prior_day_levels(d1: pd.DataFrame) -> pd.DataFrame:
    q = d1[["high", "low", "close"]].copy()
    q.columns = ["pdh", "pdl", "pdc"]
    return q.shift(1)


def confirmed_pivot_events(h4: pd.DataFrame) -> List[dict]:
    # 5-bar fractal. Pivot at i-2 becomes known only at current bar i (8h later).
    ev = []
    hi = h4.high.values
    lo = h4.low.values
    idx = h4.index
    for i in range(4, len(h4)):
        j = i-2
        if hi[j] == max(hi[i-4:i+1]):
            ev.append({"confirm": idx[i], "pivot_time": idx[j], "type": "H", "price": float(hi[j])})
        if lo[j] == min(lo[i-4:i+1]):
            ev.append({"confirm": idx[i], "pivot_time": idx[j], "type": "L", "price": float(lo[j])})
    return sorted(ev, key=lambda z: z["confirm"])


def pivot_state(events: List[dict], t: pd.Timestamp) -> Tuple[Optional[dict], Optional[dict], List[dict]]:
    e = [x for x in events if x["confirm"] <= t]
    ph = next((x for x in reversed(e) if x["type"] == "H"), None)
    pl = next((x for x in reversed(e) if x["type"] == "L"), None)
    return ph, pl, e


def fib_levels(ph: Optional[dict], pl: Optional[dict], direction: str) -> Tuple[List[float], List[float]]:
    if ph is None or pl is None:
        return [], []
    H, L = float(ph["price"]), float(pl["price"])
    if H <= L:
        return [], []
    rng = H-L
    if direction == "LONG":
        retr = [H-rng*r for r in [0.382, 0.5, 0.618, 0.786]]
        ext = [L+rng*r for r in [1.272, 1.618]]
    else:
        retr = [L+rng*r for r in [0.382, 0.5, 0.618, 0.786]]
        ext = [H-rng*r for r in [1.272, 1.618]]
    return retr, ext


def rolling_vpoc(h1: pd.DataFrame, pos: int, bins: int = 24) -> float:
    if pos < 48:
        return np.nan
    w = h1.iloc[max(0, pos-168):pos]
    if len(w) < 48:
        return np.nan
    p = ((w.high+w.low+w.close)/3).values
    v = w.quote_volume.values
    mn, mx = float(np.min(p)), float(np.max(p))
    if not np.isfinite(mn+mx) or mx <= mn:
        return np.nan
    edges = np.linspace(mn, mx, bins+1)
    ids = np.clip(np.digitize(p, edges)-1, 0, bins-1)
    vols = np.bincount(ids, weights=v, minlength=bins)
    k = int(np.argmax(vols))
    return float((edges[k]+edges[k+1])/2)


def anchored_vwap(h1: pd.DataFrame, pos: int, pivot_time: Optional[pd.Timestamp]) -> float:
    if pivot_time is None:
        return np.nan
    p0 = h1.index.searchsorted(pivot_time)
    if p0 >= pos:
        return np.nan
    w = h1.iloc[p0:pos+1]
    if w.quote_volume.sum() <= 0:
        return np.nan
    typ = (w.high+w.low+w.close)/3
    return float((typ*w.quote_volume).sum()/w.quote_volume.sum())


def regime_4h(r) -> str:
    if any(pd.isna(getattr(r, k)) for k in ["ema20", "ma50", "atr", "ema_slope"]):
        return "NA"
    if r.ema20 > r.ma50 and r.close > r.ema20 and r.ema_slope > 0.10:
        return "BULL_TREND"
    if r.ema20 < r.ma50 and r.close < r.ema20 and r.ema_slope < -0.10:
        return "BEAR_TREND"
    if abs(float(r.ema_slope)) < 0.12:
        return "RANGE"
    return "TRANSITION"


def before(df: pd.DataFrame, t: pd.Timestamp):
    q = df[df.index <= t]
    return None if q.empty else q.iloc[-1]


def cluster_refs(refs: List[Tuple[str, str, float]], atr1: float) -> List[dict]:
    refs = [(fam, name, float(v)) for fam, name, v in refs if pd.notna(v) and v > 0]
    refs.sort(key=lambda x: x[2])
    out = []
    tol = max(atr1*0.30, 1e-9)
    for fam, name, v in refs:
        hit = None
        for c in out:
            if abs(v-c["center"]) <= tol:
                hit = c; break
        if hit is None:
            out.append({"center": v, "values": [v], "families": {fam}, "names": [name]})
        else:
            hit["values"].append(v)
            hit["families"].add(fam)
            hit["names"].append(name)
            hit["center"] = float(np.mean(hit["values"]))
    return out


@dataclass
class Setup:
    asset: str
    version: str
    t: pd.Timestamp
    direction: str
    entry_center: float
    zone_low: float
    zone_high: float
    stop: float
    tp1: float
    tp2: float
    tp3: float
    pre_score: float
    pre_coverage: float
    regime: str
    fib_used: bool
    atr_buffer: float
    sources: str


def select_targets(direction: str, entry: float, stop: float, candidates: List[float]) -> Optional[Tuple[float,float,float]]:
    risk = (entry-stop) if direction == "LONG" else (stop-entry)
    if risk <= 0:
        return None
    if direction == "LONG":
        c = sorted(set(float(x) for x in candidates if pd.notna(x) and x > entry + 0.60*risk))
        tp1 = next((x for x in c if (x-entry)/risk >= 1.0), None)
        tp2 = next((x for x in c if (x-entry)/risk >= 3.0), None)
        tp3 = next((x for x in c if tp2 is not None and x > tp2 and (x-entry)/risk >= 4.0), None)
    else:
        c = sorted(set(float(x) for x in candidates if pd.notna(x) and x < entry - 0.60*risk), reverse=True)
        tp1 = next((x for x in c if (entry-x)/risk >= 1.0), None)
        tp2 = next((x for x in c if (entry-x)/risk >= 3.0), None)
        tp3 = next((x for x in c if tp2 is not None and x < tp2 and (entry-x)/risk >= 4.0), None)
    if tp1 is None or tp2 is None:
        return None
    if tp3 is None:
        # Do not manufacture a distant Fib target. Use a confirmed >=4R rolling objective only if present.
        return None
    return float(tp1), float(tp2), float(tp3)


def baseline_setups(asset: str, h1: pd.DataFrame, h4: pd.DataFrame, d1: pd.DataFrame) -> List[Setup]:
    out = []
    last = -999
    for i, (t, r) in enumerate(h1.iterrows()):
        if t < TRAIN_START or i < 220 or i-last < 8:
            continue
        r4 = before(h4, t)
        rd = before(d1, t)
        if r4 is None or rd is None or any(pd.isna(x) for x in [r.ema20, r.ma50, r.atr, r.lo8, r.hi8, r4.ema20, r4.ma50]):
            continue
        L = r.ema20 > r.ma50 and r.close > r.ema20 and r.ema_slope > 0 and r4.close > r4.ema20
        S = r.ema20 < r.ma50 and r.close < r.ema20 and r.ema_slope < 0 and r4.close < r4.ema20
        if not (L or S):
            continue
        direction = "LONG" if L else "SHORT"
        entry = float(r.ema20)
        av = float(r.atr)
        if direction == "LONG":
            stop = min(float(r.lo8), entry-1.05*av)-0.05*av
            risk = entry-stop
            candidates = [r.hi48, r.hi168, rd.high, r4.high]
            # Baseline proxy retains canonical 3R structure even if TP3 is formulaic.
            tp1, tp2, tp3 = entry+risk, entry+3*risk, entry+4*risk
        else:
            stop = max(float(r.hi8), entry+1.05*av)+0.05*av
            risk = stop-entry
            candidates = [r.lo48, r.lo168, rd.low, r4.low]
            tp1, tp2, tp3 = entry-risk, entry-3*risk, entry-4*risk
        if risk <= 0:
            continue
        # Baseline reproducible execution proxy, not reconstructed historical MASTER signals.
        out.append(Setup(asset, "BASELINE", t, direction, entry, entry-0.15*av, entry+0.15*av,
                         float(stop), float(tp1), float(tp2), float(tp3), np.nan, 1.0,
                         regime_4h(r4), False, 0.0, "1H_EMA20+STRUCTURAL_SL"))
        last = i
    return out


def v3_setups(asset: str, h1: pd.DataFrame, h4: pd.DataFrame, d1: pd.DataFrame,
              pd_levels: pd.DataFrame, session_vwap_1h: pd.Series, events: List[dict],
              with_fib: bool, atr_buffer: float, btc_h1: Optional[pd.DataFrame] = None) -> List[Setup]:
    out = []
    last = -999
    for i, (t, r) in enumerate(h1.iterrows()):
        if t < TRAIN_START or i < 220 or i-last < 6:
            continue
        r4 = before(h4, t)
        rd = before(d1, t)
        pdlv = before(pd_levels, t)
        if r4 is None or rd is None or pdlv is None or any(pd.isna(x) for x in [r.ema20, r.ma50, r.atr, r4.ema20, r4.ma50, r4.atr]):
            continue
        reg = regime_4h(r4)
        L = reg == "BULL_TREND" and r.close > r.ma50 and r.ema20 >= r.ma50*0.995
        S = reg == "BEAR_TREND" and r.close < r.ma50 and r.ema20 <= r.ma50*1.005
        if not (L or S):
            continue
        direction = "LONG" if L else "SHORT"
        av = float(r.atr)
        if av <= 0:
            continue
        ph, pl, _ = pivot_state(events, t)
        retr, ext = fib_levels(ph, pl, direction) if with_fib else ([], [])
        avwap = anchored_vwap(h1, i, (pl if direction == "LONG" else ph)["pivot_time"] if (pl if direction == "LONG" else ph) else None)
        vpoc = rolling_vpoc(h1, i)
        sv = session_vwap_1h.loc[:t].iloc[-1] if len(session_vwap_1h.loc[:t]) else np.nan

        refs: List[Tuple[str,str,float]] = [
            ("EMA", "1H_EMA20", r.ema20), ("EMA", "1H_MA50", r.ma50),
            ("MTF", "4H_EMA20", r4.ema20), ("MTF", "4H_MA50", r4.ma50),
            ("PD", "PDC", pdlv.pdc), ("VWAP", "SESSION_VWAP", sv),
            ("VWAP", "ANCHORED_VWAP", avwap), ("VP", "VPOC_7D", vpoc),
        ]
        if direction == "LONG": refs += [("PD", "PDL", pdlv.pdl), ("STRUCT", "LO12", r.lo12)]
        else: refs += [("PD", "PDH", pdlv.pdh), ("STRUCT", "HI12", r.hi12)]
        if with_fib:
            refs += [("FIB", f"FIB_{k}", v) for k, v in zip(["382","500","618","786"], retr)]

        clusters = cluster_refs(refs, av)
        if direction == "LONG":
            clusters = [c for c in clusters if c["center"] < r.close and r.close-c["center"] <= 2.0*av]
        else:
            clusters = [c for c in clusters if c["center"] > r.close and c["center"]-r.close <= 2.0*av]
        clusters = [c for c in clusters if len(c["families"]) >= 2]
        if not clusters:
            continue
        # Reward independent family count, then prefer nearer underextended location.
        clusters.sort(key=lambda c: (len(c["families"]), -abs(r.close-c["center"])/av), reverse=True)
        c = clusters[0]
        center = float(c["center"])
        zlo, zhi = center-0.15*av, center+0.15*av

        if direction == "LONG":
            structure = min(float(r.lo12), zlo)
            if pl is not None and pl["price"] < r.close: structure = min(structure, float(pl["price"]))
            stop = structure-atr_buffer*av
        else:
            structure = max(float(r.hi12), zhi)
            if ph is not None and ph["price"] > r.close: structure = max(structure, float(ph["price"]))
            stop = structure+atr_buffer*av
        risk = (center-stop) if direction == "LONG" else (stop-center)
        if risk <= 0 or risk/center > 0.08:
            continue

        target_refs = [r.hi48, r.hi168, pdlv.pdh, rd.high] if direction == "LONG" else [r.lo48, r.lo168, pdlv.pdl, rd.low]
        if ph is not None: target_refs.append(ph["price"])
        if pl is not None: target_refs.append(pl["price"])
        target_refs += ext
        tg = select_targets(direction, center, stop, target_refs)
        if tg is None:
            continue
        tp1, tp2, tp3 = tg

        # PRE-TOUCH SCORE with N/A-aware denominator.
        pts = 0.0; mx = 0.0
        # 1) Regime & MTF — 12
        mx += 12
        pts += 5  # 4H regime fit by construction
        pts += 4 if ((direction=="LONG" and r.close>r.ema20) or (direction=="SHORT" and r.close<r.ema20)) else 2
        pts += 3 if ((direction=="LONG" and rd.close>rd.ema20) or (direction=="SHORT" and rd.close<rd.ema20)) else 1
        # 2) Structure & location — 22
        mx += 22
        struct_good = (direction=="LONG" and r.close>r.ma50 and r.lo12<r.close) or (direction=="SHORT" and r.close<r.ma50 and r.hi12>r.close)
        pts += 8 if struct_good else 4
        famn = len(c["families"])
        pts += min(5, 2+famn)
        pts += 3 if ("MTF" in c["families"] or "STRUCT" in c["families"]) else 1
        pts += 3 if any(f in c["families"] for f in ["VWAP","VP"]) else 0
        pts += 2 if "PD" in c["families"] else 0
        pts += 1 if (with_fib and "FIB" in c["families"]) else 0
        # 3) Participation/order-flow — 10 available of 14
        mx += 10
        pts += 5 if r.rv >= 1.0 else (3 if r.rv >= 0.8 else 1)
        imb = float(r.taker_imb4) if pd.notna(r.taker_imb4) else 0.0
        cvd = float(r.cvd12) if pd.notna(r.cvd12) else 0.0
        flow_ok = (imb>0 and cvd>0) if direction=="LONG" else (imb<0 and cvd<0)
        pts += 5 if flow_ok else (2 if abs(imb)<0.02 else 0)
        # 4) Derivatives/liquidity — N/A in long history; zero denominator, not zero points.
        # 5) Volatility / extension / non-chasing — 7
        mx += 7
        ar = float(r.atr_pct_rank) if pd.notna(r.atr_pct_rank) else 0.5
        pts += 3 if 0.15 <= ar <= 0.90 else 1
        extn = abs(float(r.close-center))/av
        pts += 2 if extn <= 1.2 else (1 if extn <= 1.8 else 0)
        pts += 2 if risk/av <= 2.0 else (1 if risk/av <= 2.8 else 0)
        # 6) Secondary context — momentum 2 + wave 1 + relative/cross up to 2 if available
        mx += 3
        mom = 0
        if direction == "LONG":
            mom = int(r.rsi>=45) + int(r.macdh>=0) + int(r.kdjk>=45)
        else:
            mom = int(r.rsi<=55) + int(r.macdh<=0) + int(r.kdjk<=55)
        pts += min(2, mom*(2/3))
        wave_ok = (direction=="LONG" and r4.ema_slope>0) or (direction=="SHORT" and r4.ema_slope<0)
        pts += 1 if wave_ok else 0
        if asset != "BTCUSDT" and btc_h1 is not None:
            br = before(btc_h1, t)
            if br is not None:
                mx += 2
                rel_ok = (direction=="LONG" and r.close/r.close.shift(24) if False else True)
                btc_ok = (br.close>br.ema20) if direction=="LONG" else (br.close<br.ema20)
                pts += 2 if btc_ok else 0
        pre_cov = mx/70.0
        pre_score = 100*pts/mx if mx>0 else 0
        if pre_cov < MIN_COVERAGE*0.70 or pre_score < PRE_SCORE_MIN:
            continue

        out.append(Setup(asset, "V3_FIB" if with_fib else "V3_NOFIB", t, direction, center, zlo, zhi,
                         float(stop), tp1, tp2, tp3, float(pre_score), float(pre_cov), reg,
                         bool(with_fib and "FIB" in c["families"]), atr_buffer,
                         "+".join(sorted(c["names"]))))
        last = i
    return out


def find_execution(setup: Setup, m15: pd.DataFrame) -> Optional[dict]:
    z = m15[(m15.index > setup.t) & (m15.index <= setup.t + pd.Timedelta(hours=TOUCH_SEARCH_HOURS))]
    if len(z) < 3:
        return None
    if setup.direction == "LONG":
        hit = np.where((z.low.values <= setup.zone_high) & (z.high.values >= setup.zone_low))[0]
    else:
        hit = np.where((z.high.values >= setup.zone_low) & (z.low.values <= setup.zone_high))[0]
    if not len(hit):
        return {"missed": True}
    touch_local = int(hit[0])
    zz = z.iloc[touch_local:touch_local+TRIGGER_SEARCH_BARS+2]
    if len(zz) < 2:
        return None
    touch_time = zz.index[0]
    for j in range(len(zz)-1):
        r = zz.iloc[j]
        n = zz.iloc[j+1]
        rg = max(float(r.high-r.low), 1e-12)
        lw = (min(float(r.open),float(r.close))-float(r.low))/rg
        uw = (float(r.high)-max(float(r.open),float(r.close)))/rg
        if setup.direction == "LONG":
            if r.low <= setup.stop:
                return None
            candle = ((r.low <= setup.zone_high and r.close >= setup.entry_center and r.close > r.open and lw >= 0.20)
                      or (r.low < setup.zone_low and r.close > setup.entry_center and r.close > r.open))
            hold = n.low >= r.low
            flow = (r.rv >= 0.8) or (pd.notna(r.taker_imb4) and r.taker_imb4 >= -0.01)
        else:
            if r.high >= setup.stop:
                return None
            candle = ((r.high >= setup.zone_low and r.close <= setup.entry_center and r.close < r.open and uw >= 0.20)
                      or (r.high > setup.zone_high and r.close < setup.entry_center and r.close < r.open))
            hold = n.high <= r.high
            flow = (r.rv >= 0.8) or (pd.notna(r.taker_imb4) and r.taker_imb4 <= 0.01)
        if not (candle and hold and flow):
            continue
        # Enter at next completed bar open; reject chase.
        ep = float(n.open)
        risk = (ep-setup.stop) if setup.direction=="LONG" else (setup.stop-ep)
        if risk <= 0:
            continue
        atr_now = float(r.atr) if pd.notna(r.atr) else abs(setup.zone_high-setup.zone_low)/0.30
        if abs(ep-setup.entry_center) > max(0.40*atr_now, 1e-9):
            continue
        rr2 = ((setup.tp2-ep)/risk) if setup.direction=="LONG" else ((ep-setup.tp2)/risk)
        if rr2 < 3.0:
            continue
        # Reaction score. 22 max available: candle10 + volume/CVD7 + time5. Microstructure/OI N/A.
        p=0.0; mx=22.0
        p += 6
        body = abs(float(r.close-r.open))/rg
        p += 2 if body>=0.35 else 1
        p += 2 if hold else 0
        p += 4 if r.rv>=1.0 else (2 if r.rv>=0.8 else 0)
        im = float(r.taker_imb4) if pd.notna(r.taker_imb4) else 0
        p += 3 if ((setup.direction=="LONG" and im>0) or (setup.direction=="SHORT" and im<0)) else 1
        delay = j
        p += 2 if delay<=1 else (1 if delay<=2 else 0)
        p += 1 if delay<=2 else 0
        p += 2  # next bar entry remains near structure by non-chase check
        reaction_score = 100*p/mx
        # Combine pre-touch 70 / reaction30 using normalized scores, while tracking historical coverage.
        final_score = 0.70*setup.pre_score + 0.30*reaction_score if setup.version.startswith("V3") else np.nan
        coverage = setup.pre_coverage*0.70 + (22/30)*0.30 if setup.version.startswith("V3") else 1.0
        if setup.version.startswith("V3") and (coverage < MIN_COVERAGE or final_score < FINAL_SCORE_MIN):
            continue
        return {
            "missed": False, "touch_time": touch_time, "trigger_time": zz.index[j], "entry_time": zz.index[j+1],
            "entry": ep, "reaction_score": reaction_score, "final_score": final_score,
            "coverage": coverage, "delay_bars": delay, "rr2": rr2,
        }
    return None


def evaluate_trade(setup: Setup, ex: dict, m15: pd.DataFrame) -> dict:
    et = ex["entry_time"]
    ep = ex["entry"]
    risk = (ep-setup.stop) if setup.direction=="LONG" else (setup.stop-ep)
    z = m15[(m15.index >= et) & (m15.index <= et+pd.Timedelta(hours=HORIZON_HOURS))]
    if z.empty or risk <= 0:
        return {}
    if setup.direction == "LONG":
        stop_hit = np.where(z.low.values <= setup.stop)[0]
        tp1_hit = np.where(z.high.values >= setup.tp1)[0]
        tp2_hit = np.where(z.high.values >= setup.tp2)[0]
        tp3_hit = np.where(z.high.values >= setup.tp3)[0]
        mfe = (float(z.high.max())-ep)/risk
        mae = (ep-float(z.low.min()))/risk
        final_r = (float(z.close.iloc[-1])-ep)/risk
    else:
        stop_hit = np.where(z.high.values >= setup.stop)[0]
        tp1_hit = np.where(z.low.values <= setup.tp1)[0]
        tp2_hit = np.where(z.low.values <= setup.tp2)[0]
        tp3_hit = np.where(z.low.values <= setup.tp3)[0]
        mfe = (ep-float(z.low.min()))/risk
        mae = (float(z.high.max())-ep)/risk
        final_r = (ep-float(z.close.iloc[-1]))/risk
    si = int(stop_hit[0]) if len(stop_hit) else None
    t2 = int(tp2_hit[0]) if len(tp2_hit) else None
    if t2 is not None and (si is None or t2 < si):
        outcome = "TP2_CORE"
        R = ((setup.tp2-ep)/risk) if setup.direction=="LONG" else ((ep-setup.tp2)/risk)
        exit_idx = t2
    elif si is not None and (t2 is None or si < t2):
        outcome = "SL"
        R = -1.0
        exit_idx = si
    else:
        outcome = "TIME"
        R = float(np.clip(final_r, -1.0, 4.0))
        exit_idx = len(z)-1
    return {
        "asset": setup.asset, "version": setup.version, "direction": setup.direction, "regime": setup.regime,
        "setup_time": setup.t, "entry_time": et, "exit_time": z.index[exit_idx], "entry": ep,
        "stop": setup.stop, "tp1": setup.tp1, "tp2": setup.tp2, "tp3": setup.tp3,
        "R": float(R), "outcome": outcome, "MFE": float(mfe), "MAE": float(mae),
        "tp1_hit": bool(len(tp1_hit) and (si is None or int(tp1_hit[0]) < si)),
        "tp2_hit": bool(len(tp2_hit) and (si is None or int(tp2_hit[0]) < si)),
        "tp3_hit": bool(len(tp3_hit) and (si is None or int(tp3_hit[0]) < si)),
        "false_start": bool(outcome=="SL" and mfe < 0.5),
        "pre_score": setup.pre_score, "reaction_score": ex.get("reaction_score"), "final_score": ex.get("final_score"),
        "coverage": ex.get("coverage",1.0), "delay_bars": ex.get("delay_bars"), "rr2": ex.get("rr2"),
        "fib_used": setup.fib_used, "atr_buffer": setup.atr_buffer, "sources": setup.sources,
    }


def replay(setups: List[Setup], m15: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    rows = []
    missed = 0
    active_until = pd.Timestamp.min.tz_localize("UTC")
    for s in sorted(setups, key=lambda x: x.t):
        ex = find_execution(s, m15)
        if ex is None:
            continue
        if ex.get("missed"):
            missed += 1
            continue
        if ex["entry_time"] <= active_until:
            continue
        tr = evaluate_trade(s, ex, m15)
        if tr:
            rows.append(tr)
            active_until = tr["exit_time"]
    return pd.DataFrame(rows), missed


def split_name(t: pd.Timestamp) -> str:
    if t < TRAIN_END:
        return "TRAIN"
    if t < VAL_END:
        return "VALIDATION"
    return "OOS"


def metrics(x: pd.DataFrame) -> dict:
    if x.empty:
        return {"n":0}
    r = x.R.astype(float)
    neg = -r[r<0].sum()
    pf = r[r>0].sum()/neg if neg>0 else np.inf
    eq = r.cumsum()
    dd = float((eq-eq.cummax()).min())
    return {
        "n": int(len(x)), "expectancy_R": float(r.mean()), "PF": float(pf), "maxDD_R": dd,
        "win_rate": float((r>0).mean()), "sl_rate": float((x.outcome=="SL").mean()),
        "false_start_rate": float(x.false_start.mean()), "avg_MFE_R": float(x.MFE.mean()),
        "avg_MAE_R": float(x.MAE.mean()), "tp1_rate": float(x.tp1_hit.mean()),
        "tp2_rate": float(x.tp2_hit.mean()), "tp3_rate": float(x.tp3_hit.mean()),
        "avg_coverage": float(x.coverage.mean()),
    }


def bootstrap_diff(a: pd.Series, b: pd.Series, n: int = 2000, seed: int = 7) -> dict:
    a = np.asarray(a.dropna(), float); b = np.asarray(b.dropna(), float)
    if len(a)<20 or len(b)<20:
        return {"n_a":len(a),"n_b":len(b),"mean_diff":None,"ci95":None,"p_gt_0":None}
    rng = np.random.default_rng(seed)
    dif = np.empty(n)
    for i in range(n):
        dif[i] = rng.choice(a, len(a), replace=True).mean() - rng.choice(b, len(b), replace=True).mean()
    return {"n_a":len(a),"n_b":len(b),"mean_diff":float(a.mean()-b.mean()),
            "ci95":[float(np.quantile(dif,.025)),float(np.quantile(dif,.975))],
            "p_gt_0":float((dif>0).mean())}


def choose_buffer(results: pd.DataFrame, version: str) -> float:
    z = results[(results.version==version) & (results.split=="TRAIN")]
    best = None
    for b, x in z.groupby("atr_buffer"):
        m = metrics(x)
        if m.get("n",0) < 25:
            continue
        # Training choice favors expectancy/PF while penalizing drawdown lightly.
        score = m["expectancy_R"] + 0.15*min(m["PF"],3.0) + 0.01*m["maxDD_R"]
        cand = (score, m["n"], float(b))
        if best is None or cand > best:
            best = cand
    return 0.10 if best is None else best[2]


def verdict(summary: dict, boot: dict, asset_metrics: dict, regime_metrics: dict) -> Tuple[str,List[str]]:
    reasons=[]
    base=summary.get("BASELINE",{})
    v3=summary.get("V3_FIB",{})
    if base.get("n",0)<30 or v3.get("n",0)<30:
        return "V3 PARTIAL — MORE VALIDATION REQUIRED", ["OOS sample below 30 trades in baseline or V3"]
    exp_ok=v3["expectancy_R"]>base["expectancy_R"]
    pf_ok=v3["PF"]>base["PF"]
    dd_ok=abs(v3["maxDD_R"]) <= abs(base["maxDD_R"])*1.10
    fs_ok=v3["false_start_rate"] <= base["false_start_rate"]+0.02
    reasons += [f"OOS expectancy {'PASS' if exp_ok else 'FAIL'}", f"OOS PF {'PASS' if pf_ok else 'FAIL'}",
                f"MaxDD {'PASS' if dd_ok else 'FAIL'}", f"False-start {'PASS' if fs_ok else 'FAIL'}"]
    stable_assets=0
    for a in ASSETS:
        bm=asset_metrics.get(a,{}).get("BASELINE",{}); vm=asset_metrics.get(a,{}).get("V3_FIB",{})
        if bm.get("n",0)>=10 and vm.get("n",0)>=10 and vm.get("expectancy_R",-99)>=bm.get("expectancy_R",99): stable_assets+=1
    regime_wins=0; regime_tests=0
    for reg,v in regime_metrics.items():
        bm=v.get("BASELINE",{}); vm=v.get("V3_FIB",{})
        if bm.get("n",0)>=8 and vm.get("n",0)>=8:
            regime_tests+=1
            if vm.get("expectancy_R",-99)>=bm.get("expectancy_R",99): regime_wins+=1
    stability_ok = stable_assets>=2 and regime_tests>=2 and regime_wins>=max(1, math.ceil(regime_tests/2))
    boot_ok = boot.get("mean_diff") is not None and boot.get("p_gt_0",0)>=0.75
    if exp_ok and pf_ok and dd_ok and fs_ok and stability_ok and boot_ok:
        return "V3 PASS — WORK MIGRATION CANDIDATE", reasons+["BTC/ETH + multi-regime stability PASS", "bootstrap directional confidence PASS"]
    if exp_ok and pf_ok and dd_ok and fs_ok and (stable_assets>=1):
        return "V3 PARTIAL — MORE VALIDATION REQUIRED", reasons+["Core improvement positive but stability/confidence not strong enough for PASS"]
    return "V3 FAIL — KEEP CURRENT BASELINE", reasons+["Core promotion criteria not met"]


def main():
    raw: Dict[str,pd.DataFrame] = {}
    data: Dict[str,dict] = {}
    failures=[]
    for asset in ASSETS:
        m15,bad=load_15m(asset); failures += bad
        m15=add_session_vwap(enrich(m15))
        h1=enrich(resample(m15,"1h")); h4=enrich(resample(m15,"4h")); d1=enrich(resample(m15,"1d"))
        sv1=m15.session_vwap.resample("1h",label="right",closed="right").last().dropna()
        pdl=prior_day_levels(d1)
        ev=confirmed_pivot_events(h4)
        raw[asset]=m15
        data[asset]={"h1":h1,"h4":h4,"d1":d1,"sv1":sv1,"pdl":pdl,"events":ev}

    all_trades=[]; missed_counts={}
    for asset in ASSETS:
        m15=raw[asset]; h1=data[asset]["h1"]; h4=data[asset]["h4"]; d1=data[asset]["d1"]
        # Baseline
        setups=baseline_setups(asset,h1,h4,d1)
        tr,miss=replay(setups,m15); missed_counts[f"{asset}:BASELINE"]=miss
        if not tr.empty: all_trades.append(tr)
        # V3 no-Fib/Fib across research ATR buffers
        for fib in [False,True]:
            for buf in ATR_BUFFERS:
                setups=v3_setups(asset,h1,h4,d1,data[asset]["pdl"],data[asset]["sv1"],data[asset]["events"],fib,buf,
                                 btc_h1=data["BTCUSDT"]["h1"] if asset!="BTCUSDT" else None)
                tr,miss=replay(setups,m15); missed_counts[f"{asset}:{'V3_FIB' if fib else 'V3_NOFIB'}:{buf}"]=miss
                if not tr.empty: all_trades.append(tr)
    trades=pd.concat(all_trades,ignore_index=True) if all_trades else pd.DataFrame()
    if trades.empty: raise RuntimeError("no trades generated")
    trades["split"]=trades.entry_time.apply(split_name)

    # Select ATR buffer on TRAIN independently for V3_NOFIB and V3_FIB, lock for validation/OOS.
    chosen={v:choose_buffer(trades,v) for v in ["V3_NOFIB","V3_FIB"]}
    locked = trades[(trades.version=="BASELINE") |
                    ((trades.version=="V3_NOFIB")&(trades.atr_buffer==chosen["V3_NOFIB"])) |
                    ((trades.version=="V3_FIB")&(trades.atr_buffer==chosen["V3_FIB"]))].copy()

    metrics_rows=[]
    for split in ["TRAIN","VALIDATION","OOS"]:
        for ver in ["BASELINE","V3_NOFIB","V3_FIB"]:
            m=metrics(locked[(locked.split==split)&(locked.version==ver)])
            metrics_rows.append({"split":split,"version":ver,**m})
    metrics_df=pd.DataFrame(metrics_rows)

    oos=locked[locked.split=="OOS"]
    oos_summary={v:metrics(oos[oos.version==v]) for v in ["BASELINE","V3_NOFIB","V3_FIB"]}
    asset_metrics={a:{v:metrics(oos[(oos.asset==a)&(oos.version==v)]) for v in ["BASELINE","V3_NOFIB","V3_FIB"]} for a in ASSETS}
    regs=sorted(oos.regime.dropna().unique().tolist())
    regime_metrics={g:{v:metrics(oos[(oos.regime==g)&(oos.version==v)]) for v in ["BASELINE","V3_NOFIB","V3_FIB"]} for g in regs}
    direction_metrics={d:{v:metrics(oos[(oos.direction==d)&(oos.version==v)]) for v in ["BASELINE","V3_NOFIB","V3_FIB"]} for d in ["LONG","SHORT"]}
    boot=bootstrap_diff(oos[oos.version=="V3_FIB"].R,oos[oos.version=="BASELINE"].R)
    fib_boot=bootstrap_diff(oos[oos.version=="V3_FIB"].R,oos[oos.version=="V3_NOFIB"].R)
    final_verdict,reasons=verdict(oos_summary,boot,asset_metrics,regime_metrics)

    fib_m=oos_summary.get("V3_FIB",{}); nf_m=oos_summary.get("V3_NOFIB",{})
    if fib_m.get("n",0)>=20 and nf_m.get("n",0)>=20 and fib_m.get("expectancy_R",-99)>nf_m.get("expectancy_R",99) and fib_m.get("PF",0)>=nf_m.get("PF",99)*0.98:
        fib_verdict="KEEP_FIB_PRICE_CONFLUENCE"
    elif fib_m.get("n",0)>=20 and nf_m.get("n",0)>=20 and fib_m.get("expectancy_R",-99)<nf_m.get("expectancy_R",99):
        fib_verdict="REMOVE_OR_REDUCE_FIB"
    else:
        fib_verdict="FIB_NEUTRAL_OR_SAMPLE_INSUFFICIENT"

    locked.to_csv(OUT/"trades_locked.csv",index=False)
    metrics_df.to_csv(OUT/"metrics_by_split.csv",index=False)
    with open(OUT/"summary.json","w") as f:
        json.dump({
            "method":"Point-in-time reproducible execution proxy; not reconstructed historical MASTER signals",
            "window":{"start":str(START),"train":"2024","validation":"2025","oos":"2026-01-01..2026-08-31"},
            "assets":ASSETS,"chosen_atr_buffers":chosen,"download_failures":failures,"missed_counts":missed_counts,
            "oos_summary":oos_summary,"asset_metrics":asset_metrics,"regime_metrics":regime_metrics,
            "direction_metrics":direction_metrics,"bootstrap_v3fib_minus_baseline":boot,
            "bootstrap_fib_minus_nofib":fib_boot,"fib_verdict":fib_verdict,"final_verdict":final_verdict,
            "verdict_reasons":reasons,
            "historical_na":["OI history beyond provider retention","orderbook depth history","price-level liquidation heatmap history","on-chain point-in-time series","ETF/whale/options intraday history"],
            "validated_engine_spec":{
                "engine":"DAILY ENTRY ENGINE V3 + FIB PRICE" if fib_verdict=="KEEP_FIB_PRICE_CONFLUENCE" else "DAILY ENTRY ENGINE V3",
                "frame_stack":"1D context -> 4H regime -> 1H setup -> 15m trigger",
                "fib_price":fib_verdict,"fib_time":"OFF","chosen_atr_buffer":chosen.get("V3_FIB"),
                "hard_gate":["fresh current","locked frame","structural entry/retest","completed trigger","participation","non-chase","structural SL","TP1/2/3","core RR>=3","no severe risk veto","time-validity"]
            }
        },f,indent=2,default=str)

    report=[]
    report.append("# MASTER TRADING · DAILY ENTRY ENGINE V3 SHADOW BACKTEST")
    report.append("")
    report.append(f"**FINAL VERDICT: {final_verdict}**")
    report.append("")
    report.append("## Method")
    report.append("- Point-in-time OHLCV + Binance futures taker-buy quote from 15m bars; 30m/1H/4H/1D derived without look-ahead.")
    report.append("- This is a reproducible execution proxy comparison, not a reconstruction of historical MASTER signals that were never durably stored.")
    report.append("- Train=2024, Validation=2025, OOS=2026-01-01 through 2026-08-31.")
    report.append("- Historical optional fields that cannot be reconstructed are N/A, not zero.")
    report.append("")
    report.append("## Locked ATR buffer selected on TRAIN")
    report.append(f"- V3_NOFIB: {chosen['V3_NOFIB']:.2f} x 1H ATR")
    report.append(f"- V3_FIB: {chosen['V3_FIB']:.2f} x 1H ATR")
    report.append("")
    report.append("## OOS headline")
    report.append("| Version | n | Exp R | PF | MaxDD R | False-start | MFE | MAE | TP2 |")
    report.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for v in ["BASELINE","V3_NOFIB","V3_FIB"]:
        m=oos_summary[v]
        report.append(f"| {v} | {m.get('n',0)} | {m.get('expectancy_R',float('nan')):.3f} | {m.get('PF',float('nan')):.2f} | {m.get('maxDD_R',float('nan')):.2f} | {m.get('false_start_rate',float('nan')):.1%} | {m.get('avg_MFE_R',float('nan')):.2f} | {m.get('avg_MAE_R',float('nan')):.2f} | {m.get('tp2_rate',float('nan')):.1%} |")
    report.append("")
    report.append("## Fib ablation")
    report.append(f"- Verdict: **{fib_verdict}**")
    report.append(f"- Bootstrap V3_FIB - V3_NOFIB: {json.dumps(fib_boot)}")
    report.append("")
    report.append("## Promotion reasons")
    report.extend([f"- {x}" for x in reasons])
    report.append("")
    report.append("## Important limitations")
    report.append("- OI history from Binance public endpoint is retention-limited; not backfilled beyond available point-in-time history.")
    report.append("- Historical orderbook depth, liquidation heatmap, on-chain, ETF/whale/options intraday context are N/A in this test, so this validates the common-coverage V3 core rather than every optional live axis.")
    report.append("- Production UI/canonical remains unchanged by this research run.")
    (OUT/"report.md").write_text("\n".join(report),encoding="utf-8")
    print(final_verdict)
    print(metrics_df.to_string(index=False))


if __name__ == "__main__":
    main()
