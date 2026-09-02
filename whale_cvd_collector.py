#!/usr/bin/env python3
"""
MASTER ALT V2.1 - FREE LARGE-TRADE SPOT CVD COLLECTOR

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
from collections import defaultdict
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from typing import Dict, Iterable, List, Tuple, Optional

import requests

ARCHIVE = "https://data.binance.vision/data/spot"
API = "https://data-api.binance.vision"
UA = "MASTER-ALT-V2.1-Free-CVD/1.0"
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
COVERAGE_CSV = OUTPUT_DIR / "coverage.csv"

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
    idx = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {"buy":0.0,"sell":0.0})))
    for (sym, ds, b), v in agg.items():
        idx[sym][ds][b]["buy"] += v["buy_usdt"]
        idx[sym][ds][b]["sell"] += v["sell_usdt"]
    return idx


def window_stats(sym: str, idx: dict, end: date, weeks: int, bins: list, large_bins: list, retail_bins: list):
    start = end - timedelta(days=weeks*7-1)
    expected = weeks*7
    daymap = idx.get(sym, {})
    observed_dates = sorted({
        ds for ds in daymap
        if start <= date.fromisoformat(ds) <= end
        and any(daymap[ds][b]["buy"] + daymap[ds][b]["sell"] > 0 for b in [x["name"] for x in bins])
    })
    coverage = len(observed_dates)/expected*100.0

    def collect(binset):
        recs = []
        for ds in observed_dates:
            buy = sum(daymap[ds][b]["buy"] for b in binset)
            sell = sum(daymap[ds][b]["sell"] for b in binset)
            recs.append({"date": date.fromisoformat(ds), "buy":buy, "sell":sell})
        return recs

    large = collect(large_bins)
    retail = collect(retail_bins)
    w_only = collect(["W"])

    # Weekly NCVD sequence for slope
    weekly = defaultdict(lambda: {"buy":0.0,"sell":0.0})
    for r in large:
        iso = r["date"].isocalendar()
        key = (iso.year, iso.week)
        weekly[key]["buy"] += r["buy"]; weekly[key]["sell"] += r["sell"]
    weekly_vals = []
    for k in sorted(weekly):
        v = weekly[k]
        den = v["buy"]+v["sell"]
        if den:
            weekly_vals.append((v["buy"]-v["sell"])/den*100.0)

    return {
        "coverage_pct": round(coverage,2),
        "large_ncvd": ncvd_for(large),
        "retail_ncvd": ncvd_for(retail),
        "w_ncvd": ncvd_for(w_only),
        "large_weekly_slope": simple_slope(weekly_vals),
        "observed_days": len(observed_dates)
    }


def price_features(klines: List[dict], btc_klines: List[dict]):
    if len(klines) < 60 or len(btc_klines) < 60:
        return {}
    # align simply by date
    km = {x["date"]:x for x in klines}
    bm = {x["date"]:x for x in btc_klines}
    common = sorted(set(km) & set(bm))
    if len(common) < 60:
        return {}
    def ret(days, m):
        ds = common[-1]
        base_target = ds - timedelta(days=days)
        candidates = [d for d in common if d <= base_target]
        if not candidates: return None
        d0 = candidates[-1]
        return (m[ds]["close"]/m[d0]["close"]-1)*100
    coin4 = ret(28, km); btc4 = ret(28, bm)
    last28 = [km[d] for d in common[-28:]]
    prev28 = [km[d] for d in common[-56:-28]]
    recent_low = min(x["low"] for x in last28)
    prev_low = min(x["low"] for x in prev28) if prev28 else recent_low
    low_def = (recent_low/prev_low-1)*100 if prev_low else 0
    v4 = sum(x["quote_volume"] for x in last28)/max(1,len(last28))
    vp = sum(x["quote_volume"] for x in prev28)/max(1,len(prev28)) if prev28 else v4
    return {
        "price_4w_pct": coin4,
        "btc_4w_pct": btc4,
        "rs_btc_4w_pp": (coin4-btc4) if coin4 is not None and btc4 is not None else None,
        "low_defense_pct": low_def,
        "volume_persistence_ratio": (v4/vp) if vp else None,
        "current": km[common[-1]]["close"]
    }


def cvd_score(wins: dict, pf: dict, min_cov: float) -> Optional[float]:
    if any(wins[w]["coverage_pct"] < min_cov for w in (26,13,7,4)):
        return None
    n26,n13,n7,n4 = [wins[w]["large_ncvd"] or 0 for w in (26,13,7,4)]
    w7,w4 = wins[7]["w_ncvd"] or 0, wins[4]["w_ncvd"] or 0
    r7,r4 = wins[7]["retail_ncvd"] or 0, wins[4]["retail_ncvd"] or 0

    # 1) Large direction/persistence 25
    weighted = 0.20*n26 + 0.25*n13 + 0.25*n7 + 0.30*n4
    s1 = map_range(weighted, -10, 10, 0, 25)

    # 2) W-only flow 15
    s2 = map_range(0.4*w7 + 0.6*w4, -10, 10, 0, 15)

    # 3) Acceleration 15 (nested-window improvement)
    accel = 0.6*(n4-n7) + 0.4*(n7-n13)
    s3 = map_range(accel, -5, 5, 0, 15)

    # 4) Large vs Retail divergence 20
    div = 0.4*(n7-r7) + 0.6*(n4-r4)
    s4 = map_range(div, -10, 10, 0, 20)

    # 5) Price vs Large divergence 10
    p4 = pf.get("price_4w_pct")
    if p4 is None:
        s5 = 5
    elif p4 <= 0 and n4 >= 5:
        s5 = 10
    elif p4 <= 3 and n4 >= 3:
        s5 = 8
    elif n4 > 0:
        s5 = 6
    else:
        s5 = 2

    # 6) Base / low defense 5
    ld = pf.get("low_defense_pct")
    s6 = 2.5 if ld is None else (5 if ld >= -2 else 3 if ld >= -5 else 1)

    # 7) Volume persistence 5
    vr = pf.get("volume_persistence_ratio")
    s7 = 2.5 if vr is None else (5 if vr >= 0.9 else 3 if vr >= 0.7 else 1)

    # 8) RS/BTC 5
    rs = pf.get("rs_btc_4w_pp")
    s8 = 2.5 if rs is None else (5 if rs >= 5 else 3 if rs >= 0 else 1 if rs >= -5 else 0)

    return round(clamp(s1+s2+s3+s4+s5+s6+s7+s8, 0, 100), 1)


def judgement(score: Optional[float]) -> Optional[str]:
    if score is None: return None
    if score >= 85: return "VERY_STRONG_STEALTH_PROXY"
    if score >= 75: return "STEALTH_ACCUMULATION_PROXY"
    if score >= 65: return "LARGE_TRADE_ABSORPTION_CANDIDATE"
    if score >= 50: return "WATCH"
    return "WEAK"


def summarize(config: dict, agg: dict, asof: date):
    idx = build_daily_index(agg)
    btc_klines = fetch_klines("BTCUSDT", 220)
    results = []
    for sym in config["symbols"]:
        if not symbol_exists(sym):
            results.append({"symbol":sym, "ticker":config["ticker_labels"].get(sym,sym), "supported":False})
            continue
        wins = {}
        for w in config["windows_weeks"]:
            wins[w] = window_stats(sym, idx, asof, w, config["bins_usdt"], config["large_bins"], config["retail_bins"])
        kl = fetch_klines(sym, 220)
        pf = price_features(kl, btc_klines)
        score = cvd_score(wins, pf, config["min_cvd_coverage_pct"])
        row = {
            "symbol":sym,
            "ticker":config["ticker_labels"].get(sym,sym),
            "supported":True,
            "asof":asof.isoformat(),
            "current":pf.get("current"),
            "cvd_score":score,
            "judgement":judgement(score),
            "price_4w_pct":pf.get("price_4w_pct"),
            "rs_btc_4w_pp":pf.get("rs_btc_4w_pp"),
            "low_defense_pct":pf.get("low_defense_pct"),
            "volume_persistence_ratio":pf.get("volume_persistence_ratio"),
            "windows":{}
        }
        for w in config["windows_weeks"]:
            x = wins[w]
            row["windows"][str(w)] = {
                "coverage_pct":x["coverage_pct"],
                "large_ncvd":None if x["large_ncvd"] is None else round(x["large_ncvd"],3),
                "retail_ncvd":None if x["retail_ncvd"] is None else round(x["retail_ncvd"],3),
                "w_ncvd":None if x["w_ncvd"] is None else round(x["w_ncvd"],3),
                "large_weekly_slope":None if x["large_weekly_slope"] is None else round(x["large_weekly_slope"],3),
                "state":arrow(x["large_ncvd"], x["large_weekly_slope"])
            }
        results.append(row)

    payload = {
        "master":"MASTER ALT V2.1",
        "patch":"FREE LARGE-TRADE SPOT CVD",
        "venue":"Binance Spot",
        "asof":asof.isoformat(),
        "generated_at":datetime.now(UTC).isoformat(),
        "warning":"Order-size CVD only; not wallet identity.",
        "results":results
    }
    SUMMARY_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    fields = [
        "ticker","symbol","supported","current","cvd_score","judgement",
        "26W","13W","7W","4W",
        "large_ncvd_26w","large_ncvd_13w","large_ncvd_7w","large_ncvd_4w",
        "retail_ncvd_7w","retail_ncvd_4w","price_4w_pct","rs_btc_4w_pp",
        "coverage_26w","coverage_13w","coverage_7w","coverage_4w"
    ]
    flat = []
    for r in results:
        x = {k:r.get(k) for k in fields}
        if r.get("supported"):
            for w in (26,13,7,4):
                wx = r["windows"][str(w)]
                x[f"{w}W"] = wx["state"]
                x[f"large_ncvd_{w}w"] = wx["large_ncvd"]
                x[f"coverage_{w}w"] = wx["coverage_pct"]
            x["retail_ncvd_7w"] = r["windows"]["7"]["retail_ncvd"]
            x["retail_ncvd_4w"] = r["windows"]["4"]["retail_ncvd"]
        flat.append(x)

    with SUMMARY_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(flat)

    ranked = [r for r in results if r.get("cvd_score") is not None]
    ranked.sort(key=lambda x:x["cvd_score"], reverse=True)
    top = ranked[:5]
    topfields = ["rank","ticker","cvd_score","judgement","26W","13W","7W","4W","large_4w","retail_4w","price_4w_pct","rs_btc_4w_pp"]
    with TOP5_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=topfields); w.writeheader()
        for i,r in enumerate(top,1):
            w.writerow({
                "rank":i,"ticker":r["ticker"],"cvd_score":r["cvd_score"],"judgement":r["judgement"],
                "26W":r["windows"]["26"]["state"],"13W":r["windows"]["13"]["state"],
                "7W":r["windows"]["7"]["state"],"4W":r["windows"]["4"]["state"],
                "large_4w":r["windows"]["4"]["large_ncvd"],
                "retail_4w":r["windows"]["4"]["retail_ncvd"],
                "price_4w_pct":r.get("price_4w_pct"),"rs_btc_4w_pp":r.get("rs_btc_4w_pp")
            })

    with COVERAGE_CSV.open("w", encoding="utf-8", newline="") as f:
        fields2=["ticker","symbol","supported","26W","13W","7W","4W"]
        w=csv.DictWriter(f, fieldnames=fields2); w.writeheader()
        for r in results:
            row={"ticker":r["ticker"],"symbol":r["symbol"],"supported":r["supported"]}
            if r.get("supported"):
                for ww in (26,13,7,4): row[f"{ww}W"]=r["windows"][str(ww)]["coverage_pct"]
            w.writerow(row)


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
            state["last_completed_date"][sym] = end.isoformat()
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
    print("SELF TEST OK")


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--config", default=str(BASE_DIR/"final20_config.json"))
    ap.add_argument("--mode", choices=["bootstrap","update","summarize","self-test"], default="update")
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
        asof=datetime.now(UTC).date()-timedelta(days=1)
        summarize(cfg,agg,asof)


if __name__=="__main__":
    main()
