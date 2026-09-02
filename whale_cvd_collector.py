#!/usr/bin/env python3
"""
MASTER ALT V2.2.1 - FREE LARGE-TRADE SPOT CVD COLLECTOR

Data:
- Binance Spot public archive: https://data.binance.vision/
- Binance market-data-only API: https://data-api.binance.vision/

What it does:
1) Downloads/streams Binance Spot aggTrades for a rolling 26-week baseline.
2) Buckets each aggregate trade by notional (price * quantity).
3) Classifies aggressive buy/sell using `m` (buyer maker):
      m == False -> aggressive buy
      m == True  -> aggressive sell
4) Stores only DAILY AGGREGATES, never raw trades.
5) Calculates 26W / 13W / 7W / 4W normalized CVD for Large vs Retail.
6) Adds price/volume/RS confirmations using public 1D klines.
7) Produces FINAL20 JSON/CSV summaries and a TOP5 list.

Important:
- This is order-size CVD, NOT wallet identity.
- Large trades are a "large-trade / big-money proxy", not proof of a whale account.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import sys
import tempfile
import time
import zipfile
import hashlib
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from typing import Dict, Iterable, List, Tuple, Optional

import requests

ARCHIVE = "https://data.binance.vision/data/spot"
API = "https://data-api.binance.vision"
UA = "MASTER-ALT-V2.2.1-Free-CVD/2.2.1"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA})

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
STATE_DIR = BASE_DIR / "state"
for p in (DATA_DIR, OUTPUT_DIR, STATE_DIR):
    p.mkdir(parents=True, exist_ok=True)

DAILY_FILE = DATA_DIR / "daily_cvd_bins.csv"
STATE_FILE = STATE_DIR / "collector_state.json"
SUMMARY_JSON = OUTPUT_DIR / "final20_cvd_summary.json"
SUMMARY_CSV = OUTPUT_DIR / "final20_cvd_summary.csv"
TOP5_CSV = OUTPUT_DIR / "large_trade_cvd_top5.csv"
STEALTH_TOP5_CSV = OUTPUT_DIR / "stealth_accumulation_top5.csv"
COVERAGE_CSV = OUTPUT_DIR / "coverage.csv"
SCHEMA_VERSION = "2.2.1"

UTC = timezone.utc


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"last_completed_date": {}, "unsupported": {}, "updated_at": None}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    state["updated_at"] = datetime.now(UTC).isoformat()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def ts_to_date(v: str) -> date:
    x = int(float(v))
    # Binance archive timestamps can be ms; newer archives may use microseconds.
    if x > 10**14:
        x //= 1000
    return datetime.fromtimestamp(x / 1000, tz=UTC).date()


def month_iter(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        if m == 12:
            y += 1; m = 1
        else:
            m += 1


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def bin_name(notional: float, bins: list) -> str:
    for b in bins:
        lo = float(b["min"])
        hi = b["max"]
        if notional >= lo and (hi is None or notional < float(hi)):
            return b["name"]
    return bins[-1]["name"]


def get_json(url: str, params=None, retries=5, timeout=30):
    delay = 1.0
    for i in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep(delay); delay *= 2; continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(delay); delay *= 2


def symbol_exists(symbol: str) -> bool:
    try:
        x = get_json(f"{API}/api/v3/exchangeInfo", {"symbol": symbol})
        return any(s.get("symbol") == symbol and s.get("status") in {"TRADING","BREAK","HALT"} for s in x.get("symbols", []))
    except Exception:
        return False


def download_zip(url: str) -> Optional[Path]:
    """Download archive to a temporary file. Return None on 404."""
    r = SESSION.get(url, stream=True, timeout=60)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    fd, name = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    path = Path(name)
    try:
        with path.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


def rows_from_zip(path: Path) -> Iterable[List[str]]:
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not names:
            return
        with z.open(names[0], "r") as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            reader = csv.reader(text)
            for row in reader:
                if not row:
                    continue
                # Header-safe
                if row[0].lower() in {"agg_trade_id", "aggregate_trade_id", "a"}:
                    continue
                yield row


def aggregate_rows(rows: Iterable[List[str]], symbol: str, bins: list,
                   start: date, end: date, agg: dict):
    """
    Binance archive aggTrades columns:
      0 agg_trade_id
      1 price
      2 quantity
      3 first_trade_id
      4 last_trade_id
      5 transact_time
      6 is_buyer_maker
      7 is_best_match
    """
    for row in rows:
        if len(row) < 7:
            continue
        try:
            px = float(row[1]); qty = float(row[2])
            d = ts_to_date(row[5])
            if d < start or d > end:
                continue
            maker = str(row[6]).strip().lower() in {"true","1"}
            notional = px * qty
            b = bin_name(notional, bins)
            key = (symbol, d.isoformat(), b)
            if maker:
                agg[key]["sell_usdt"] += notional
                agg[key]["sell_count"] += 1
            else:
                agg[key]["buy_usdt"] += notional
                agg[key]["buy_count"] += 1
        except Exception:
            continue


def monthly_url(symbol: str, y: int, m: int) -> str:
    ym = f"{y:04d}-{m:02d}"
    return f"{ARCHIVE}/monthly/aggTrades/{symbol}/{symbol}-aggTrades-{ym}.zip"


def daily_url(symbol: str, d: date) -> str:
    ds = d.isoformat()
    return f"{ARCHIVE}/daily/aggTrades/{symbol}/{symbol}-aggTrades-{ds}.zip"


def bootstrap_symbol(symbol: str, start: date, end: date, bins: list, agg: dict, verbose=True):
    """
    Use monthly archives for completed months; daily archives for current month.
    Monthly rows are filtered to [start, end], so boundary month is safe.
    """
    today = datetime.now(UTC).date()
    current_ym = (today.year, today.month)
    for y, m in month_iter(start, end):
        month_start = date(y, m, 1)
        if m == 12:
            next_month = date(y+1, 1, 1)
        else:
            next_month = date(y, m+1, 1)
        month_end = next_month - timedelta(days=1)
        seg_start, seg_end = max(start, month_start), min(end, month_end)

        if (y, m) != current_ym:
            url = monthly_url(symbol, y, m)
            if verbose: print(f"[{symbol}] monthly {y}-{m:02d}")
            z = download_zip(url)
            if z:
                try:
                    aggregate_rows(rows_from_zip(z), symbol, bins, seg_start, seg_end, agg)
                    continue
                finally:
                    z.unlink(missing_ok=True)

        # fallback to daily (or current month)
        for d in daterange(seg_start, seg_end):
            url = daily_url(symbol, d)
            if verbose: print(f"[{symbol}] daily {d}")
            z = download_zip(url)
            if not z:
                continue
            try:
                aggregate_rows(rows_from_zip(z), symbol, bins, d, d, agg)
            finally:
                z.unlink(missing_ok=True)


def read_daily_existing() -> dict:
    out = defaultdict(lambda: {"buy_usdt":0.0,"sell_usdt":0.0,"buy_count":0,"sell_count":0})
    if not DAILY_FILE.exists():
        return out
    with DAILY_FILE.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            key = (r["symbol"], r["date"], r["bin"])
            out[key] = {
                "buy_usdt": float(r["buy_usdt"]),
                "sell_usdt": float(r["sell_usdt"]),
                "buy_count": int(r["buy_count"]),
                "sell_count": int(r["sell_count"])
            }
    return out


def write_daily(agg: dict):
    fields = ["symbol","date","bin","buy_usdt","sell_usdt","net_cvd_usdt","ncvd","buy_count","sell_count"]
    rows = []
    for (symbol, ds, b), v in sorted(agg.items()):
        buy, sell = v["buy_usdt"], v["sell_usdt"]
        den = buy + sell
        ncvd = ((buy - sell) / den * 100.0) if den else 0.0
        rows.append({
            "symbol": symbol, "date": ds, "bin": b,
            "buy_usdt": round(buy, 4), "sell_usdt": round(sell, 4),
            "net_cvd_usdt": round(buy-sell, 4), "ncvd": round(ncvd, 6),
            "buy_count": v["buy_count"], "sell_count": v["sell_count"]
        })
    with DAILY_FILE.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)


def fetch_klines(symbol: str, days=220) -> List[dict]:
    end_ms = int(datetime.now(UTC).timestamp() * 1000)
    start_ms = int((datetime.now(UTC) - timedelta(days=days+5)).timestamp() * 1000)
    raw = get_json(f"{API}/api/v3/klines", {
        "symbol": symbol, "interval": "1d",
        "startTime": start_ms, "endTime": end_ms, "limit": min(1000, days+10)
    })
    out = []
    for k in raw:
        out.append({
            "date": datetime.fromtimestamp(int(k[0])/1000, UTC).date(),
            "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
            "close": float(k[4]), "volume": float(k[5]),
            "quote_volume": float(k[7])
        })
    return out


def ncvd_for(records: List[dict]) -> Optional[float]:
    buy = sum(r["buy"] for r in records)
    sell = sum(r["sell"] for r in records)
    den = buy + sell
    return ((buy-sell)/den*100.0) if den else None


def clamp(x, lo, hi): return max(lo, min(hi, x))
def map_range(x, lo, hi, out_lo, out_hi):
    if hi == lo: return out_lo
    x = clamp(x, lo, hi)
    return out_lo + (x-lo)/(hi-lo)*(out_hi-out_lo)


def arrow(ncvd: Optional[float], slope: Optional[float]) -> Optional[str]:
    if ncvd is None:
        return None
    s = slope or 0.0
    if ncvd >= 5 and s >= 0: return "↑↑"
    if ncvd >= 1: return "↑"
    if ncvd <= -5 and s <= 0: return "↓↓"
    if ncvd <= -1: return "↓"
    return "→"


def simple_slope(vals: List[float]) -> Optional[float]:
    n = len(vals)
    if n < 2: return None
    xbar = (n-1)/2
    ybar = sum(vals)/n
    den = sum((i-xbar)**2 for i in range(n))
    if not den: return 0.0
    return sum((i-xbar)*(v-ybar) for i,v in enumerate(vals))/den


def build_daily_index(agg: dict):
    idx = defaultdict(lambda: defaultdict(lambda: defaultdict(
        lambda: {"buy":0.0,"sell":0.0,"buy_count":0,"sell_count":0}
    )))
    for (sym, ds, b), v in agg.items():
        idx[sym][ds][b]["buy"] += v["buy_usdt"]
        idx[sym][ds][b]["sell"] += v["sell_usdt"]
        idx[sym][ds][b]["buy_count"] += v.get("buy_count", 0)
        idx[sym][ds][b]["sell_count"] += v.get("sell_count", 0)
    return idx


def window_stats(sym: str, idx: dict, end: date, weeks: int, bins: list, large_bins: list, retail_bins: list):
    """
    DATA QUALITY LOCK:
    - data_coverage_pct = archive/trading-day presence (any trade bin)
    - large_activity_pct = share of observed days that actually had W/MW trades
    These are intentionally separate. A day with no large trade is NOT a missing-data day.
    """
    start = end - timedelta(days=weeks*7-1)
    expected = weeks * 7
    daymap = idx.get(sym, {})
    all_bin_names = [x["name"] for x in bins]

    observed_dates = sorted({
        ds for ds in daymap
        if start <= date.fromisoformat(ds) <= end
        and any(daymap[ds][b]["buy"] + daymap[ds][b]["sell"] > 0 for b in all_bin_names)
    })
    data_coverage = (len(observed_dates) / expected * 100.0) if expected else 0.0

    def collect(binset):
        recs = []
        for ds in observed_dates:
            buy = sum(daymap[ds][b]["buy"] for b in binset)
            sell = sum(daymap[ds][b]["sell"] for b in binset)
            buy_count = sum(daymap[ds][b]["buy_count"] for b in binset)
            sell_count = sum(daymap[ds][b]["sell_count"] for b in binset)
            recs.append({
                "date": date.fromisoformat(ds), "buy": buy, "sell": sell,
                "buy_count": buy_count, "sell_count": sell_count
            })
        return recs

    large = collect(large_bins)
    retail = collect(retail_bins)
    w_only = collect(["W"])
    all_trades = collect(all_bin_names)

    large_active = [r for r in large if (r["buy"] + r["sell"]) > 0]
    large_activity_days = len(large_active)
    large_activity_pct = (large_activity_days / len(observed_dates) * 100.0) if observed_dates else 0.0
    large_trade_count = sum(r["buy_count"] + r["sell_count"] for r in large_active)

    total_large_notional = sum(r["buy"] + r["sell"] for r in large)
    total_all_notional = sum(r["buy"] + r["sell"] for r in all_trades)
    large_notional_share_pct = (total_large_notional / total_all_notional * 100.0) if total_all_notional else None

    # Weekly Large NCVD sequence. Weeks without W/MW trades are omitted, not zero-filled.
    weekly = defaultdict(lambda: {"buy":0.0,"sell":0.0})
    for r in large_active:
        iso = r["date"].isocalendar()
        key = (iso.year, iso.week)
        weekly[key]["buy"] += r["buy"]; weekly[key]["sell"] += r["sell"]

    weekly_vals = []
    for k in sorted(weekly):
        v = weekly[k]
        den = v["buy"] + v["sell"]
        if den:
            weekly_vals.append((v["buy"] - v["sell"]) / den * 100.0)

    positive_week_ratio = None
    if weekly_vals:
        positive_week_ratio = sum(1 for x in weekly_vals if x > 0) / len(weekly_vals) * 100.0

    return {
        "data_coverage_pct": round(data_coverage, 2),
        "large_activity_days": large_activity_days,
        "large_activity_pct": round(large_activity_pct, 2),
        "large_active_weeks": len(weekly_vals),
        "large_trade_count": int(large_trade_count),
        "large_notional_share_pct": None if large_notional_share_pct is None else round(large_notional_share_pct, 4),
        "large_ncvd": ncvd_for(large),
        "retail_ncvd": ncvd_for(retail),
        "w_ncvd": ncvd_for(w_only),
        "large_weekly_slope": simple_slope(weekly_vals),
        "positive_week_ratio": positive_week_ratio,
        "observed_days": len(observed_dates)
    }


def ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    alpha = 2.0 / (period + 1.0)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = alpha * v + (1 - alpha) * e
    return e


def price_features(klines: List[dict], btc_klines: List[dict], asof: date):
    """Use only completed D1 bars at or before the common CVD as-of date."""
    klines = [x for x in klines if x["date"] <= asof]
    btc_klines = [x for x in btc_klines if x["date"] <= asof]
    if len(klines) < 60 or len(btc_klines) < 60:
        return {}

    km = {x["date"]:x for x in klines}
    bm = {x["date"]:x for x in btc_klines}
    common = sorted(set(km) & set(bm))
    if len(common) < 60:
        return {}

    def ret(days, m):
        ds = common[-1]
        base_target = ds - timedelta(days=days)
        candidates = [d for d in common if d <= base_target]
        if not candidates:
            return None
        d0 = candidates[-1]
        return (m[ds]["close"] / m[d0]["close"] - 1) * 100

    coin7, coin4 = ret(7, km), ret(28, km)
    btc7, btc4 = ret(7, bm), ret(28, bm)

    last28 = [km[d] for d in common[-28:]]
    prev28 = [km[d] for d in common[-56:-28]]
    recent_low = min(x["low"] for x in last28)
    prev_low = min(x["low"] for x in prev28) if prev28 else recent_low
    low_def = (recent_low / prev_low - 1) * 100 if prev_low else None

    v4 = sum(x["quote_volume"] for x in last28) / max(1, len(last28))
    vp = sum(x["quote_volume"] for x in prev28) / max(1, len(prev28)) if prev28 else None

    closes = [km[d]["close"] for d in common]
    e20 = ema(closes, 20)
    close = km[common[-1]]["close"]
    ext_ema20 = ((close / e20) - 1) * 100 if e20 else None

    rs7 = (coin7 - btc7) if coin7 is not None and btc7 is not None else None
    rs4 = (coin4 - btc4) if coin4 is not None and btc4 is not None else None
    rs_improving = None
    if rs7 is not None and rs4 is not None:
        rs_improving = (rs7 >= 0) or (rs7 >= rs4 + 2.0)

    # Hard non-chase gate for STEALTH only. Flow score remains independent.
    non_chase = None
    if coin7 is not None and coin4 is not None and ext_ema20 is not None:
        non_chase = (coin7 <= 10.0 and coin4 <= 20.0 and ext_ema20 <= 10.0)

    return {
        "price_7d_pct": coin7,
        "price_4w_pct": coin4,
        "btc_7d_pct": btc7,
        "btc_4w_pct": btc4,
        "rs_btc_7d_pp": rs7,
        "rs_btc_4w_pp": rs4,
        "rs_improving": rs_improving,
        "low_defense_pct": low_def,
        "volume_persistence_ratio": (v4 / vp) if vp else None,
        "price_close_asof": close,
        "ema20_asof": e20,
        "extension_vs_ema20_pct": ext_ema20,
        "non_chase_gate": non_chase
    }


def determine_common_asof(idx: dict, symbols: List[str]) -> Optional[date]:
    """
    SAME COMPLETED-DATE LOCK:
    pick the modal latest archive date across symbols. Scored rows must match exactly.
    This prevents one early/late archive from mixing timestamps.
    """
    latest = []
    for sym in symbols:
        dates = [date.fromisoformat(ds) for ds in idx.get(sym, {}).keys()]
        if dates:
            latest.append(max(dates))
    if not latest:
        return None
    counts = Counter(latest)
    max_count = max(counts.values())
    candidates = [d for d, c in counts.items() if c == max_count]
    return max(candidates)


def large_flow_score(wins: dict, min_cov: float, timestamp_locked: bool) -> Optional[float]:
    """Pure order-flow score. No price, RS or NonChase inputs."""
    if not timestamp_locked:
        return None
    if any(wins[w]["data_coverage_pct"] < min_cov for w in (26,13,7,4)):
        return None

    required = [
        wins[26]["large_ncvd"], wins[13]["large_ncvd"],
        wins[7]["large_ncvd"], wins[4]["large_ncvd"],
        wins[7]["retail_ncvd"], wins[4]["retail_ncvd"]
    ]
    # Missing Large CVD is UNKNOWN, never zero.
    if any(v is None for v in required):
        return None

    n26, n13, n7, n4, r7, r4 = required

    # 1) Large direction/persistence 35
    weighted = 0.15*n26 + 0.20*n13 + 0.25*n7 + 0.40*n4
    s1 = map_range(weighted, -10, 10, 0, 35)

    # 2) Recent acceleration 25
    accel = 0.65*(n4-n7) + 0.35*(n7-n13)
    s2 = map_range(accel, -8, 8, 0, 25)

    # 3) Large vs Retail divergence 25
    div = 0.35*(n7-r7) + 0.65*(n4-r4)
    s3 = map_range(div, -10, 10, 0, 25)

    # 4) Weekly persistence 15
    ratios = [wins[7]["positive_week_ratio"], wins[4]["positive_week_ratio"]]
    ratios = [x for x in ratios if x is not None]
    if ratios:
        s4 = clamp(sum(ratios) / len(ratios) / 100.0 * 15.0, 0, 15)
    else:
        s4 = 0.0

    return round(clamp(s1+s2+s3+s4, 0, 100), 1)


def flow_label(score: Optional[float]) -> Optional[str]:
    if score is None: return None
    if score >= 85: return "STRONG_LARGE_BUY_FLOW"
    if score >= 70: return "LARGE_BUY_FLOW"
    if score >= 55: return "POSITIVE_FLOW"
    if score >= 45: return "NEUTRAL_FLOW"
    return "SELL_FLOW"


def stealth_score(wins: dict, pf: dict, flow_score: Optional[float],
                  min_cov: float, timestamp_locked: bool) -> Optional[float]:
    """
    Stealth is NOT the same as flow.
    Requires price context + retail weakness + non-chase + structure proxies.
    """
    if flow_score is None or not timestamp_locked:
        return None
    if any(wins[w]["data_coverage_pct"] < min_cov for w in (7,4)):
        return None
    if wins[7]["large_ncvd"] is None or wins[4]["large_ncvd"] is None:
        return None
    if wins[7]["retail_ncvd"] is None or wins[4]["retail_ncvd"] is None:
        return None
    if pf.get("non_chase_gate") is None:
        return None

    n7, n4 = wins[7]["large_ncvd"], wins[4]["large_ncvd"]
    r7, r4 = wins[7]["retail_ncvd"], wins[4]["retail_ncvd"]
    p4 = pf.get("price_4w_pct")
    if p4 is None:
        return None

    # Flow foundation 35
    s1 = flow_score / 100.0 * 35.0

    # Price-vs-Large divergence 20: best when price is quiet/weak while Large CVD positive.
    if n4 > 0:
        divergence = n4 - max(p4, 0) * 0.5
        s2 = map_range(divergence, -5, 20, 0, 20)
    else:
        s2 = 0.0

    # Retail weakness / divergence 15
    retail_div = 0.35*(n7-r7) + 0.65*(n4-r4)
    s3 = map_range(retail_div, -5, 12, 0, 15)

    # Non-chase 15 (hard gate reflected in label below)
    s4 = 15.0 if pf.get("non_chase_gate") else 0.0

    # Low defense 5
    ld = pf.get("low_defense_pct")
    s5 = 2.5 if ld is None else (5 if ld >= -2 else 3 if ld >= -5 else 1)

    # Volume persistence 5
    vr = pf.get("volume_persistence_ratio")
    s6 = 2.5 if vr is None else (5 if vr >= 0.9 else 3 if vr >= 0.7 else 1)

    # RS improvement 5
    rsimp = pf.get("rs_improving")
    s7 = 2.5 if rsimp is None else (5 if rsimp else 0)

    return round(clamp(s1+s2+s3+s4+s5+s6+s7, 0, 100), 1)


def stealth_label(score: Optional[float], flow_score: Optional[float], wins: dict, pf: dict) -> Optional[str]:
    if score is None or flow_score is None:
        return None

    # Hard gates: strong enough flow, non-chasing price, and repeated recent Large activity.
    activity_ok = (
        wins[4]["large_active_weeks"] >= 2
        and wins[4]["large_trade_count"] >= 3
        and wins[4]["large_ncvd"] is not None
        and wins[7]["large_ncvd"] is not None
    )
    hard_ok = (
        flow_score >= 70
        and pf.get("non_chase_gate") is True
        and activity_ok
        and wins[4]["large_ncvd"] > 0
    )
    if not hard_ok:
        return "FLOW_ONLY_OR_WAIT"
    if score >= 85:
        return "STRONG_STEALTH_CANDIDATE"
    if score >= 75:
        return "STEALTH_CANDIDATE"
    return "WATCH"


def build_cvd_run_id(results: list, common_asof: date) -> str:
    core = []
    for r in results:
        if not r.get("supported"):
            continue
        core.append([
            r.get("symbol"), r.get("timestamp_locked"),
            r.get("flow_score"), r.get("stealth_score"),
            r.get("windows", {}).get("26", {}).get("large_ncvd"),
            r.get("windows", {}).get("13", {}).get("large_ncvd"),
            r.get("windows", {}).get("7", {}).get("large_ncvd"),
            r.get("windows", {}).get("4", {}).get("large_ncvd")
        ])
    digest = hashlib.sha256(json.dumps(core, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return f"CVD-{common_asof.isoformat()}-{digest}"

def summarize(config: dict, agg: dict, requested_asof: Optional[date]=None):
    idx = build_daily_index(agg)
    common_asof = determine_common_asof(idx, config["symbols"])
    if common_asof is None:
        raise RuntimeError("No common completed CVD date available")

    btc_klines = fetch_klines("BTCUSDT", 240)
    results = []

    for sym in config["symbols"]:
        ticker = config["ticker_labels"].get(sym, sym)
        if not symbol_exists(sym):
            results.append({"symbol":sym, "ticker":ticker, "supported":False})
            continue

        sym_dates = [date.fromisoformat(ds) for ds in idx.get(sym, {}).keys()]
        latest_date = max(sym_dates) if sym_dates else None
        timestamp_locked = (latest_date == common_asof)

        wins = {
            w: window_stats(sym, idx, common_asof, w,
                            config["bins_usdt"], config["large_bins"], config["retail_bins"])
            for w in config["windows_weeks"]
        }

        kl = fetch_klines(sym, 240)
        pf = price_features(kl, btc_klines, common_asof)

        flow = large_flow_score(wins, config["min_cvd_coverage_pct"], timestamp_locked)
        stealth = stealth_score(wins, pf, flow, config["min_cvd_coverage_pct"], timestamp_locked)
        slabel = stealth_label(stealth, flow, wins, pf)

        row = {
            "symbol":sym, "ticker":ticker, "supported":True,
            "cvd_asof_utc":common_asof.isoformat(),
            "symbol_latest_cvd_date":None if latest_date is None else latest_date.isoformat(),
            "timestamp_locked":timestamp_locked,
            "price_close_asof":pf.get("price_close_asof"),
            "flow_score":flow,
            "flow_label":flow_label(flow),
            "stealth_score":stealth,
            "stealth_label":slabel,
            "non_chase_gate":pf.get("non_chase_gate"),
            "price_7d_pct":pf.get("price_7d_pct"),
            "price_4w_pct":pf.get("price_4w_pct"),
            "extension_vs_ema20_pct":pf.get("extension_vs_ema20_pct"),
            "rs_btc_7d_pp":pf.get("rs_btc_7d_pp"),
            "rs_btc_4w_pp":pf.get("rs_btc_4w_pp"),
            "rs_improving":pf.get("rs_improving"),
            "low_defense_pct":pf.get("low_defense_pct"),
            "volume_persistence_ratio":pf.get("volume_persistence_ratio"),
            "windows":{}
        }

        for w in config["windows_weeks"]:
            x = wins[w]
            row["windows"][str(w)] = {
                "data_coverage_pct":x["data_coverage_pct"],
                "large_activity_days":x["large_activity_days"],
                "large_activity_pct":x["large_activity_pct"],
                "large_active_weeks":x["large_active_weeks"],
                "large_trade_count":x["large_trade_count"],
                "large_notional_share_pct":x["large_notional_share_pct"],
                "large_ncvd":None if x["large_ncvd"] is None else round(x["large_ncvd"],3),
                "retail_ncvd":None if x["retail_ncvd"] is None else round(x["retail_ncvd"],3),
                "w_ncvd":None if x["w_ncvd"] is None else round(x["w_ncvd"],3),
                "large_weekly_slope":None if x["large_weekly_slope"] is None else round(x["large_weekly_slope"],3),
                "positive_week_ratio":None if x["positive_week_ratio"] is None else round(x["positive_week_ratio"],2),
                "state":arrow(x["large_ncvd"], x["large_weekly_slope"])
            }
        results.append(row)

    cvd_run_id = build_cvd_run_id(results, common_asof)
    payload = {
        "schema_version":SCHEMA_VERSION,
        "master":"MASTER ALT V2.2.1",
        "patch":"CVD DATA QUALITY PATCH",
        "venue":"Binance Spot",
        "cvd_run_id":cvd_run_id,
        "cvd_asof_utc":common_asof.isoformat(),
        "generated_at_utc":datetime.now(UTC).isoformat(),
        "warning":"Order-size CVD only; not wallet identity. Flow and Stealth are separate.",
        "results":results
    }
    SUMMARY_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    fields = [
        "ticker","symbol","supported","cvd_asof_utc","symbol_latest_cvd_date","timestamp_locked",
        "price_close_asof","flow_score","flow_label","stealth_score","stealth_label","non_chase_gate",
        "price_7d_pct","price_4w_pct","extension_vs_ema20_pct","rs_btc_7d_pp","rs_btc_4w_pp","rs_improving",
        "26W","13W","7W","4W",
        "large_ncvd_26w","large_ncvd_13w","large_ncvd_7w","large_ncvd_4w",
        "retail_ncvd_7w","retail_ncvd_4w",
        "data_cov_26w","data_cov_13w","data_cov_7w","data_cov_4w",
        "large_activity_26w","large_activity_13w","large_activity_7w","large_activity_4w",
        "large_active_weeks_4w","large_trade_count_4w","large_notional_share_4w"
    ]

    flat = []
    for r in results:
        x = {k:r.get(k) for k in fields}
        if r.get("supported"):
            for w in (26,13,7,4):
                wx = r["windows"][str(w)]
                x[f"{w}W"] = wx["state"]
                x[f"large_ncvd_{w}w"] = wx["large_ncvd"]
                x[f"data_cov_{w}w"] = wx["data_coverage_pct"]
                x[f"large_activity_{w}w"] = wx["large_activity_pct"]
            x["retail_ncvd_7w"] = r["windows"]["7"]["retail_ncvd"]
            x["retail_ncvd_4w"] = r["windows"]["4"]["retail_ncvd"]
            x["large_active_weeks_4w"] = r["windows"]["4"]["large_active_weeks"]
            x["large_trade_count_4w"] = r["windows"]["4"]["large_trade_count"]
            x["large_notional_share_4w"] = r["windows"]["4"]["large_notional_share_pct"]
        flat.append(x)

    with SUMMARY_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(flat)

    # Pure order-flow TOP5. Stealth is intentionally NOT used here.
    ranked_flow = [r for r in results if r.get("flow_score") is not None]
    ranked_flow.sort(key=lambda x:x["flow_score"], reverse=True)
    top = ranked_flow[:5]
    topfields = [
        "rank","ticker","flow_score","flow_label","stealth_score","stealth_label","non_chase_gate",
        "26W","13W","7W","4W","large_4w","retail_4w","price_4w_pct","rs_btc_4w_pp",
        "large_activity_4w","large_trade_count_4w"
    ]
    with TOP5_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=topfields); w.writeheader()
        for i,r in enumerate(top,1):
            w.writerow({
                "rank":i,"ticker":r["ticker"],"flow_score":r["flow_score"],"flow_label":r["flow_label"],
                "stealth_score":r["stealth_score"],"stealth_label":r["stealth_label"],
                "non_chase_gate":r["non_chase_gate"],
                "26W":r["windows"]["26"]["state"],"13W":r["windows"]["13"]["state"],
                "7W":r["windows"]["7"]["state"],"4W":r["windows"]["4"]["state"],
                "large_4w":r["windows"]["4"]["large_ncvd"],
                "retail_4w":r["windows"]["4"]["retail_ncvd"],
                "price_4w_pct":r.get("price_4w_pct"),"rs_btc_4w_pp":r.get("rs_btc_4w_pp"),
                "large_activity_4w":r["windows"]["4"]["large_activity_pct"],
                "large_trade_count_4w":r["windows"]["4"]["large_trade_count"]
            })

    # Stealth TOP5 only among hard-gate candidates.
    stealth_ranked = [
        r for r in results
        if r.get("stealth_score") is not None
        and r.get("stealth_label") in {"STEALTH_CANDIDATE","STRONG_STEALTH_CANDIDATE"}
    ]
    stealth_ranked.sort(key=lambda x:x["stealth_score"], reverse=True)
    with STEALTH_TOP5_CSV.open("w", encoding="utf-8", newline="") as f:
        sf = ["rank","ticker","stealth_score","stealth_label","flow_score","non_chase_gate",
              "large_7w","large_4w","retail_4w","price_7d_pct","price_4w_pct",
              "extension_vs_ema20_pct","rs_btc_7d_pp","rs_btc_4w_pp"]
        w = csv.DictWriter(f, fieldnames=sf); w.writeheader()
        for i,r in enumerate(stealth_ranked[:5],1):
            w.writerow({
                "rank":i,"ticker":r["ticker"],"stealth_score":r["stealth_score"],
                "stealth_label":r["stealth_label"],"flow_score":r["flow_score"],
                "non_chase_gate":r["non_chase_gate"],
                "large_7w":r["windows"]["7"]["large_ncvd"],
                "large_4w":r["windows"]["4"]["large_ncvd"],
                "retail_4w":r["windows"]["4"]["retail_ncvd"],
                "price_7d_pct":r.get("price_7d_pct"),"price_4w_pct":r.get("price_4w_pct"),
                "extension_vs_ema20_pct":r.get("extension_vs_ema20_pct"),
                "rs_btc_7d_pp":r.get("rs_btc_7d_pp"),"rs_btc_4w_pp":r.get("rs_btc_4w_pp")
            })

    with COVERAGE_CSV.open("w", encoding="utf-8", newline="") as f:
        fields2 = ["ticker","symbol","supported","timestamp_locked",
                   "data_26W","data_13W","data_7W","data_4W",
                   "large_activity_26W","large_activity_13W","large_activity_7W","large_activity_4W"]
        w = csv.DictWriter(f, fieldnames=fields2); w.writeheader()
        for r in results:
            row = {
                "ticker":r["ticker"],"symbol":r["symbol"],"supported":r["supported"],
                "timestamp_locked":r.get("timestamp_locked")
            }
            if r.get("supported"):
                for ww in (26,13,7,4):
                    row[f"data_{ww}W"] = r["windows"][str(ww)]["data_coverage_pct"]
                    row[f"large_activity_{ww}W"] = r["windows"][str(ww)]["large_activity_pct"]
            w.writerow(row)


def validate_outputs():
    if not SUMMARY_JSON.exists():
        raise RuntimeError("summary json missing")
    payload = json.loads(SUMMARY_JSON.read_text(encoding="utf-8"))
    assert payload.get("schema_version") == SCHEMA_VERSION
    asof = payload.get("cvd_asof_utc")
    assert asof
    for r in payload.get("results", []):
        if not r.get("supported"):
            continue
        # A scored row must be same-date locked.
        if r.get("flow_score") is not None:
            assert r.get("timestamp_locked") is True
            for ww in ("26","13","7","4"):
                assert r["windows"][ww]["large_ncvd"] is not None
        # Stealth candidate must pass NonChase and have flow.
        if r.get("stealth_label") in {"STEALTH_CANDIDATE","STRONG_STEALTH_CANDIDATE"}:
            assert r.get("non_chase_gate") is True
            assert r.get("flow_score") is not None and r["flow_score"] >= 70
    print(f"VALIDATION OK | schema={SCHEMA_VERSION} | asof={asof} | run_id={payload.get('cvd_run_id')}")

def bootstrap(config: dict, lookback_days: int, only: Optional[List[str]]=None):
    end = datetime.now(UTC).date() - timedelta(days=1)  # completed daily archive only
    start = end - timedelta(days=lookback_days-1)
    agg = read_daily_existing()
    state = load_state()
    symbols = only or config["symbols"]

    for sym in symbols:
        print(f"\n=== {sym} ===")
        if not symbol_exists(sym):
            print(f"{sym}: Binance Spot symbol not available; skip.")
            state["unsupported"][sym] = True
            save_state(state)
            continue
        try:
            bootstrap_symbol(sym, start, end, config["bins_usdt"], agg)
            actual_dates = [date.fromisoformat(ds) for (ss, ds, _b) in agg.keys() if ss == sym]
            if actual_dates:
                state["last_completed_date"][sym] = max(actual_dates).isoformat()
            state["unsupported"].pop(sym, None)
            write_daily(agg)
            save_state(state)
        except Exception as e:
            print(f"{sym}: ERROR {e}", file=sys.stderr)
            write_daily(agg); save_state(state)
    summarize(config, agg, end)


def update(config: dict):
    """Fetch newly completed DAILY archives only. Cheap enough for scheduled cloud use."""
    agg = read_daily_existing()
    state = load_state()
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    for sym in config["symbols"]:
        if state.get("unsupported", {}).get(sym):
            if not symbol_exists(sym):
                continue
            state["unsupported"].pop(sym, None)
        if not symbol_exists(sym):
            state["unsupported"][sym] = True
            continue
        last_s = state.get("last_completed_date", {}).get(sym)
        start = (date.fromisoformat(last_s) + timedelta(days=1)) if last_s else yesterday
        if start > yesterday:
            continue
        last_success = date.fromisoformat(last_s) if last_s else None
        for d in daterange(start, yesterday):
            z = download_zip(daily_url(sym, d))
            if not z:
                # Daily archive may not be published yet. Do not advance state past it.
                print(f"[{sym}] archive not ready for {d}; will retry next run")
                break
            try:
                aggregate_rows(rows_from_zip(z), sym, config["bins_usdt"], d, d, agg)
                last_success = d
            finally:
                z.unlink(missing_ok=True)
        if last_success is not None:
            state["last_completed_date"][sym] = last_success.isoformat()
    write_daily(agg); save_state(state)
    summarize(config, agg, yesterday)


def self_test():
    bins = [
        {"name":"R1","min":0,"max":1000},{"name":"R2","min":1000,"max":10000},
        {"name":"M","min":10000,"max":100000},{"name":"W","min":100000,"max":1000000},
        {"name":"MW","min":1000000,"max":None}
    ]
    assert bin_name(999,bins)=="R1"
    assert bin_name(1000,bins)=="R2"
    assert bin_name(100000,bins)=="W"
    assert bin_name(1000000,bins)=="MW"
    assert arrow(6,1)=="↑↑" and arrow(-6,-1)=="↓↓"
    # Missing CVD must stay None; never silently become zero.
    assert ncvd_for([{"buy":0.0,"sell":0.0}]) is None
    print("SELF TEST OK | V2.2.1")


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--config", default=str(BASE_DIR/"final20_config.json"))
    ap.add_argument("--mode", choices=["bootstrap","update","summarize","validate","self-test"], default="update")
    ap.add_argument("--lookback-days", type=int, default=None)
    ap.add_argument("--symbols", nargs="*", default=None, help="Optional subset, e.g. SUIUSDT ONDOUSDT")
    args=ap.parse_args()

    if args.mode=="self-test":
        self_test(); return

    cfg=load_config(Path(args.config))
    if args.mode=="bootstrap":
        bootstrap(cfg, args.lookback_days or cfg["lookback_days"], args.symbols)
    elif args.mode=="update":
        update(cfg)
    elif args.mode=="summarize":
        agg=read_daily_existing()
        summarize(cfg, agg)
    elif args.mode=="validate":
        validate_outputs()


if __name__=="__main__":
    main()
