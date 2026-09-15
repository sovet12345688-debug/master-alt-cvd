from __future__ import annotations

import io
import json
import math
import zipfile
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

START = pd.Timestamp("2024-01-01T00:00:00Z")
VALID_START = pd.Timestamp("2025-01-01T00:00:00Z")
OOS_START = pd.Timestamp("2025-07-01T00:00:00Z")
END = pd.Timestamp("2026-09-01T00:00:00Z")
ASSETS = ["BTCUSDT", "ETHUSDT"]
OUT = Path("research/trading_v3_shadow/output")
OUT.mkdir(parents=True, exist_ok=True)
COLS = ["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]
FUT_URL = "https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{ym}.zip"
SPOT_URL = "https://data.binance.vision/data/spot/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{ym}.zip"
INTERVAL_DELTA = {"15m": pd.Timedelta(minutes=15), "1h": pd.Timedelta(hours=1)}
RNG = np.random.default_rng(20260915)
SCORE_FLOORS = [70, 75, 78, 80]
ATR_BUFFERS = [0.00, 0.10, 0.15, 0.20]


def months(start=START, end=END):
    x = start.normalize().replace(day=1)
    while x < end:
        yield x.strftime("%Y-%m")
        x = x + pd.offsets.MonthBegin(1)


def _read_zip(content: bytes, interval: str) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".csv")]
        if not names:
            raise RuntimeError("zip has no csv")
        q = pd.read_csv(zf.open(names[0]), header=None, names=COLS)
    ts = pd.to_numeric(q.open_time, errors="coerce")
    ts = np.where(ts > 1e14, ts / 1000, ts)
    q["open_time"] = pd.to_datetime(ts, unit="ms", utc=True, errors="coerce")
    for c in ["open","high","low","close","volume","quote_volume","taker_buy_quote"]:
        q[c] = pd.to_numeric(q[c], errors="coerce")
    q = q.dropna(subset=["open_time","open","high","low","close","volume","quote_volume"])
    q["time"] = q["open_time"] + INTERVAL_DELTA[interval]
    return q[["time","open_time","open","high","low","close","volume","quote_volume","taker_buy_quote"]]


def load_archive(symbol: str, interval: str, spot: bool = False) -> Tuple[pd.DataFrame, List[str]]:
    url = SPOT_URL if spot else FUT_URL
    frames: List[pd.DataFrame] = []
    failures: List[str] = []
    session = requests.Session()
    session.headers.update({"User-Agent": "money-master-trading-v3-shadow/1.0"})
    for ym in months():
        u = url.format(symbol=symbol, interval=interval, ym=ym)
        try:
            r = session.get(u, timeout=60)
            if r.status_code != 200:
                failures.append(f"{symbol}:{'spot' if spot else 'fut'}:{interval}:{ym}:HTTP{r.status_code}")
                continue
            frames.append(_read_zip(r.content, interval))
        except Exception as e:
            failures.append(f"{symbol}:{'spot' if spot else 'fut'}:{interval}:{ym}:{type(e).__name__}")
    if not frames:
        raise RuntimeError(f"No archive data for {symbol} {interval} spot={spot}")
    d = pd.concat(frames, ignore_index=True).drop_duplicates("time").sort_values("time")
    d = d[(d.time >= START) & (d.time < END)].set_index("time")
    return d, failures


def atr(d: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = d.close.shift(1)
    tr = pd.concat([(d.high-d.low).abs(),(d.high-pc).abs(),(d.low-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100/(1+rs)


def kdj(d: pd.DataFrame, n: int = 9) -> Tuple[pd.Series,pd.Series,pd.Series]:
    ll = d.low.rolling(n).min(); hh = d.high.rolling(n).max()
    rsv = 100*(d.close-ll)/(hh-ll).replace(0,np.nan)
    k = rsv.ewm(alpha=1/3,adjust=False).mean(); dd = k.ewm(alpha=1/3,adjust=False).mean(); j = 3*k-2*dd
    return k,dd,j


def enrich(d: pd.DataFrame) -> pd.DataFrame:
    x = d.copy()
    x["ema20"] = x.close.ewm(span=20,adjust=False).mean(); x["ma50"] = x.close.rolling(50).mean(); x["ma200"] = x.close.rolling(200).mean()
    x["atr"] = atr(x); x["rsi"] = rsi(x.close)
    ema12=x.close.ewm(span=12,adjust=False).mean(); ema26=x.close.ewm(span=26,adjust=False).mean(); macd=ema12-ema26
    x["macd_hist"] = macd-macd.ewm(span=9,adjust=False).mean(); _,_,x["kdj_j"] = kdj(x)
    x["vr"] = x.volume/x.volume.rolling(20).mean(); x["taker_delta"] = 2*x.taker_buy_quote-x.quote_volume
    x["cvd4"] = x.taker_delta.rolling(4).sum(); x["cvd12"] = x.taker_delta.rolling(12).sum()
    x["atr_pct"] = x.atr/x.close; x["atr_pct_rank"] = x.atr_pct.rolling(24*30,min_periods=24*7).rank(pct=True)
    x["ema_slope"] = (x.ema20-x.ema20.shift(3))/x.atr.replace(0,np.nan)
    x["range_hi48"] = x.high.rolling(48).max(); x["range_lo48"] = x.low.rolling(48).min(); x["range_mid48"]=(x.range_hi48+x.range_lo48)/2
    x["hi20"] = x.high.rolling(20).max().shift(1); x["lo20"] = x.low.rolling(20).min().shift(1)
    x["hi8"] = x.high.rolling(8).max().shift(1); x["lo8"] = x.low.rolling(8).min().shift(1)
    session_key=x.open_time.dt.floor("D"); pv=x.quote_volume.fillna(0); vv=x.volume.replace(0,np.nan)
    x["session_vwap"] = pv.groupby(session_key).cumsum()/vv.groupby(session_key).cumsum()
    return x


def resample_completed(h: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg=pd.DataFrame({"open":h.open.resample(rule,label="right",closed="right").first(),"high":h.high.resample(rule,label="right",closed="right").max(),"low":h.low.resample(rule,label="right",closed="right").min(),"close":h.close.resample(rule,label="right",closed="right").last(),"volume":h.volume.resample(rule,label="right",closed="right").sum(),"quote_volume":h.quote_volume.resample(rule,label="right",closed="right").sum(),"taker_buy_quote":h.taker_buy_quote.resample(rule,label="right",closed="right").sum()}).dropna()
    agg["open_time"] = agg.index-pd.to_timedelta(rule)
    return enrich(agg)


def prior_day_levels(h: pd.DataFrame) -> pd.DataFrame:
    d=pd.DataFrame({"pdh_raw":h.high.resample("1D",label="right",closed="right").max(),"pdl_raw":h.low.resample("1D",label="right",closed="right").min(),"pdc_raw":h.close.resample("1D",label="right",closed="right").last(),"pdo_raw":h.open.resample("1D",label="right",closed="right").first()}).dropna().shift(1)
    d.columns=["pdh","pdl","pdc","pdo"]
    return d.reindex(h.index,method="ffill")


def add_spot_alignment(fut1h: pd.DataFrame, spot1h: pd.DataFrame) -> pd.DataFrame:
    s=enrich(spot1h); out=fut1h.copy(); s=s.reindex(out.index,method="ffill")
    out["spot_close"]=s.close; out["spot_cvd4"]=s.cvd4; out["spot_cvd12"]=s.cvd12
    out["basis"]=(out.close-out.spot_close)/out.spot_close.replace(0,np.nan)
    out["basis_z"]=(out.basis-out.basis.rolling(168).mean())/out.basis.rolling(168).std().replace(0,np.nan)
    return out


def confirmed_pivots(h: pd.DataFrame, left: int=2, right: int=2):
    n=len(h); ph=np.full(n,np.nan); pl=np.full(n,np.nan); ph_origin=np.full(n,-1,dtype=int); pl_origin=np.full(n,-1,dtype=int)
    highs=h.high.to_numpy(); lows=h.low.to_numpy()
    for i in range(left,n-right):
        wh=highs[i-left:i+right+1]; wl=lows[i-left:i+right+1]
        if np.isfinite(highs[i]) and highs[i]>=np.nanmax(wh) and np.sum(wh==highs[i])==1:
            c=i+right; ph[c]=highs[i]; ph_origin[c]=i
        if np.isfinite(lows[i]) and lows[i]<=np.nanmin(wl) and np.sum(wl==lows[i])==1:
            c=i+right; pl[c]=lows[i]; pl_origin[c]=i
    return ph,ph_origin,pl,pl_origin


def rolling_vp_levels(h: pd.DataFrame, i: int, window: int=168, bins: int=24) -> Tuple[float,List[float],List[float]]:
    a=max(0,i-window+1); z=h.iloc[a:i+1]
    if len(z)<48: return np.nan,[],[]
    px=((z.high+z.low+z.close)/3).to_numpy(float); w=z.quote_volume.to_numpy(float); lo,hi=np.nanmin(px),np.nanmax(px)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi<=lo: return np.nan,[],[]
    edges=np.linspace(lo,hi,bins+1); hist,_=np.histogram(px,bins=edges,weights=np.nan_to_num(w,nan=0.0)); centers=(edges[:-1]+edges[1:])/2; order=np.argsort(hist)[::-1]
    poc=float(centers[order[0]]) if len(order) else np.nan; hvn=[float(centers[k]) for k in order[:3] if hist[k]>0]; lorder=np.argsort(hist); lvn=[float(centers[k]) for k in lorder[:3] if hist[k]>=0]
    return poc,hvn,lvn


def cluster_levels(levels: List[dict], close: float, atrv: float, direction: str) -> Optional[dict]:
    if not np.isfinite(atrv) or atrv<=0: return None
    lo,hi=(close-2.0*atrv,close+0.20*atrv) if direction=="LONG" else (close-0.20*atrv,close+2.0*atrv)
    cand=[x for x in levels if np.isfinite(x["value"]) and lo<=x["value"]<=hi]
    if len(cand)<2: return None
    best=None; tol=0.35*atrv
    for anchor in cand:
        c=[x for x in cand if abs(x["value"]-anchor["value"])<=tol]; fam={x["family"] for x in c}; raw=sum(x.get("weight",1.0) for x in c); center=float(np.median([x["value"] for x in c])); dist=abs(close-center)/atrv; score=raw+1.5*len(fam)-0.35*dist
        if best is None or score>best[0]: best=(score,c,center,fam)
    if best is None: return None
    _,c,center,fam=best
    if len(fam)<2: return None
    vals=[x["value"] for x in c]; zlo=min(vals)-0.05*atrv; zhi=max(vals)+0.05*atrv
    if zhi-zlo>0.85*atrv: return None
    return {"levels":c,"center":center,"low":zlo,"high":zhi,"families":fam}


def latest_impulse(events: deque, direction: str) -> Optional[Tuple[dict,dict]]:
    ev=list(events)
    if direction=="LONG":
        for j in range(len(ev)-1,-1,-1):
            if ev[j]["type"]!="H": continue
            for k in range(j-1,-1,-1):
                if ev[k]["type"]=="L" and ev[k]["origin"]<ev[j]["origin"]: return ev[k],ev[j]
    else:
        for j in range(len(ev)-1,-1,-1):
            if ev[j]["type"]!="L": continue
            for k in range(j-1,-1,-1):
                if ev[k]["type"]=="H" and ev[k]["origin"]<ev[j]["origin"]: return ev[k],ev[j]
    return None


def fib_levels(pair: Optional[Tuple[dict,dict]], direction: str) -> Tuple[List[float],List[float]]:
    if pair is None: return [],[]
    a,b=pair
    if direction=="LONG":
        low,high=a["price"],b["price"]
        if high<=low: return [],[]
        rng=high-low; retr=[high-rng*r for r in [0.382,0.5,0.618,0.786]]; ext=[low+rng*r for r in [1.272,1.618]]
    else:
        high,low=a["price"],b["price"]
        if high<=low: return [],[]
        rng=high-low; retr=[low+rng*r for r in [0.382,0.5,0.618,0.786]]; ext=[high-rng*r for r in [1.272,1.618]]
    return retr,ext


def avwap_from_anchor(h: pd.DataFrame, cum_q: np.ndarray, cum_v: np.ndarray, anchor: int, i: int) -> float:
    if anchor<0 or anchor>i: return np.nan
    q0=cum_q[anchor-1] if anchor>0 else 0.0; v0=cum_v[anchor-1] if anchor>0 else 0.0; q=cum_q[i]-q0; v=cum_v[i]-v0
    return float(q/v) if v>0 else np.nan


def regime_label(r4: pd.Series) -> str:
    if any(pd.isna(r4.get(k,np.nan)) for k in ["ema20","ma50","atr","ema_slope"]): return "UNKNOWN"
    sep=abs(r4.ema20-r4.ma50)/r4.atr if r4.atr>0 else 0
    if sep>=0.45 and abs(r4.ema_slope)>=0.12: return "TREND_UP" if r4.ema20>r4.ma50 else "TREND_DOWN"
    if sep<=0.30 and abs(r4.ema_slope)<=0.12: return "RANGE"
    return "TRANSITION"


def _asof_row(df: pd.DataFrame, t: pd.Timestamp) -> Optional[pd.Series]:
    pos=df.index.searchsorted(t,side="right")-1
    return None if pos<0 else df.iloc[pos]


def score_pre(h: pd.DataFrame, i: int, direction: str, r4: pd.Series, r1d: pd.Series, zone: dict, fib_on: bool, struct_strength: float, repeated: int) -> Tuple[float,float,dict]:
    r=h.iloc[i]; raw=0.0; avail=0.0; parts={}; reg=regime_label(r4); s=0.0; avail_reg=9.0
    if direction=="LONG":
        s+=5 if reg=="TREND_UP" else 3 if reg in ["RANGE","TRANSITION"] else 0; s+=4 if r.close>=r.ma50 and r.ema_slope>=-0.05 else 2 if r.close>=r.ema20 else 0
        if r1d is not None and pd.notna(r1d.get("ma50",np.nan)): s+=3 if r1d.close>=r1d.ma50 else 0; avail_reg+=3
    else:
        s+=5 if reg=="TREND_DOWN" else 3 if reg in ["RANGE","TRANSITION"] else 0; s+=4 if r.close<=r.ma50 and r.ema_slope<=0.05 else 2 if r.close<=r.ema20 else 0
        if r1d is not None and pd.notna(r1d.get("ma50",np.nan)): s+=3 if r1d.close<=r1d.ma50 else 0; avail_reg+=3
    raw+=s; avail+=avail_reg; parts["regime"]=s
    s=min(8.0,max(0.0,struct_strength))+min(5.0,repeated*1.25); fam=zone["families"]; mtf_fam=len(fam & {"structure","mtf","day","vwap","vp","fib"}); s+=min(3.0,max(0,mtf_fam-1)*0.75); s+=3.0 if ("vwap" in fam or "vp" in fam) else 0.0; s+=2.0 if "day" in fam else 0.0
    if fib_on: s+=1.0 if "fib" in fam else 0.0; avail+=22
    else: avail+=21
    raw+=s; parts["structure_location"]=s
    s=0.0; vr=r.vr if pd.notna(r.vr) else np.nan
    if pd.notna(vr): s+=5.0 if vr>=1.2 else 3.0 if vr>=0.9 else 1.0
    avail_part=5.0
    if pd.notna(r.cvd4) and pd.notna(r.cvd12):
        align=(r.cvd4>0 and r.cvd12>0) if direction=="LONG" else (r.cvd4<0 and r.cvd12<0); partial=(r.cvd4>0) if direction=="LONG" else (r.cvd4<0); s+=5.0 if align else 2.5 if partial else 0.0; avail_part+=5.0
    if pd.notna(r.spot_cvd4) and pd.notna(r.cvd4):
        align=(r.spot_cvd4>0 and r.cvd4>0) if direction=="LONG" else (r.spot_cvd4<0 and r.cvd4<0); s+=2.0 if align else 0.5; avail_part+=2.0
    raw+=s; avail+=avail_part; parts["participation_flow"]=s
    s=0.0
    if pd.notna(r.basis_z):
        abz=abs(float(r.basis_z)); s+=2.0 if abz<=1.0 else 1.0 if abz<=2.0 else 0.0; raw+=s; avail+=2.0
    parts["derivatives_liquidity"]=s
    s=0.0
    if pd.notna(r.atr_pct_rank):
        p=float(r.atr_pct_rank); s+=3.0 if 0.15<=p<=0.85 else 1.5 if 0.05<=p<=0.95 else 0.5
    dist=abs(r.close-r.session_vwap)/r.atr if pd.notna(r.session_vwap) and r.atr>0 else np.nan
    if pd.notna(dist): s+=2.0 if dist<=1.0 else 1.0 if dist<=1.5 else 0.0
    zw=(zone["high"]-zone["low"])/r.atr if r.atr>0 else np.inf; s+=2.0 if zw<=0.45 else 1.0 if zw<=0.75 else 0.0
    raw+=s; avail+=7; parts["vol_nonchase"]=s
    s=0.0; mom=0
    if direction=="LONG": mom+=int(pd.notna(r.rsi) and 35<=r.rsi<=62); mom+=int(pd.notna(r.kdj_j) and r.kdj_j<65); macd_ok=pd.notna(r.macd_hist) and r.macd_hist>-0.001*r.close
    else: mom+=int(pd.notna(r.rsi) and 38<=r.rsi<=65); mom+=int(pd.notna(r.kdj_j) and r.kdj_j>35); macd_ok=pd.notna(r.macd_hist) and r.macd_hist<0.001*r.close
    mom+=int(macd_ok); s+=min(2.0,mom*(2.0/3.0)); wave_ok=(r.ema_slope>0 if direction=="LONG" else r.ema_slope<0); s+=1.0 if wave_ok else 0.0; raw+=s; avail+=3.0; parts["secondary"]=s
    score=70.0*raw/avail if avail>0 else 0.0; coverage=avail/70.0
    return float(min(score,70.0)),float(coverage),parts


def score_reaction(m: pd.DataFrame, trig_i: int, next_i: int, delay: int, direction: str, spot_perp_ok: Optional[bool], basis_ok: Optional[bool]) -> Tuple[float,float,dict]:
    r=m.iloc[trig_i]; n=m.iloc[next_i]; raw=0.0; avail=0.0; parts={}; rrng=max(r.high-r.low,1e-12)
    if direction=="LONG": wick=max(0.0,min(r.open,r.close)-r.low)/rrng; close_loc=(r.close-r.low)/rrng
    else: wick=max(0.0,r.high-max(r.open,r.close))/rrng; close_loc=(r.high-r.close)/rrng
    s=6.0+(2.0 if wick>=0.25 and close_loc>=0.60 else 1.0); hold=(n.low>=r.low) if direction=="LONG" else (n.high<=r.high); s+=2.0 if hold else 0.0; raw+=s; avail+=10; parts["candle"]=s
    s=0.0
    if pd.notna(r.vr): s+=4.0 if r.vr>=1.2 else 2.5 if r.vr>=0.9 else 1.0
    avail_part=4.0
    if pd.notna(r.cvd4): ok=r.cvd4>0 if direction=="LONG" else r.cvd4<0; s+=3.0 if ok else 0.0; avail_part+=3.0
    raw+=s; avail+=avail_part; parts["trigger_participation"]=s
    s=0.0
    if spot_perp_ok is not None: s+=1.0 if spot_perp_ok else 0.0; avail+=1.0
    if basis_ok is not None: s+=1.0 if basis_ok else 0.0; avail+=1.0
    raw+=s; parts["microstructure"]=s
    s=2.0 if delay<=2 else 1.0 if delay<=4 else 0.0; s+=1.0 if delay<=4 else 0.0; s+=2.0; raw+=s; avail+=5.0; parts["time"]=s
    score=30.0*raw/avail if avail>0 else 0.0; coverage=avail/30.0
    return float(min(score,30.0)),float(coverage),parts


def count_reactions(h: pd.DataFrame, i: int, level: float, atrv: float, direction: str) -> int:
    a=max(0,i-72); z=h.iloc[a:i]
    if z.empty or atrv<=0: return 0
    tol=0.20*atrv
    return int(((z.low-level).abs()<=tol).sum()) if direction=="LONG" else int(((z.high-level).abs()<=tol).sum())


@dataclass
class Setup:
    asset: str; variant: str; signal_time: pd.Timestamp; direction: str; regime: str; zone_low: float; zone_high: float; entry_ref: float; stop: float; tp1: float; tp2: float; tp3: float; pre_score: float; pre_coverage: float; atr: float; metadata: dict


def target_candidates(direction: str, entry: float, risk: float, recent_events: deque, pd_levels: pd.Series, fib_ext: List[float], vp_hvn: List[float], r4: pd.Series) -> List[Tuple[float,str]]:
    out=[]
    for e in recent_events:
        if direction=="LONG" and e["type"]=="H" and e["price"]>entry: out.append((float(e["price"]),"swing"))
        if direction=="SHORT" and e["type"]=="L" and e["price"]<entry: out.append((float(e["price"]),"swing"))
    for nm in ["pdh","pdl","pdc"]:
        v=pd_levels.get(nm,np.nan)
        if pd.notna(v):
            if direction=="LONG" and v>entry: out.append((float(v),"day"))
            if direction=="SHORT" and v<entry: out.append((float(v),"day"))
    for v in vp_hvn:
        if direction=="LONG" and v>entry: out.append((float(v),"vp"))
        if direction=="SHORT" and v<entry: out.append((float(v),"vp"))
    for v in fib_ext:
        if direction=="LONG" and v>entry: out.append((float(v),"fib_ext"))
        if direction=="SHORT" and v<entry: out.append((float(v),"fib_ext"))
    for nm in ["hi20","lo20"]:
        v=r4.get(nm,np.nan)
        if pd.notna(v):
            if direction=="LONG" and v>entry: out.append((float(v),"4h_structure"))
            if direction=="SHORT" and v<entry: out.append((float(v),"4h_structure"))
    out2=[]
    for px,src in sorted(out,key=lambda x:x[0],reverse=(direction=="SHORT")):
        if not out2 or abs(px-out2[-1][0])>0.05*risk: out2.append((px,src))
    return out2


def choose_targets(direction: str, entry: float, stop: float, cands: List[Tuple[float,str]]) -> Optional[Tuple[float,float,float,dict]]:
    risk=entry-stop if direction=="LONG" else stop-entry
    if risk<=0: return None
    c=sorted([(p,s,(p-entry)/risk) for p,s in cands if p>entry],key=lambda x:x[0]) if direction=="LONG" else sorted([(p,s,(entry-p)/risk) for p,s in cands if p<entry],key=lambda x:x[0],reverse=True)
    if not c: return None
    tp1c=[x for x in c if x[2]>=0.8]; tp2c=[x for x in c if x[2]>=3.0 and x[1]!="fib_ext"]
    if not tp1c or not tp2c: return None
    tp1=tp1c[0]; tp2=tp2c[0]; tp3c=[x for x in c if x[2]>=max(tp2[2]+0.5,4.0)]; tp3=tp3c[0] if tp3c else tp2
    return float(tp1[0]),float(tp2[0]),float(tp3[0]),{"tp1_src":tp1[1],"tp2_src":tp2[1],"tp3_src":tp3[1],"tp1_R":tp1[2],"tp2_R":tp2[2],"tp3_R":tp3[2]}


def build_v3_setups(asset: str, h: pd.DataFrame, h4: pd.DataFrame, d1: pd.DataFrame, fib_on: bool, atr_buffer: float) -> List[Setup]:
    ph,ph_origin,pl,pl_origin=confirmed_pivots(h); cum_q=np.nancumsum(h.quote_volume.fillna(0).to_numpy(float)); cum_v=np.nancumsum(h.volume.fillna(0).to_numpy(float)); pdlev=prior_day_levels(h); events=deque(maxlen=30); setups=[]; last_signal=-999; last_high=None; last_low=None; last_high_origin=-1; last_low_origin=-1
    for i in range(len(h)):
        if np.isfinite(ph[i]): e={"type":"H","price":float(ph[i]),"origin":int(ph_origin[i]),"confirmed":i}; events.append(e); last_high=e["price"]; last_high_origin=e["origin"]
        if np.isfinite(pl[i]): e={"type":"L","price":float(pl[i]),"origin":int(pl_origin[i]),"confirmed":i}; events.append(e); last_low=e["price"]; last_low_origin=e["origin"]
        if i<250 or i-last_signal<6: continue
        r=h.iloc[i]
        if any(pd.isna(r.get(k,np.nan)) for k in ["atr","ema20","ma50","rsi","session_vwap"]) or r.atr<=0: continue
        t=h.index[i]; r4=_asof_row(h4,t); r1d=_asof_row(d1,t)
        if r4 is None or r1d is None: continue
        reg=regime_label(r4); direction=None; struct_strength=4.0
        if reg=="TREND_UP" and r.close>=r.ma50*0.995: direction="LONG"; struct_strength=6.5
        elif reg=="TREND_DOWN" and r.close<=r.ma50*1.005: direction="SHORT"; struct_strength=6.5
        elif reg=="RANGE":
            if pd.notna(r.range_mid48) and r.close<=r.range_mid48-0.60*r.atr and r.rsi<=48: direction="LONG"; struct_strength=5.0
            elif pd.notna(r.range_mid48) and r.close>=r.range_mid48+0.60*r.atr and r.rsi>=52: direction="SHORT"; struct_strength=5.0
        else:
            if last_high is not None and r.close>last_high+0.10*r.atr: direction="LONG"; struct_strength=7.0
            elif last_low is not None and r.close<last_low-0.10*r.atr: direction="SHORT"; struct_strength=7.0
        if direction is None: continue
        pair=latest_impulse(events,direction); retr,ext=fib_levels(pair,direction); poc,hvn,_=rolling_vp_levels(h,i); levels=[]
        def add(name,value,family,weight=1.0):
            if value is not None and np.isfinite(value): levels.append({"name":name,"value":float(value),"family":family,"weight":float(weight)})
        if direction=="LONG":
            add("last_swing_low",last_low,"structure",2.5)
            if last_high is not None and r.close>last_high: add("bos_retest",last_high,"structure",3.0)
        else:
            add("last_swing_high",last_high,"structure",2.5)
            if last_low is not None and r.close<last_low: add("bos_retest",last_low,"structure",3.0)
        add("1h_ema20",r.ema20,"mtf",1.2); add("1h_ma50",r.ma50,"mtf",1.0); add("4h_ema20",r4.ema20,"mtf",1.5); add("4h_ma50",r4.ma50,"mtf",1.2)
        for nm in ["pdh","pdl","pdc","pdo"]: add(nm,pdlev.iloc[i].get(nm,np.nan),"day",1.2)
        add("session_vwap",r.session_vwap,"vwap",1.5); anchor=last_low_origin if direction=="LONG" else last_high_origin; add("avwap_swing",avwap_from_anchor(h,cum_q,cum_v,anchor,i),"vwap",1.7); add("vp_poc_7d",poc,"vp",1.4)
        if fib_on:
            for j,v in enumerate(retr): add(f"fib_{[382,500,618,786][j]}",v,"fib",0.8)
        zone=cluster_levels(levels,float(r.close),float(r.atr),direction)
        if zone is None: continue
        entry=zone["center"]
        if direction=="LONG":
            if last_low is None or last_low>=entry: continue
            stop=min(float(last_low),zone["low"]-0.60*r.atr)-atr_buffer*r.atr; risk=entry-stop
        else:
            if last_high is None or last_high<=entry: continue
            stop=max(float(last_high),zone["high"]+0.60*r.atr)+atr_buffer*r.atr; risk=stop-entry
        if risk<=0 or risk<0.35*r.atr or risk>2.75*r.atr: continue
        cands=target_candidates(direction,entry,risk,events,pdlev.iloc[i],ext if fib_on else [],hvn,r4); targets=choose_targets(direction,entry,stop,cands)
        if targets is None: continue
        tp1,tp2,tp3,tmeta=targets; repeated=count_reactions(h,i,entry,float(r.atr),direction); pre_score,pre_cov,parts=score_pre(h,i,direction,r4,r1d,zone,fib_on,struct_strength,repeated)
        if pre_cov<0.60: continue
        setups.append(Setup(asset,"V3+FIB" if fib_on else "V3-FIB",t,direction,reg,float(zone["low"]),float(zone["high"]),float(entry),float(stop),tp1,tp2,tp3,pre_score,pre_cov,float(r.atr),{"families":sorted(zone["families"]),"levels":zone["levels"],"score_parts":parts,"target_meta":tmeta,"atr_buffer":atr_buffer})); last_signal=i
    return setups


def build_baseline_setups(asset: str, h: pd.DataFrame) -> List[Setup]:
    out=[]; last=-999
    for i,(t,r) in enumerate(h.iterrows()):
        if i<220 or i-last<6: continue
        if any(pd.isna(r.get(k,np.nan)) for k in ["ema20","ma50","atr","lo8","hi8"]) or r.atr<=0: continue
        L=r.ema20>r.ma50 and r.ema_slope>0 and r.close>r.ema20 and 0<=(r.close-r.ema20)/r.atr<=1.5; S=r.ema20<r.ma50 and r.ema_slope<0 and r.close<r.ema20 and 0<=(r.ema20-r.close)/r.atr<=1.5
        if not(L or S): continue
        direction="LONG" if L else "SHORT"; entry=float(r.ema20)
        if direction=="LONG": stop=min(float(r.lo8),entry-1.05*r.atr)-0.05*r.atr; risk=entry-stop; tp1,tp2,tp3=entry+risk,entry+2*risk,entry+3*risk
        else: stop=max(float(r.hi8),entry+1.05*r.atr)+0.05*r.atr; risk=stop-entry; tp1,tp2,tp3=entry-risk,entry-2*risk,entry-3*risk
        if risk<=0: continue
        out.append(Setup(asset,"CURRENT_PROXY",t,direction,"BASELINE_TREND",entry-0.10*r.atr,entry+0.10*r.atr,entry,float(stop),float(tp1),float(tp2),float(tp3),0.0,1.0,float(r.atr),{"proxy":"time_validity_v2_like"})); last=i
    return out


def evaluate_setups(setups: List[Setup], h: pd.DataFrame, m: pd.DataFrame, score_floor: Optional[float]=None) -> Tuple[pd.DataFrame,pd.DataFrame]:
    trades=[]; misses=[]; h_idx=h.index; m_idx=m.index
    for s in setups:
        a=m_idx.searchsorted(s.signal_time,side="left"); b=m_idx.searchsorted(s.signal_time+pd.Timedelta(hours=24),side="right")
        if a>=len(m) or b<=a: continue
        z=m.iloc[a:b]; hits=np.where((z.low.to_numpy()<=s.zone_high)&(z.high.to_numpy()>=s.zone_low))[0]
        if len(hits)==0: continue
        touch_pos=a+int(hits[0]); trig_pos=None; next_pos=None; delay=None; max_search=min(len(m)-2,touch_pos+8)
        for k in range(touch_pos,max_search+1):
            r=m.iloc[k]; n=m.iloc[k+1]
            if s.direction=="LONG" and r.low<=s.stop: break
            if s.direction=="SHORT" and r.high>=s.stop: break
            rrng=max(r.high-r.low,1e-12)
            if s.direction=="LONG": lower_wick=max(0.0,min(r.open,r.close)-r.low)/rrng; ok=r.close>r.open and r.close>=s.zone_low and r.low<=s.zone_high+0.10*(s.entry_ref-s.stop) and lower_wick>=0.15 and n.low>=r.low
            else: upper_wick=max(0.0,r.high-max(r.open,r.close))/rrng; ok=r.close<r.open and r.close<=s.zone_high and r.high>=s.zone_low-0.10*(s.stop-s.entry_ref) and upper_wick>=0.15 and n.high<=r.high
            if ok: trig_pos=k; next_pos=k+1; delay=k-touch_pos; break
        if trig_pos is None: continue
        ht=m.index[trig_pos]; hp=h_idx.searchsorted(ht,side="right")-1; spot_perp_ok=None; basis_ok=None
        if hp>=0:
            hr=h.iloc[hp]
            if pd.notna(hr.spot_cvd4) and pd.notna(hr.cvd4): spot_perp_ok=(hr.spot_cvd4>0 and hr.cvd4>0) if s.direction=="LONG" else (hr.spot_cvd4<0 and hr.cvd4<0)
            if pd.notna(hr.basis_z): basis_ok=abs(float(hr.basis_z))<=2.0
        react_score,react_cov,react_parts=score_reaction(m,trig_pos,next_pos,int(delay),s.direction,spot_perp_ok,basis_ok); total_score=s.pre_score+react_score if s.variant.startswith("V3") else 0.0; total_cov=(70*s.pre_coverage+30*react_cov)/100 if s.variant.startswith("V3") else 1.0
        retest=None; retest_end=min(len(m)-1,next_pos+4)
        for k in range(next_pos,retest_end+1):
            r=m.iloc[k]
            if s.direction=="LONG":
                if r.low<=s.stop: break
                if r.low<=s.zone_high and r.close>=s.zone_low: retest=k; break
            else:
                if r.high>=s.stop: break
                if r.high>=s.zone_low and r.close<=s.zone_high: retest=k; break
        if retest is None:
            risk_ref=s.entry_ref-s.stop if s.direction=="LONG" else s.stop-s.entry_ref; zz=m.iloc[next_pos:min(len(m),next_pos+96)]
            if risk_ref>0 and not zz.empty:
                if s.direction=="LONG": run=(zz.high.max()-s.entry_ref)/risk_ref; bad=zz.low.min()<=s.stop
                else: run=(s.entry_ref-zz.low.min())/risk_ref; bad=zz.high.max()>=s.stop
                if run>=2 and not bad: misses.append({"asset":s.asset,"variant":s.variant,"signal_time":s.signal_time,"direction":s.direction,"regime":s.regime,"missed_R":float(run)})
            continue
        rr=m.iloc[retest]; entry=float(np.clip(rr.close,s.zone_low,s.zone_high)); risk=entry-s.stop if s.direction=="LONG" else s.stop-entry
        if risk<=0 or abs(entry-s.entry_ref)>0.40*s.atr: continue
        tp2_rr=(s.tp2-entry)/risk if s.direction=="LONG" else (entry-s.tp2)/risk
        if tp2_rr<3.0: continue
        if score_floor is not None and s.variant.startswith("V3") and total_score<score_floor: continue
        path=m.iloc[retest:min(len(m),retest+97)]
        if path.empty: continue
        if s.direction=="LONG": adverse=(entry-path.low.to_numpy())/risk; favorable=(path.high.to_numpy()-entry)/risk; close_r=(float(path.close.iloc[-1])-entry)/risk
        else: adverse=(path.high.to_numpy()-entry)/risk; favorable=(entry-path.low.to_numpy())/risk; close_r=(entry-float(path.close.iloc[-1]))/risk
        stop_hits=np.where(adverse>=1.0)[0]; h1=np.where(favorable>=1.0)[0]; h2=np.where(favorable>=2.0)[0]; h3=np.where(favorable>=3.0)[0]; st=int(stop_hits[0]) if len(stop_hits) else None; t3=int(h3[0]) if len(h3) else None
        if t3 is not None and (st is None or t3<st): R=3.0; label="TP3"
        elif st is not None and (t3 is None or st<=t3): R=-1.0; label="SL"
        else: R=float(np.clip(close_r,-1.0,3.0)); label="TIME_EXIT"
        mfe=float(np.nanmax(favorable)); mae=float(np.nanmax(adverse)); false_start=bool(st is not None and len(np.where(favorable[:st+1]>=0.5)[0])==0); entry_eff=100*mfe/(mfe+mae) if mfe+mae>0 else 50.0
        trades.append({"asset":s.asset,"variant":s.variant,"signal_time":s.signal_time,"entry_time":m.index[retest],"direction":s.direction,"regime":s.regime,"entry":entry,"stop":s.stop,"tp1_plan":s.tp1,"tp2_plan":s.tp2,"tp3_plan":s.tp3,"tp2_plan_R":tp2_rr,"pre_score":s.pre_score,"reaction_score":react_score,"total_score":total_score,"coverage":total_cov,"trigger_delay_15m":delay,"R":R,"label":label,"MFE":mfe,"MAE":mae,"false_start":false_start,"hit_1R":bool(len(h1)),"hit_2R":bool(len(h2)),"hit_3R":bool(len(h3)),"entry_efficiency":entry_eff,"metadata":json.dumps({**s.metadata,"reaction_parts":react_parts},default=str)})
    return pd.DataFrame(trades),pd.DataFrame(misses)


def metrics(x: pd.DataFrame) -> dict:
    if x.empty: return {"n":0}
    y=x.sort_values("entry_time"); r=y.R.astype(float); gp=float(r[r>0].sum()); gl=float(-r[r<0].sum()); pf=gp/gl if gl>0 else float("inf"); eq=r.cumsum(); dd=eq-eq.cummax()
    return {"n":int(len(y)),"avg_R":float(r.mean()),"median_R":float(r.median()),"PF":float(pf),"maxDD_R":float(dd.min()),"win_rate":float((r>0).mean()),"sl_rate":float((y.label=="SL").mean()),"false_start_rate":float(y.false_start.mean()),"avg_MFE":float(y.MFE.mean()),"avg_MAE":float(y.MAE.mean()),"entry_efficiency":float(y.entry_efficiency.mean()),"hit_1R":float(y.hit_1R.mean()),"hit_2R":float(y.hit_2R.mean()),"hit_3R":float(y.hit_3R.mean()),"avg_trigger_delay_15m":float(pd.to_numeric(y.trigger_delay_15m,errors="coerce").mean()),"avg_coverage":float(y.coverage.mean())}


def bootstrap_mean_ci(x: pd.DataFrame, nboot: int=2000) -> dict:
    if len(x)<10: return {"lo":None,"mid":None,"hi":None}
    a=x.R.to_numpy(float); means=np.empty(nboot)
    for i in range(nboot): means[i]=RNG.choice(a,size=len(a),replace=True).mean()
    return {"lo":float(np.quantile(means,0.025)),"mid":float(np.quantile(means,0.5)),"hi":float(np.quantile(means,0.975))}


def bootstrap_delta_ci(a: pd.DataFrame,b: pd.DataFrame,nboot: int=2000) -> dict:
    if len(a)<10 or len(b)<10: return {"lo":None,"mid":None,"hi":None,"p_gt_0":None}
    aa=a.R.to_numpy(float); bb=b.R.to_numpy(float); d=np.empty(nboot)
    for i in range(nboot): d[i]=RNG.choice(aa,size=len(aa),replace=True).mean()-RNG.choice(bb,size=len(bb),replace=True).mean()
    return {"lo":float(np.quantile(d,0.025)),"mid":float(np.quantile(d,0.5)),"hi":float(np.quantile(d,0.975)),"p_gt_0":float((d>0).mean())}


def period_tag(t: pd.Timestamp) -> str:
    if t<VALID_START: return "TRAIN"
    if t<OOS_START: return "VALID"
    return "OOS"


def choose_config(grid: pd.DataFrame, variant: str) -> dict:
    z=grid[(grid.variant==variant)&(grid.period=="VALID")].copy()
    if z.empty: return {"score_floor":70,"atr_buffer":0.0}
    z=z[z.n>=24]
    if z.empty: return {"score_floor":70,"atr_buffer":0.0}
    z["objective"]=z.avg_R+0.12*np.log(z.PF.clip(lower=1e-6))-0.01*z.false_start_rate*100+0.002*z.maxDD_R
    q=z.sort_values(["objective","n"],ascending=[False,False]).iloc[0]
    return {"score_floor":int(q.score_floor),"atr_buffer":float(q.atr_buffer)}


def summarize_by(trades: pd.DataFrame, keys: List[str]) -> List[dict]:
    out=[]
    if trades.empty: return out
    for g,y in trades.groupby(keys):
        if not isinstance(g,tuple): g=(g,)
        row={k:v for k,v in zip(keys,g)}; row.update(metrics(y)); out.append(row)
    return out


def verdict(baseline: pd.DataFrame, v3fib: pd.DataFrame, v3nofib: pd.DataFrame) -> Tuple[str,dict]:
    b=metrics(baseline); v=metrics(v3fib); nf=metrics(v3nofib); delta=bootstrap_delta_ci(v3fib,baseline); fibdelta=bootstrap_delta_ci(v3fib,v3nofib); reasons=[]
    if b.get("n",0)<30 or v.get("n",0)<30: reasons.append("OOS sample count < 30 for baseline or V3")
    improve=v.get("avg_R",-9)>b.get("avg_R",9) and v.get("PF",0)>b.get("PF",999); dd_ok=abs(v.get("maxDD_R",-999))<=max(abs(b.get("maxDD_R",-999))*1.20,abs(b.get("maxDD_R",-999))+2.0); fs_ok=v.get("false_start_rate",1)<=b.get("false_start_rate",0)+0.02; p_ok=delta.get("p_gt_0") is not None and delta["p_gt_0"]>=0.70
    stable=True; asset_rows={}
    for asset in ASSETS:
        mb=metrics(baseline[baseline.asset==asset]); mv=metrics(v3fib[v3fib.asset==asset]); asset_rows[asset]={"baseline":mb,"v3":mv}
        if mb.get("n",0)>=10 and mv.get("n",0)>=10 and mv.get("avg_R",-9)<mb.get("avg_R",9)-0.10: stable=False
    if not improve: reasons.append("Combined OOS expectancy/PF did not both improve")
    if not dd_ok: reasons.append("MaxDD materially worsened")
    if not fs_ok: reasons.append("False-start rate worsened")
    if not p_ok: reasons.append("Bootstrap probability of positive expectancy delta < 70%")
    if not stable: reasons.append("Improvement not stable across BTC and ETH")
    if not reasons: status="V3 PASS — WORK MIGRATION CANDIDATE"
    else:
        clear_fail=(v.get("avg_R",0)<b.get("avg_R",0) and v.get("PF",0)<b.get("PF",0) and (delta.get("p_gt_0") or 0)<0.30); status="V3 FAIL — KEEP CURRENT BASELINE" if clear_fail else "V3 PARTIAL — MORE VALIDATION REQUIRED"
    fib_keep="KEEP" if (v.get("avg_R",0)>=nf.get("avg_R",0)-0.03 and v.get("PF",0)>=nf.get("PF",0)*0.97) else "REDUCE_OR_REMOVE"
    return status,{"baseline":b,"v3_fib":v,"v3_no_fib":nf,"delta_ci":delta,"fib_delta_ci":fibdelta,"asset_stability":asset_rows,"reasons":reasons,"fib_verdict":fib_keep}


def run():
    all_failures=[]; asset_data={}
    for asset in ASSETS:
        print("loading",asset,flush=True); fut1h,f1=load_archive(asset,"1h",False); fut15,f15=load_archive(asset,"15m",False); spot1h,fs=load_archive(asset,"1h",True); all_failures+=f1+f15+fs
        h=add_spot_alignment(enrich(fut1h),spot1h); m=enrich(fut15); h4=resample_completed(fut1h,"4h"); d1=resample_completed(fut1h,"1D"); asset_data[asset]=(h,m,h4,d1)
    btrs=[]; bmis=[]
    for asset,(h,m,h4,d1) in asset_data.items():
        tr,mi=evaluate_setups(build_baseline_setups(asset,h),h,m,None); btrs.append(tr); bmis.append(mi)
    baseline=pd.concat(btrs,ignore_index=True) if btrs else pd.DataFrame()
    if not baseline.empty: baseline["period"]=baseline.entry_time.map(period_tag)
    grid_rows=[]; trade_store={}; miss_store={}
    for fib_on in [False,True]:
        variant="V3+FIB" if fib_on else "V3-FIB"
        for buf in ATR_BUFFERS:
            asset_setups={asset:build_v3_setups(asset,h,h4,d1,fib_on,buf) for asset,(h,m,h4,d1) in asset_data.items()}
            for floor in SCORE_FLOORS:
                trs=[]; mis=[]
                for asset,(h,m,h4,d1) in asset_data.items(): tr,mi=evaluate_setups(asset_setups[asset],h,m,float(floor)); trs.append(tr); mis.append(mi)
                t=pd.concat(trs,ignore_index=True) if trs else pd.DataFrame(); mm=pd.concat(mis,ignore_index=True) if mis else pd.DataFrame()
                if not t.empty: t["period"]=t.entry_time.map(period_tag)
                trade_store[(variant,fib_on,buf,floor)]=t; miss_store[(variant,fib_on,buf,floor)]=mm
                for period in ["TRAIN","VALID","OOS"]:
                    q=t[t.period==period] if not t.empty else t; grid_rows.append({"variant":variant,"fib_on":fib_on,"atr_buffer":buf,"score_floor":floor,"period":period,**metrics(q)})
    grid=pd.DataFrame(grid_rows); cfg_nf=choose_config(grid,"V3-FIB"); cfg_f=choose_config(grid,"V3+FIB")
    v3nf=trade_store[("V3-FIB",False,cfg_nf["atr_buffer"],cfg_nf["score_floor"])]; v3f=trade_store[("V3+FIB",True,cfg_f["atr_buffer"],cfg_f["score_floor"])]; miss_nf=miss_store[("V3-FIB",False,cfg_nf["atr_buffer"],cfg_nf["score_floor"])]; miss_f=miss_store[("V3+FIB",True,cfg_f["atr_buffer"],cfg_f["score_floor"])]
    bo=baseline[baseline.period=="OOS"].copy() if not baseline.empty else pd.DataFrame(); nfo=v3nf[v3nf.period=="OOS"].copy() if not v3nf.empty else pd.DataFrame(); fo=v3f[v3f.period=="OOS"].copy() if not v3f.empty else pd.DataFrame(); status,decision=verdict(bo,fo,nfo)
    combined=pd.concat([bo,nfo,fo],ignore_index=True) if not (bo.empty and nfo.empty and fo.empty) else pd.DataFrame()
    summary={"research":"MASTER TRADING DAILY ENTRY ENGINE V3 SHADOW","generated_utc":pd.Timestamp.now(tz="UTC").isoformat(),"window":{"start":str(START),"validation_start":str(VALID_START),"oos_start":str(OOS_START),"end":str(END)},"assets":ASSETS,"baseline_definition":"CURRENT + TIME VALIDITY V2.1 point-in-time price/trigger proxy; not reconstructed historical MASTER outputs","v3_definition":"4H regime -> 1H setup/location -> 15m trigger/retest; available-history normalized 70+30 quality score; structural SL; realistic >=3R TP2","selected_configs":{"V3-FIB":cfg_nf,"V3+FIB":cfg_f},"oos":{"CURRENT_PROXY":metrics(bo),"V3-FIB":metrics(nfo),"V3+FIB":metrics(fo),"ci_CURRENT":bootstrap_mean_ci(bo),"ci_V3_FIB":bootstrap_mean_ci(fo),"delta_V3_vs_CURRENT":decision["delta_ci"],"delta_FIB_vs_NOFIB":decision["fib_delta_ci"]},"oos_by_asset":summarize_by(combined,["variant","asset"]),"oos_by_regime":summarize_by(combined,["variant","regime"]),"oos_by_direction":summarize_by(combined,["variant","direction"]),"missed_move_counts":{"V3-FIB":int(len(miss_nf)),"V3+FIB":int(len(miss_f))},"archive_failures":all_failures,"final_verdict":status,"decision_detail":decision,"limitations":["Historical OI/depth/liquidation-map/on-chain/whale data were not reconstructed and therefore were N/A, not zero.","The baseline is an auditable point-in-time operational proxy of current MASTER execution rules, not a claim that historical manual MASTER outputs existed.","Volume Profile is an OHLCV-bin proxy; order-book level microstructure is not available in the long historical archive.","No fees/slippage/leverage liquidation model is included; results are normalized in R to evaluate entry/stop/trigger quality."],"validated_engine_spec":{"status":"PROMOTION_CANDIDATE" if status.startswith("V3 PASS") else "NOT_PROMOTED","daily_frame_stack":"1D context -> 4H regime -> 1H setup -> 15m/30m trigger","fib_price":"KEEP" if decision["fib_verdict"]=="KEEP" else "REDUCE_OR_REMOVE","fib_time":"OFF","selected_v3_config":cfg_f,"hard_gates":["CurrentFresh","TradeFrameLocked","StructuralEntryOrRetest","CompletedTrigger","Participation","NonChasing","StructuralSL","TP1/2/3","CoreRR>=3","NoSevereRiskVeto","TimeValidity"]}}
    baseline.to_csv(OUT/"trades_current_proxy.csv",index=False); v3nf.to_csv(OUT/"trades_v3_no_fib.csv",index=False); v3f.to_csv(OUT/"trades_v3_fib.csv",index=False); grid.to_csv(OUT/"validation_grid.csv",index=False)
    with open(OUT/"summary.json","w",encoding="utf-8") as f: json.dump(summary,f,ensure_ascii=False,indent=2,default=str)
    def fmt(m):
        if not m or m.get("n",0)==0: return "N/A"
        return f"n={m['n']} | avgR={m['avg_R']:.3f} | PF={m['PF']:.3f} | MaxDD={m['maxDD_R']:.2f}R | false-start={m['false_start_rate']*100:.1f}% | MFE/MAE={m['avg_MFE']:.2f}/{m['avg_MAE']:.2f}"
    lines=["# MASTER TRADING DAILY ENTRY ENGINE V3 — SHADOW BACKTEST REPORT","",f"Final verdict: **{status}**","","## OOS combined",f"- CURRENT_PROXY: {fmt(metrics(bo))}",f"- V3-FIB: {fmt(metrics(nfo))}",f"- V3+FIB: {fmt(metrics(fo))}",f"- Bootstrap Δ(V3+FIB - CURRENT) avgR: {decision['delta_ci']}",f"- Fib ablation Δ(V3+FIB - V3-FIB) avgR: {decision['fib_delta_ci']}",f"- Selected configs: V3-FIB={cfg_nf}; V3+FIB={cfg_f}",f"- Fib verdict: {decision['fib_verdict']}","","## Decision reasons"]
    lines += [f"- {x}" for x in decision["reasons"]] if decision["reasons"] else ["- Promotion criteria satisfied on this shadow proxy/OOS test."]
    lines += ["","## Important limitations","- Long-horizon OI/depth/liquidation heatmap/on-chain/whale history was not reconstructed; N/A remains N/A.","- CURRENT baseline is a point-in-time operational proxy, not reconstructed historical MASTER output.","- No fees/slippage model; all comparisons are normalized in R.","","## Next step","- PASS: prepare Work migration/development prompt from VALIDATED_ENGINE_SPEC; do not cut over Production without final approval.","- PARTIAL/FAIL: keep current Production canonical/UI unchanged and refine only the research branch."]
    (OUT/"REPORT.md").write_text("\n".join(lines),encoding="utf-8")
    print(json.dumps({"final_verdict":status,"oos":summary["oos"],"selected_configs":summary["selected_configs"]},indent=2,default=str),flush=True)


if __name__=="__main__": run()
