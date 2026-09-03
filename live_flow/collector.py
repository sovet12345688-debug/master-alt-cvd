#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import requests

UTC = timezone.utc
BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
DATA = BASE / "data"
OUT = BASE / "output"
STATE = BASE / "state"
for p in (DATA, OUT, STATE):
    p.mkdir(parents=True, exist_ok=True)

CONFIG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
FINAL20 = json.loads((ROOT / "final20_config.json").read_text(encoding="utf-8"))
API = CONFIG["api_base"].rstrip("/")
HISTORY = DATA / "hourly_large_flow_bins.csv"
SUMMARY_JSON = OUT / "live_large_flow_summary.json"
SUMMARY_CSV = OUT / "live_large_flow_summary.csv"
STATE_JSON = STATE / "collector_state.json"
SCHEMA = "1.0"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "MASTER-ALT2-Live-Large-Flow/1.0"})

FIELDS = ["hour_utc","symbol","bin","buy_usdt","sell_usdt","buy_count","sell_count"]


def get_json(path: str, params=None):
    delay = 1.0
    last = None
    for i in range(int(CONFIG["request_retries"])):
        try:
            r = SESSION.get(f"{API}{path}", params=params, timeout=int(CONFIG["request_timeout_seconds"]))
            if r.status_code == 429:
                time.sleep(delay); delay *= 2; continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            if i + 1 < int(CONFIG["request_retries"]):
                time.sleep(delay); delay *= 2
    raise last


def completed_hour(now=None):
    now = now or datetime.now(UTC)
    floor = now.replace(minute=0, second=0, microsecond=0)
    return floor - timedelta(hours=1)


def bin_name(notional: float) -> str:
    for b in FINAL20["bins_usdt"]:
        lo = float(b["min"])
        hi = b["max"]
        if notional >= lo and (hi is None or notional < float(hi)):
            return b["name"]
    return FINAL20["bins_usdt"][-1]["name"]


def normalized(buy: float, sell: float):
    den = buy + sell
    if den <= 0:
        return None
    return (buy - sell) / den * 100.0


def exchange_symbols():
    x = get_json("/api/v3/exchangeInfo")
    return {s.get("symbol") for s in x.get("symbols", []) if s.get("status") == "TRADING"}


def fetch_hour(symbol: str, hour: datetime) -> List[dict]:
    start_ms = int(hour.timestamp() * 1000)
    end_ms = int((hour + timedelta(hours=1)).timestamp() * 1000) - 1
    first = get_json("/api/v3/aggTrades", {"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": 1000})
    rows = list(first)
    if not first:
        return rows
    pages = 1
    last_id = int(first[-1]["a"])
    while len(first) == 1000:
        if pages >= int(CONFIG["max_pages_per_symbol"]):
            raise RuntimeError(f"{symbol}: pagination safety limit exceeded")
        page = get_json("/api/v3/aggTrades", {"symbol": symbol, "fromId": last_id + 1, "limit": 1000})
        pages += 1
        if not page:
            break
        in_hour = [r for r in page if start_ms <= int(r["T"]) <= end_ms]
        rows.extend(in_hour)
        last_id = int(page[-1]["a"])
        if int(page[-1]["T"]) > end_ms or len(page) < 1000:
            break
    dedup = {int(r["a"]): r for r in rows if start_ms <= int(r["T"]) <= end_ms}
    return [dedup[k] for k in sorted(dedup)]


def aggregate(symbol: str, hour: datetime, trades: List[dict]):
    agg = {b["name"]: {"buy_usdt":0.0,"sell_usdt":0.0,"buy_count":0,"sell_count":0} for b in FINAL20["bins_usdt"]}
    for r in trades:
        px = float(r["p"]); qty = float(r["q"]); notional = px * qty
        b = bin_name(notional)
        if bool(r["m"]):
            agg[b]["sell_usdt"] += notional; agg[b]["sell_count"] += 1
        else:
            agg[b]["buy_usdt"] += notional; agg[b]["buy_count"] += 1
    h = hour.isoformat().replace("+00:00","Z")
    out = []
    for b, v in agg.items():
        out.append({"hour_utc":h,"symbol":symbol,"bin":b,**v})
    return out


def load_history():
    if not HISTORY.exists(): return []
    with HISTORY.open(encoding="utf-8", newline="") as f:
        rows=[]
        for r in csv.DictReader(f):
            for k in ("buy_usdt","sell_usdt"): r[k]=float(r[k])
            for k in ("buy_count","sell_count"): r[k]=int(r[k])
            rows.append(r)
        return rows


def save_history(rows):
    cutoff = datetime.now(UTC) - timedelta(days=int(CONFIG["retention_days"]))
    keep=[]; seen=set()
    for r in rows:
        dt=datetime.fromisoformat(r["hour_utc"].replace("Z","+00:00"))
        key=(r["hour_utc"],r["symbol"],r["bin"])
        if dt >= cutoff and key not in seen:
            keep.append(r); seen.add(key)
    keep.sort(key=lambda r:(r["hour_utc"],r["symbol"],r["bin"]))
    with HISTORY.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=FIELDS); w.writeheader(); w.writerows(keep)
    return keep


def window_metrics(rows, symbol, end_hour, hours):
    start = end_hour - timedelta(hours=hours-1)
    wanted = {(start + timedelta(hours=i)).isoformat().replace("+00:00","Z") for i in range(hours)}
    sr=[r for r in rows if r["symbol"]==symbol and r["hour_utc"] in wanted]
    observed={r["hour_utc"] for r in sr}
    coverage=len(observed)/hours*100.0
    if CONFIG.get("strict_full_window", True) and len(observed) != hours:
        return {"coverage_pct":coverage,"hours_observed":len(observed),"large_cvd":None,"retail_cvd":None,"large_notional_usdt":None,"large_trade_count":None}
    large=set(FINAL20["large_bins"]); retail=set(FINAL20["retail_bins"])
    lb=ls=rb=rs=0.0; lc=0
    for r in sr:
        if r["bin"] in large:
            lb+=r["buy_usdt"]; ls+=r["sell_usdt"]; lc+=r["buy_count"]+r["sell_count"]
        if r["bin"] in retail:
            rb+=r["buy_usdt"]; rs+=r["sell_usdt"]
    return {"coverage_pct":coverage,"hours_observed":len(observed),"large_cvd":normalized(lb,ls),"retail_cvd":normalized(rb,rs),"large_notional_usdt":lb+ls,"large_trade_count":lc}


def flow_state(m):
    a=m.get("1h",{}).get("large_cvd"); b=m.get("4h",{}).get("large_cvd"); c=m.get("24h",{}).get("large_cvd")
    if a is None: return "BUILDING_HISTORY"
    if b is None: return "LIVE_1H_ONLY"
    if c is None:
        if a>0 and b<=0: return "EARLY_BUY_TURN"
        if a<0 and b>=0: return "EARLY_SELL_TURN"
        return "BUILDING_24H"
    if a>0 and b>0 and c>0 and a>b>c: return "BUY_ACCELERATION"
    if a<0 and b<0 and c<0 and a<b<c: return "SELL_ACCELERATION"
    if a>0 and b<=0: return "EARLY_BUY_TURN"
    if a<0 and b>=0: return "EARLY_SELL_TURN"
    if a>0 and b>0 and c>0: return "BUY_DOMINANT"
    if a<0 and b<0 and c<0: return "SELL_DOMINANT"
    return "MIXED"


def build_summary(rows, target, supported, failures):
    assets=[]
    target_s=target.isoformat().replace("+00:00","Z")
    for s in FINAL20["symbols"]:
        if s not in supported:
            assets.append({"symbol":s,"status":"UNSUPPORTED","1h":None,"4h":None,"24h":None,"flow_state":"N/A"}); continue
        m={}
        for h in CONFIG["windows_hours"]:
            m[f"{h}h"]=window_metrics(rows,s,target,int(h))
        one=m["1h"]; four=m["4h"]; day=m["24h"]
        activity_ratio=None
        if one.get("large_notional_usdt") is not None and day.get("large_notional_usdt") not in (None,0):
            activity_ratio=one["large_notional_usdt"]/(day["large_notional_usdt"]/24.0)
        assets.append({
            "symbol":s,
            "status":"OK" if s not in failures else "ERROR",
            **m,
            "large_cvd_1h_minus_4h": None if one.get("large_cvd") is None or four.get("large_cvd") is None else one["large_cvd"]-four["large_cvd"],
            "large_cvd_4h_minus_24h": None if four.get("large_cvd") is None or day.get("large_cvd") is None else four["large_cvd"]-day["large_cvd"],
            "large_activity_1h_vs_24h_hourly_avg":activity_ratio,
            "flow_state":flow_state(m)
        })
    payload={
        "engine":"ALT2_LIVE_LARGE_FLOW_V1",
        "schema_version":SCHEMA,
        "source":"Binance Spot public aggTrades API",
        "venue":"Binance Spot",
        "target_completed_hour_utc":target_s,
        "generated_at_utc":datetime.now(UTC).isoformat().replace("+00:00","Z"),
        "windows_hours":CONFIG["windows_hours"],
        "note":"Large=W+MW order-size flow proxy. Not wallet identity. Completed hours only. Confirmation/counter-evidence only; never an ENTER signal by itself.",
        "assets":assets
    }
    SUMMARY_JSON.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    cols=["symbol","status","large_cvd_1h","large_cvd_4h","large_cvd_24h","retail_cvd_1h","retail_cvd_4h","retail_cvd_24h","large_activity_1h_vs_24h_hourly_avg","flow_state"]
    with SUMMARY_CSV.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=cols); w.writeheader()
        for a in assets:
            def g(win,key): return None if not isinstance(a.get(win),dict) else a[win].get(key)
            w.writerow({"symbol":a["symbol"],"status":a["status"],"large_cvd_1h":g("1h","large_cvd"),"large_cvd_4h":g("4h","large_cvd"),"large_cvd_24h":g("24h","large_cvd"),"retail_cvd_1h":g("1h","retail_cvd"),"retail_cvd_4h":g("4h","retail_cvd"),"retail_cvd_24h":g("24h","retail_cvd"),"large_activity_1h_vs_24h_hourly_avg":a.get("large_activity_1h_vs_24h_hourly_avg"),"flow_state":a["flow_state"]})
    return payload


def collect():
    target=completed_hour(); supported=exchange_symbols(); history=load_history(); failures={}
    key_target=target.isoformat().replace("+00:00","Z")
    history=[r for r in history if not (r["hour_utc"]==key_target and r["symbol"] in FINAL20["symbols"])]
    ok=0; unsupported=[]
    for s in FINAL20["symbols"]:
        if s not in supported:
            unsupported.append(s); continue
        try:
            trades=fetch_hour(s,target)
            history.extend(aggregate(s,target,trades)); ok+=1
            print(f"{s}: {len(trades)} aggTrades")
        except Exception as e:
            failures[s]=str(e); print(f"ERROR {s}: {e}")
    history=save_history(history)
    payload=build_summary(history,target,supported,failures)
    state={"last_run_utc":datetime.now(UTC).isoformat().replace("+00:00","Z"),"target_completed_hour_utc":key_target,"universe_count":len(FINAL20["symbols"]),"supported_count":len(FINAL20["symbols"])-len(unsupported),"ok_count":ok,"failure_count":len(failures),"unsupported":unsupported,"failures":failures,"history_rows":len(history)}
    STATE_JSON.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(state,ensure_ascii=False,indent=2))
    return payload


def validate():
    if not HISTORY.exists() or not SUMMARY_JSON.exists() or not STATE_JSON.exists(): raise SystemExit("missing output")
    rows=load_history(); seen=set()
    for r in rows:
        k=(r["hour_utc"],r["symbol"],r["bin"])
        if k in seen: raise SystemExit(f"duplicate key {k}")
        seen.add(k)
    p=json.loads(SUMMARY_JSON.read_text(encoding="utf-8")); s=json.loads(STATE_JSON.read_text(encoding="utf-8"))
    if p.get("engine")!="ALT2_LIVE_LARGE_FLOW_V1" or p.get("schema_version")!=SCHEMA: raise SystemExit("schema mismatch")
    if len(p.get("assets",[]))!=len(FINAL20["symbols"]): raise SystemExit("asset count mismatch")
    if s.get("ok_count",0) < max(1,int(s.get("supported_count",0)*0.8)): raise SystemExit("live collection coverage <80%")
    print("VALIDATION PASS")


def self_test():
    assert bin_name(999)=="R1" and bin_name(1000)=="R2" and bin_name(100000)=="W" and bin_name(1000000)=="MW"
    assert round(normalized(75,25),6)==50.0
    assert completed_hour(datetime(2026,9,3,1,55,tzinfo=UTC))==datetime(2026,9,3,0,0,tzinfo=UTC)
    print("SELF TEST PASS")


if __name__=="__main__":
    ap=argparse.ArgumentParser(); ap.add_argument("--mode",choices=["collect","validate","self-test"],default="collect"); a=ap.parse_args()
    if a.mode=="collect": collect()
    elif a.mode=="validate": validate()
    else: self_test()
