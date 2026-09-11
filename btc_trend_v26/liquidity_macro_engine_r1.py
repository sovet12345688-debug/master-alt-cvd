from __future__ import annotations

import argparse, csv, io, json, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import requests

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
SUMMARY=ROOT/'market_vault/output/latest_summary.json'
MACRO=ROOT/'market_vault/output/latest_macro_liquidity.json'
BITGET='https://api.bitget.com/api/v3/market/history-candles'
FRED_MULTI='https://fred.stlouisfed.org/graph/fredgraph.csv?id={}'
LLAMA='https://stablecoins.llama.fi/stablecoincharts/all'
RECENT_EXCLUSION_DAYS=120
EVAL_DAYS=1080
FORWARD_DAYS=7
GATES={"min_signals":80,"min_hit_rate_pct":52.0,"min_holdout_signals":15,"min_holdout_hit_rate_pct":50.0,"min_positive_folds":3}
DEADBANDS={"real_yield_pp":0.03,"nominal_yield_pp":0.05,"netliq_pct":0.25,"stablecoin_pct":0.10,"tga_pct":2.0}
FRED_SERIES=('DFII10','DGS10','WALCL','WTREGEN','RRPONTSYD')


def iso(dt): return dt.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
def load_json(p): return json.loads(p.read_text())
def metric_map(p): return {x['metric']:x for x in p.get('metrics',[]) if isinstance(x,dict) and x.get('metric')}

def get_retry(url:str, *, params=None, timeout=60, attempts=3):
    last=None
    for i in range(attempts):
        try:
            r=requests.get(url,params=params,timeout=timeout,headers={'User-Agent':'btc-trend-v26-macro-r1/1.1'})
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            last=exc
            if i+1<attempts: time.sleep(2**i)
    raise RuntimeError(f'source fetch failed after {attempts} attempts: {url}: {last}')

def delta7(rec:dict[str,Any], fallback_weekly:bool=False):
    v=rec.get('vs_7d')
    if isinstance(v,dict) and v.get('delta') is not None: return float(v['delta']), float(v.get('delta_pct') or 0)
    if fallback_weekly:
        for k in ('vs_3d','vs_1d'):
            x=rec.get(k)
            if isinstance(x,dict) and x.get('delta') is not None and x.get('previous_source_observation_time') != rec.get('source_observation_time'):
                return float(x['delta']), float(x.get('delta_pct') or 0)
    return None

def sign_deadband(x:float, band:float)->int:
    return 1 if x>band else -1 if x<-band else 0

def classify_components(real_delta_pp, nom_delta_pp, netliq_pct, stable_pct, tga_pct):
    comps={
      'REAL_YIELD': -sign_deadband(real_delta_pp,DEADBANDS['real_yield_pp']),
      'NOMINAL_YIELD': -sign_deadband(nom_delta_pp,DEADBANDS['nominal_yield_pp']),
      'NET_LIQUIDITY': sign_deadband(netliq_pct,DEADBANDS['netliq_pct']),
      'STABLECOIN_SUPPLY': sign_deadband(stable_pct,DEADBANDS['stablecoin_pct']),
      'TGA': -sign_deadband(tga_pct,DEADBANDS['tga_pct']),
    }
    score=sum(comps.values())
    state='STRONG_LONG' if score>=4 else 'LONG' if score>=2 else 'STRONG_SHORT' if score<=-4 else 'SHORT' if score<=-2 else 'NEUTRAL'
    return state,score,comps

def current_feature():
    s=metric_map(load_json(SUMMARY)); m=metric_map(load_json(MACRO))
    req=['US10Y_REAL','US10Y','STABLECOIN_TOTAL_SUPPLY','TGA_CLOSING_BALANCE']
    if any(k not in s for k in req) or 'US_NET_LIQUIDITY_PROXY' not in m: raise RuntimeError('required current macro metrics missing')
    r=delta7(s['US10Y_REAL']); n=delta7(s['US10Y']); st=delta7(s['STABLECOIN_TOTAL_SUPPLY']); tga=delta7(s['TGA_CLOSING_BALANCE']); nl=delta7(m['US_NET_LIQUIDITY_PROXY'],True)
    if any(x is None for x in (r,n,st,tga,nl)): raise RuntimeError('required 7d/current-source delta missing')
    state,score,comps=classify_components(r[0],n[0],nl[1],st[1],tga[1])
    times=[s[k].get('source_observation_time') for k in req]+[m['US_NET_LIQUIDITY_PROXY'].get('source_observation_time')]
    return {'state':state,'composite_score':score,'components':comps,'inputs':{'real_yield_delta_pp':r[0],'nominal_yield_delta_pp':n[0],'net_liquidity_delta_pct':nl[1],'stablecoin_supply_delta_pct':st[1],'tga_delta_pct':tga[1]},'source_times':times}

def fred_bundle(series_ids=FRED_SERIES)->dict[str,dict[datetime,float]]:
    # One request instead of five independent requests. This removes the prior FRED timeout bottleneck.
    url=FRED_MULTI.format(','.join(series_ids))
    r=get_retry(url,timeout=60,attempts=3)
    out={sid:{} for sid in series_ids}
    for row in csv.DictReader(io.StringIO(r.text)):
        date_raw=row.get('DATE') or row.get('observation_date')
        if not date_raw: continue
        try: dt=datetime.strptime(date_raw,'%Y-%m-%d').replace(tzinfo=timezone.utc)
        except ValueError: continue
        for sid in series_ids:
            raw=row.get(sid)
            if not raw or raw=='.': continue
            try: out[sid][dt]=float(raw)
            except (TypeError,ValueError): pass
    missing=[sid for sid in series_ids if not out[sid]]
    if missing: raise RuntimeError(f'FRED bundle missing series: {missing}')
    return out

def stable_series()->dict[datetime,float]:
    r=get_retry(LLAMA,timeout=60,attempts=3); data=r.json(); out={}
    def num(v):
        if isinstance(v,(int,float)): return float(v)
        if isinstance(v,dict): return sum(num(x) for x in v.values())
        return 0.0
    for x in data if isinstance(data,list) else []:
        try:
            dt=datetime.fromtimestamp(int(x['date']),tz=timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
            val=num(x.get('totalCirculatingUSD')) or num(x.get('totalCirculating'))
            if val>0: out[dt]=val
        except Exception: pass
    if not out: raise RuntimeError('stablecoin history unavailable')
    return out

def btc_daily(start,end):
    rows={}; cur=start
    while cur<end:
        ce=min(cur+timedelta(days=80),end)
        r=get_retry(BITGET,params={'category':'USDT-FUTURES','symbol':'BTCUSDT','interval':'1D','startTime':str(int(cur.timestamp()*1000)),'endTime':str(int(ce.timestamp()*1000)),'type':'market','limit':'100'},timeout=30,attempts=3); p=r.json()
        if str(p.get('code'))!='00000': raise RuntimeError(f'Bitget {p!r}')
        for x in p.get('data') or []: rows[int(x[0])]=float(x[4])
        cur=ce; time.sleep(.04)
    return [(datetime.fromtimestamp(k/1000,tz=timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0),rows[k]) for k in sorted(rows)]

def ff(series:dict[datetime,float], d:datetime):
    ks=[k for k in series.keys() if k<=d]
    return series[max(ks)] if ks else None

def pct(a,b): return (a/b-1)*100 if a is not None and b not in (None,0) else None

def summarize(rs):
    n=len(rs); h=sum(int(x['hit']) for x in rs); av=sum(x['signed'] for x in rs)/n if n else None
    return {'signals':n,'hits':h,'hit_rate_pct':round(100*h/n,2) if n else None,'avg_signed_forward_return_pct':round(100*av,4) if av is not None else None}

def oos(now):
    ee=(now-timedelta(days=RECENT_EXCLUSION_DAYS)).replace(hour=0,minute=0,second=0,microsecond=0); es=ee-timedelta(days=EVAL_DAYS); fs=es-timedelta(days=45)
    btc=btc_daily(fs,ee+timedelta(days=FORWARD_DAYS+3))
    fred=fred_bundle(); real=fred['DFII10']; nom=fred['DGS10']; fed=fred['WALCL']; tga=fred['WTREGEN']; rrp=fred['RRPONTSYD']
    stable=stable_series()
    prices={d:p for d,p in btc}; dates=[d for d,_ in btc]
    recs=[]
    for idx,d in enumerate(dates):
        if d<es or d>ee or idx%7!=0: continue
        target=d+timedelta(days=FORWARD_DAYS)
        if target not in prices: continue
        d7=d-timedelta(days=7)
        vals=[ff(real,d),ff(real,d7),ff(nom,d),ff(nom,d7),ff(fed,d),ff(fed,d7),ff(tga,d),ff(tga,d7),ff(rrp,d),ff(rrp,d7),ff(stable,d),ff(stable,d7)]
        if any(v is None for v in vals): continue
        rv,rv0,nv,nv0,fv,fv0,tv,tv0,rr,rr0,sv,sv0=vals
        nl=fv-tv-rr*1000.0; nl0=fv0-tv0-rr0*1000.0
        state,score,_=classify_components(rv-rv0,nv-nv0,pct(nl,nl0),pct(sv,sv0),pct(tv,tv0))
        if state=='NEUTRAL': continue
        fwd=prices[target]/prices[d]-1
        signed=fwd if state in {'LONG','STRONG_LONG'} else -fwd
        recs.append({'date':d,'state':state,'score':score,'signed':signed,'hit':signed>0})
    total=summarize(recs); sm=int(es.timestamp()); em=int(ee.timestamp()); span=em-sm; folds=[]
    for j in range(4):
        lo=sm+span*j//4; hi=sm+span*(j+1)//4
        sub=[x for x in recs if lo<=int(x['date'].timestamp())<(hi if j<3 else hi+1)]; folds.append({'fold':j+1,**summarize(sub)})
    hs=sm+span*3//4; hold=summarize([x for x in recs if int(x['date'].timestamp())>=hs]); pf=sum(1 for f in folds if f['signals'] and (f['avg_signed_forward_return_pct'] or 0)>0)
    checks={'signal_count':total['signals']>=GATES['min_signals'],'hit_rate':(total['hit_rate_pct'] or 0)>=GATES['min_hit_rate_pct'],'positive_return':(total['avg_signed_forward_return_pct'] or 0)>0,'holdout_signal_count':hold['signals']>=GATES['min_holdout_signals'],'holdout_hit_rate':(hold['hit_rate_pct'] or 0)>=GATES['min_holdout_hit_rate_pct'],'holdout_positive_return':(hold['avg_signed_forward_return_pct'] or 0)>0,'fold_stability':pf>=GATES['min_positive_folds']}
    return {'window_start':iso(es),'window_end':iso(ee),'total':total,'folds':folds,'holdout':hold,'positive_folds':pf,'checks':checks,'promotion_gate':'PASS' if all(checks.values()) else 'HOLD'}

def build():
    now=datetime.now(timezone.utc); cur=current_feature(); val=oos(now); gate=val['promotion_gate']
    feature={'availability':'CURRENT' if gate=='PASS' else 'N_A_THRESHOLD_UNAPPROVED','state':cur['state'] if gate=='PASS' else None,'feature_timestamp':iso(now),'source_timestamps':{'market_vault_summary':load_json(SUMMARY).get('snapshot_hour_utc'),'market_vault_macro':load_json(MACRO).get('snapshot_hour_utc')},'lineage':{'engine':'BTC_TREND_V26_LIQUIDITY_MACRO_R1','current_sources':['market_vault/output/latest_summary.json','market_vault/output/latest_macro_liquidity.json'],'oos_sources':['FRED bundled CSV','DefiLlama','Bitget'],'promotion_gate':gate},'details':cur}
    return {'schema_version':'1.1','engine_id':'BTC_TREND_V26_LIQUIDITY_MACRO_R1','status':'SHADOW_CANDIDATE','asof_utc':iso(now),'deadbands':DEADBANDS,'gates':GATES,'oos':val,'feature':feature,'production_approved':False,'official_state_write_allowed':False}

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    try:o=build()
    except Exception as e:o={'status':'VALIDATION_FAIL','error':f'{type(e).__name__}: {e}','production_approved':False,'official_state_write_allowed':False}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(o,indent=2)+'\n');print(json.dumps(o,indent=2));return 0 if o.get('status')!='VALIDATION_FAIL' else 2
if __name__=='__main__': raise SystemExit(main())
