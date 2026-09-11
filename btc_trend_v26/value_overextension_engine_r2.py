from __future__ import annotations

import argparse, json, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import requests

URL='https://api.bitget.com/api/v3/market/history-candles'
DAY_MS=86400000
MIN_BARS=260
RECENT_EXCLUSION_DAYS=120
EVAL_DAYS=1080
FORWARD_DAYS=20
LOOKBACK_PERCENTILE=180
GATES={"min_signals":60,"min_hit_rate_pct":53.0,"min_holdout_signals":12,"min_holdout_hit_rate_pct":50.0,"min_positive_folds":3}


def iso(dt): return dt.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
def get_retry(url,params,attempts=3):
    last=None
    for i in range(attempts):
        try:
            r=requests.get(url,params=params,timeout=30,headers={'User-Agent':'btc-trend-v26-value-r2/1.0'});r.raise_for_status();return r
        except requests.RequestException as e:
            last=e
            if i+1<attempts: time.sleep(1+i)
    raise RuntimeError(str(last))
def fetch_daily(start,end):
    rows={};cur=start
    while cur<end:
        ce=min(cur+timedelta(days=80),end)
        p=get_retry(URL,{'category':'USDT-FUTURES','symbol':'BTCUSDT','interval':'1D','startTime':str(int(cur.timestamp()*1000)),'endTime':str(int(ce.timestamp()*1000)),'type':'market','limit':'100'}).json()
        if str(p.get('code'))!='00000': raise RuntimeError(f'Bitget {p!r}')
        for x in p.get('data') or []:
            try: rows[int(x[0])]={'ts':float(x[0]),'open':float(x[1]),'high':float(x[2]),'low':float(x[3]),'close':float(x[4])}
            except Exception: pass
        cur=ce;time.sleep(.03)
    out=[rows[k] for k in sorted(rows)]
    if len(out)<MIN_BARS: raise RuntimeError(f'insufficient bars {len(out)}')
    return out
def ema(vals,period):
    a=2/(period+1);v=vals[0]
    for x in vals[1:]: v=a*x+(1-a)*v
    return v
def atr14(rows):
    trs=[]
    for i in range(len(rows)-14,len(rows)):
        r=rows[i];pc=rows[i-1]['close'];trs.append(max(r['high']-r['low'],abs(r['high']-pc),abs(r['low']-pc)))
    return sum(trs)/len(trs)
def rsi(vals,period=14):
    ds=[vals[i]-vals[i-1] for i in range(len(vals)-period,len(vals))];g=sum(max(d,0) for d in ds)/period;l=sum(max(-d,0) for d in ds)/period
    if l==0:return 100.0
    return 100-100/(1+g/l)
def weekly_closes(rows):
    out=[];key=None;last=None
    for r in rows:
        dt=datetime.fromtimestamp(r['ts']/1000,tz=timezone.utc);k=(dt.isocalendar().year,dt.isocalendar().week)
        if key is not None and k!=key and last is not None:out.append(last)
        key=k;last=r['close']
    if last is not None:out.append(last)
    return out
def percentile_rank(xs,value):
    if not xs:return None
    return 100.0*sum(1 for x in xs if x<=value)/len(xs)
def deviation_series(rows):
    out=[]
    for i in range(60,len(rows)):
        sub=rows[:i+1];cl=[x['close'] for x in sub];e50=ema(cl[-140:],50);a=atr14(sub)
        out.append((cl[-1]-e50)/a if a>0 else 0.0)
    return out
def classify(rows):
    cl=[x['close'] for x in rows];e50=ema(cl[-140:],50);a=atr14(rows);dev=(cl[-1]-e50)/a if a>0 else 0
    hist=deviation_series(rows[-(LOOKBACK_PERCENTILE+60):])
    rank=percentile_rank(hist[:-1],dev) if len(hist)>1 else None
    wc=weekly_closes(rows);wr=rsi(wc,14) if len(wc)>=15 else None
    state='NEUTRAL'
    if rank is not None and wr is not None:
        if rank<=10 and wr<=45: state='STRONG_LONG'
        elif rank<=20 and wr<=50: state='LONG'
        elif rank>=90 and wr>=55: state='STRONG_SHORT'
        elif rank>=80 and wr>=50: state='SHORT'
    return {'state':state,'ema50':e50,'atr14':a,'distance_ema50_atr':dev,'deviation_percentile_180d':rank,'rsi_1w':wr,'close':cl[-1],'completed_ts_ms':int(rows[-1]['ts'])}
def summarize(rs):
    n=len(rs);h=sum(int(x['hit']) for x in rs);avg=sum(x['signed'] for x in rs)/n if n else None
    return {'signals':n,'hits':h,'hit_rate_pct':round(100*h/n,2) if n else None,'avg_signed_forward_return_pct':round(100*avg,4) if avg is not None else None}
def build(now=None):
    now=now or datetime.now(timezone.utc);ee=(now-timedelta(days=RECENT_EXCLUSION_DAYS)).replace(hour=0,minute=0,second=0,microsecond=0);es=ee-timedelta(days=EVAL_DAYS)
    rows=fetch_daily(es-timedelta(days=400),now);cur=classify(rows);recs=[]
    for i in range(MIN_BARS-1,len(rows)-FORWARD_DAYS):
        asof=int(rows[i]['ts'])+DAY_MS
        if asof<int(es.timestamp()*1000) or asof>int(ee.timestamp()*1000):continue
        c=classify(rows[:i+1]);s=c['state']
        if s=='NEUTRAL':continue
        fwd=rows[i+FORWARD_DAYS]['close']/rows[i]['close']-1;signed=fwd if s in {'LONG','STRONG_LONG'} else -fwd
        recs.append({'asof':asof,'state':s,'signed':signed,'hit':signed>0})
    total=summarize(recs);sm=int(es.timestamp()*1000);em=int(ee.timestamp()*1000);span=em-sm;folds=[]
    for j in range(4):
        lo=sm+span*j//4;hi=sm+span*(j+1)//4;sub=[x for x in recs if lo<=x['asof']<(hi if j<3 else hi+1)];folds.append({'fold':j+1,**summarize(sub)})
    hs=sm+span*3//4;hold=summarize([x for x in recs if x['asof']>=hs]);pf=sum(1 for f in folds if f['signals'] and (f['avg_signed_forward_return_pct'] or 0)>0)
    checks={'signal_count':total['signals']>=GATES['min_signals'],'hit_rate':(total['hit_rate_pct'] or 0)>=GATES['min_hit_rate_pct'],'positive_return':(total['avg_signed_forward_return_pct'] or 0)>0,'holdout_signal_count':hold['signals']>=GATES['min_holdout_signals'],'holdout_hit_rate':(hold['hit_rate_pct'] or 0)>=GATES['min_holdout_hit_rate_pct'],'holdout_positive_return':(hold['avg_signed_forward_return_pct'] or 0)>0,'fold_stability':pf>=GATES['min_positive_folds']};gate='PASS' if all(checks.values()) else 'HOLD'
    feature={'availability':'CURRENT' if gate=='PASS' else 'N_A_THRESHOLD_UNAPPROVED','state':cur['state'] if gate=='PASS' else None,'feature_timestamp':iso(datetime.fromtimestamp((cur['completed_ts_ms']+DAY_MS)/1000,tz=timezone.utc)),'source_timestamps':{'bitget_1d_completed_open_ms':str(cur['completed_ts_ms'])},'lineage':{'engine':'BTC_TREND_V26_VALUE_OVEREXTENSION_R2','source':'Bitget BTCUSDT USDT Futures','completed_candle_only':True,'hypothesis':'adaptive 180d EMA50/ATR value percentile + weekly RSI, 20d horizon','promotion_gate':gate},'details':cur}
    return {'schema_version':'2.0','engine_id':'BTC_TREND_V26_VALUE_OVEREXTENSION_R2','status':'SHADOW_CANDIDATE','asof_utc':iso(now),'hypothesis_frozen_before_run':True,'gates':GATES,'oos':{'window_start':iso(es),'window_end':iso(ee),'forward_days':FORWARD_DAYS,'total':total,'folds':folds,'holdout':hold,'positive_folds':pf,'checks':checks,'promotion_gate':gate},'feature':feature,'production_approved':False,'official_state_write_allowed':False}
def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    try:o=build()
    except Exception as e:o={'schema_version':'2.0','engine_id':'BTC_TREND_V26_VALUE_OVEREXTENSION_R2','status':'VALIDATION_FAIL','error':f'{type(e).__name__}: {e}','production_approved':False,'official_state_write_allowed':False}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(o,indent=2)+'\n');print(json.dumps(o,indent=2));return 0 if o.get('status')!='VALIDATION_FAIL' else 2
if __name__=='__main__':raise SystemExit(main())
