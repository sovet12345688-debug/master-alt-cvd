from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

URL = "https://api.bitget.com/api/v3/market/history-candles"
DAY_MS = 24 * 60 * 60 * 1000
MIN_BARS = 120
RECENT_EXCLUSION_DAYS = 120
EVAL_DAYS = 1080
FORWARD_DAYS = 5

GATES = {
    "min_signals": 80,
    "min_hit_rate_pct": 52.0,
    "avg_signed_forward_return_must_be_positive": True,
    "min_holdout_signals": 15,
    "min_holdout_hit_rate_pct": 50.0,
    "holdout_avg_signed_forward_return_must_be_positive": True,
    "min_positive_folds": 3,
}


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_daily(start: datetime, end: datetime) -> list[dict[str, float]]:
    rows: dict[int, dict[str, float]] = {}
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=80), end)
        r = requests.get(URL, params={
            "category": "USDT-FUTURES", "symbol": "BTCUSDT", "interval": "1D",
            "startTime": str(int(cursor.timestamp()*1000)),
            "endTime": str(int(chunk_end.timestamp()*1000)),
            "type": "market", "limit": "100",
        }, timeout=20, headers={"User-Agent":"btc-trend-v26-value-r1/1.0"})
        r.raise_for_status(); p = r.json()
        if str(p.get("code")) != "00000": raise RuntimeError(f"Bitget error: {p!r}")
        for x in p.get("data") or []:
            if not isinstance(x, list) or len(x) < 7: continue
            ts = int(x[0])
            rows[ts] = {"ts": float(ts), "open": float(x[1]), "high": float(x[2]), "low": float(x[3]), "close": float(x[4]), "volume": float(x[6])}
        cursor = chunk_end; time.sleep(0.05)
    out = [rows[k] for k in sorted(rows)]
    if len(out) < MIN_BARS: raise RuntimeError(f"insufficient daily bars: {len(out)}")
    return out


def ema(values: list[float], period: int) -> float:
    a = 2.0/(period+1.0); v = values[0]
    for x in values[1:]: v = a*x + (1-a)*v
    return v


def atr14(rows: list[dict[str,float]]) -> float:
    trs=[]
    for i in range(len(rows)-14, len(rows)):
        r=rows[i]; pc=rows[i-1]["close"]
        trs.append(max(r["high"]-r["low"], abs(r["high"]-pc), abs(r["low"]-pc)))
    return sum(trs)/len(trs)


def rsi(values: list[float], period: int=14) -> float:
    diffs=[values[i]-values[i-1] for i in range(len(values)-period, len(values))]
    gains=sum(max(d,0) for d in diffs)/period
    losses=sum(max(-d,0) for d in diffs)/period
    if losses == 0: return 100.0
    rs=gains/losses
    return 100.0 - 100.0/(1.0+rs)


def weekly_closes(rows: list[dict[str,float]]) -> list[float]:
    out=[]
    bucket=None; last=None
    for r in rows:
        dt=datetime.fromtimestamp(r["ts"]/1000, tz=timezone.utc)
        key=(dt.isocalendar().year, dt.isocalendar().week)
        if bucket is not None and key != bucket and last is not None: out.append(last)
        bucket=key; last=r["close"]
    if last is not None: out.append(last)
    return out


def classify(rows: list[dict[str,float]]) -> dict[str,Any]:
    closes=[r["close"] for r in rows]
    e20=ema(closes[-60:],20); a14=atr14(rows); rd=rsi(closes,14)
    wc=weekly_closes(rows)
    rw=rsi(wc,14) if len(wc)>=15 else None
    dist=(closes[-1]-e20)/a14 if a14>0 else 0.0
    if dist <= -2.0 and rd <= 30 and rw is not None and rw <= 45: state="STRONG_LONG"
    elif dist <= -1.0 and rd <= 40: state="LONG"
    elif dist >= 2.0 and rd >= 70 and rw is not None and rw >= 55: state="STRONG_SHORT"
    elif dist >= 1.0 and rd >= 60: state="SHORT"
    else: state="NEUTRAL"
    return {"state":state,"distance_from_ema20_atr":dist,"rsi_1d":rd,"rsi_1w":rw,"ema20":e20,"atr14":a14,"close":closes[-1],"completed_ts_ms":int(rows[-1]["ts"])}


def summarize(recs:list[dict[str,Any]])->dict[str,Any]:
    n=len(recs); hits=sum(int(r["hit"]) for r in recs)
    avg=sum(r["signed"] for r in recs)/n if n else None
    return {"signals":n,"hits":hits,"hit_rate_pct":round(100*hits/n,2) if n else None,"avg_signed_forward_return_pct":round(100*avg,4) if avg is not None else None}


def build(now: datetime|None=None)->dict[str,Any]:
    now=now or datetime.now(timezone.utc)
    eval_end=now-timedelta(days=RECENT_EXCLUSION_DAYS)
    eval_start=eval_end-timedelta(days=EVAL_DAYS)
    rows=fetch_daily(eval_start-timedelta(days=180), now)
    current=classify(rows)
    recs=[]
    for i in range(MIN_BARS-1, len(rows)-FORWARD_DAYS):
        asof=int(rows[i]["ts"])+DAY_MS
        if not (int(eval_start.timestamp()*1000)<=asof<=int(eval_end.timestamp()*1000)): continue
        c=classify(rows[:i+1]); s=c["state"]
        if s=="NEUTRAL": continue
        fwd=rows[i+FORWARD_DAYS]["close"]/rows[i]["close"]-1.0
        signed=fwd if s in {"LONG","STRONG_LONG"} else -fwd
        recs.append({"asof":asof,"state":s,"signed":signed,"hit":signed>0})
    total=summarize(recs)
    start_ms=int(eval_start.timestamp()*1000); end_ms=int(eval_end.timestamp()*1000); span=end_ms-start_ms
    folds=[]
    for j in range(4):
        lo=start_ms+span*j//4; hi=start_ms+span*(j+1)//4
        sub=[r for r in recs if lo<=r["asof"]<(hi if j<3 else hi+1)]
        folds.append({"fold":j+1,**summarize(sub)})
    hold_start=start_ms+span*3//4; hold=summarize([r for r in recs if r["asof"]>=hold_start])
    positive_folds=sum(1 for f in folds if f["signals"]>0 and (f["avg_signed_forward_return_pct"] or 0)>0)
    checks={
        "signal_count": total["signals"]>=GATES["min_signals"],
        "hit_rate": (total["hit_rate_pct"] or 0)>=GATES["min_hit_rate_pct"],
        "positive_return": (total["avg_signed_forward_return_pct"] or 0)>0,
        "holdout_signal_count": hold["signals"]>=GATES["min_holdout_signals"],
        "holdout_hit_rate": (hold["hit_rate_pct"] or 0)>=GATES["min_holdout_hit_rate_pct"],
        "holdout_positive_return": (hold["avg_signed_forward_return_pct"] or 0)>0,
        "fold_stability": positive_folds>=GATES["min_positive_folds"],
    }
    gate="PASS" if all(checks.values()) else "HOLD"
    feature={
        "availability":"CURRENT" if gate=="PASS" else "N_A_THRESHOLD_UNAPPROVED",
        "state":current["state"] if gate=="PASS" else None,
        "feature_timestamp":iso(datetime.fromtimestamp((current["completed_ts_ms"]+DAY_MS)/1000,tz=timezone.utc)),
        "source_timestamps":{"bitget_1d_completed_open_ms":str(current["completed_ts_ms"])},
        "lineage":{"engine":"BTC_TREND_V26_VALUE_OVEREXTENSION_R1","source":"Bitget BTCUSDT USDT Futures","completed_candle_only":True,"promotion_gate":gate},
        "details":current,
    }
    return {"schema_version":"1.0","engine_id":"BTC_TREND_V26_VALUE_OVEREXTENSION_R1","status":"SHADOW_CANDIDATE","asof_utc":iso(now),"promotion_gate":gate,"checks":checks,"gates":GATES,"oos":{"window_start":iso(eval_start),"window_end":iso(eval_end),"forward_days":FORWARD_DAYS,"total":total,"folds":folds,"holdout":hold,"positive_folds":positive_folds},"feature":feature,"production_approved":False,"official_state_write_allowed":False}


def main()->int:
    p=argparse.ArgumentParser(); p.add_argument("--output",type=Path,required=True); a=p.parse_args()
    try: out=build()
    except Exception as e: out={"status":"VALIDATION_FAIL","error":f"{type(e).__name__}: {e}","promotion_gate":"HOLD","production_approved":False,"official_state_write_allowed":False}
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(out,indent=2)+"\n")
    print(json.dumps(out,indent=2)); return 0 if out.get("status")!="VALIDATION_FAIL" else 2

if __name__=="__main__": raise SystemExit(main())
