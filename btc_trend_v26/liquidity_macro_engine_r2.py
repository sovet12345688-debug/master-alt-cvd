from __future__ import annotations

import argparse, json, time, xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import requests

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
SUMMARY=ROOT/'market_vault/output/latest_summary.json'
TREASURY_XML='https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml'
FISCAL_TGA='https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/dts/operating_cash_balance'
LLAMA='https://stablecoins.llama.fi/stablecoincharts/all'
BITGET='https://api.bitget.com/api/v3/market/history-candles'
RECENT_EXCLUSION_DAYS=120
EVAL_DAYS=1080
FORWARD_DAYS=7
GATES={"min_signals":80,"min_hit_rate_pct":52.0,"min_holdout_signals":15,"min_holdout_hit_rate_pct":50.0,"min_positive_folds":3}
DEADBANDS={"real_yield_pp":0.03,"nominal_yield_pp":0.05,"stablecoin_pct":0.10,"tga_pct":2.0}
HEADERS={'User-Agent':'btc-trend-v26-macro-r2/1.0'}


def iso(dt): return dt.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
def load_json(p): return json.loads(p.read_text())
def metric_map(p): return {x['metric']:x for x in p.get('metrics',[]) if isinstance(x,dict) and x.get('metric')}
def local_name(tag): return tag.rsplit('}',1)[-1] if '}' in tag else tag

def get_retry(url, *, params=None, timeout=30, attempts=3):
    last=None
    for i in range(attempts):
        try:
            r=requests.get(url,params=params,headers=HEADERS,timeout=timeout); r.raise_for_status(); return r
        except requests.RequestException as e:
            last=e
            if i+1<attempts: time.sleep(1.5*(i+1))
    raise RuntimeError(f'fetch failed: {url}: {last}')

def delta7(rec):
    x=rec.get('vs_7d')
    if not isinstance(x,dict) or x.get('delta') is None: return None
    return float(x['delta']),float(x.get('delta_pct') or 0)

def sign_deadband(x,band): return 1 if x>band else -1 if x<-band else 0

def classify(real_delta_pp,nom_delta_pp,stable_pct,tga_pct):
    comps={
      'REAL_YIELD':-sign_deadband(real_delta_pp,DEADBANDS['real_yield_pp']),
      'NOMINAL_YIELD':-sign_deadband(nom_delta_pp,DEADBANDS['nominal_yield_pp']),
      'STABLECOIN_SUPPLY':sign_deadband(stable_pct,DEADBANDS['stablecoin_pct']),
      'TGA':-sign_deadband(tga_pct,DEADBANDS['tga_pct']),
    }
    score=sum(comps.values())
    state='STRONG_LONG' if score>=3 else 'LONG' if score>=2 else 'STRONG_SHORT' if score<=-3 else 'SHORT' if score<=-2 else 'NEUTRAL'
    return state,score,comps

def current_feature():
    s=metric_map(load_json(SUMMARY)); req=['US10Y_REAL','US10Y','STABLECOIN_TOTAL_SUPPLY','TGA_CLOSING_BALANCE']
    if any(k not in s for k in req): raise RuntimeError('required current macro metrics missing')
    vals={k:delta7(s[k]) for k in req}
    if any(v is None for v in vals.values()): raise RuntimeError('required current 7d delta missing')
    state,score,comps=classify(vals['US10Y_REAL'][0],vals['US10Y'][0],vals['STABLECOIN_TOTAL_SUPPLY'][1],vals['TGA_CLOSING_BALANCE'][1])
    return {'state':state,'composite_score':score,'components':comps,'inputs':{'real_yield_delta_pp':vals['US10Y_REAL'][0],'nominal_yield_delta_pp':vals['US10Y'][0],'stablecoin_supply_delta_pct':vals['STABLECOIN_TOTAL_SUPPLY'][1],'tga_delta_pct':vals['TGA_CLOSING_BALANCE'][1]},'source_times':{k:s[k].get('source_observation_time') for k in req},'net_liquidity_auxiliary':'NOT_SCORED_UNTIL_VALIDATED_HISTORY_EXISTS'}

def treasury_year(data_key,field,year):
    r=get_retry(TREASURY_XML,params={'data':data_key,'field_tdr_date_value':str(year)},timeout=30)
    root=ET.fromstring(r.content); out={}
    for elem in root.iter():
        if local_name(elem.tag)!='properties': continue
        vals={local_name(c.tag):(c.text or '').strip() for c in list(elem)}
        rawd=vals.get('NEW_DATE'); rawv=vals.get(field)
        if not rawd or not rawv: continue
        try:
            d=datetime.fromisoformat(rawd.replace('Z','+00:00')).astimezone(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
            out[d]=float(rawv)
        except Exception: pass
    return out

def treasury_history(data_key,field,start,end):
    out={}
    for y in range(start.year,end.year+1): out.update(treasury_year(data_key,field,y))
    if not out: raise RuntimeError(f'no Treasury history {data_key}/{field}')
    return out

def tga_history(start,end):
    params={'sort':'record_date','page[size]':'5000','fields':'record_date,account_type,close_today_bal,open_today_bal','format':'json','filter':f'record_date:gte:{start.date()},record_date:lte:{end.date()}'}
    doc=get_retry(FISCAL_TGA,params=params,timeout=45).json(); rows=doc.get('data',[]) if isinstance(doc,dict) else []
    bydate={}
    for r in rows:
        d=str(r.get('record_date') or ''); typ=str(r.get('account_type') or '').lower()
        if 'treasury general account' not in typ: continue
        raw=r.get('close_today_bal') or r.get('open_today_bal')
        try: val=float(str(raw).replace(',',''))
        except Exception: continue
        try: dt=datetime.strptime(d,'%Y-%m-%d').replace(tzinfo=timezone.utc)
        except Exception: continue
        # Prefer explicit closing-labelled record where duplicates exist.
        rank=2 if 'closing' in typ else 1
        old=bydate.get(dt)
        if old is None or rank>old[0]: bydate[dt]=(rank,val)
    out={d:v for d,(_,v) in bydate.items()}
    if not out: raise RuntimeError('no TGA history')
    return out

def stable_history():
    data=get_retry(LLAMA,timeout=60).json(); out={}
    def num(v):
        if isinstance(v,(int,float)): return float(v)
        if isinstance(v,dict): return sum(num(x) for x in v.values())
        return 0.0
    for x in data if isinstance(data,list) else []:
        try:
            d=datetime.fromtimestamp(int(x['date']),tz=timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
            v=num(x.get('totalCirculatingUSD')) or num(x.get('totalCirculating'))
            if v>0: out[d]=v
        except Exception: pass
    if not out: raise RuntimeError('no stablecoin history')
    return out

def btc_daily(start,end):
    rows={}; cur=start
    while cur<end:
        ce=min(cur+timedelta(days=80),end)
        p=get_retry(BITGET,params={'category':'USDT-FUTURES','symbol':'BTCUSDT','interval':'1D','startTime':str(int(cur.timestamp()*1000)),'endTime':str(int(ce.timestamp()*1000)),'type':'market','limit':'100'},timeout=30).json()
        if str(p.get('code'))!='00000': raise RuntimeError(f'Bitget {p!r}')
        for x in p.get('data') or []:
            try: rows[int(x[0])]=float(x[4])
            except Exception: pass
        cur=ce; time.sleep(.04)
    return [(datetime.fromtimestamp(k/1000,tz=timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0),rows[k]) for k in sorted(rows)]
def ff(series,d):
    ks=[k for k in series if k<=d]; return series[max(ks)] if ks else None
def pct(a,b): return (a/b-1)*100 if a is not None and b not in (None,0) else None
def summarize(rs):
    n=len(rs); h=sum(int(x['hit']) for x in rs); av=sum(x['signed'] for x in rs)/n if n else None
    return {'signals':n,'hits':h,'hit_rate_pct':round(100*h/n,2) if n else None,'avg_signed_forward_return_pct':round(100*av,4) if av is not None else None}

def oos(now):
    ee=(now-timedelta(days=RECENT_EXCLUSION_DAYS)).replace(hour=0,minute=0,second=0,microsecond=0); es=ee-timedelta(days=EVAL_DAYS); fs=es-timedelta(days=45)
    btc=btc_daily(fs,ee+timedelta(days=FORWARD_DAYS+3)); prices={d:p for d,p in btc}; dates=[d for d,_ in btc]
    real=treasury_history('daily_treasury_real_yield_curve','TC_10YEAR',fs,ee)
    nom=treasury_history('daily_treasury_yield_curve','BC_10YEAR',fs,ee)
    tga=tga_history(fs,ee); stable=stable_history(); recs=[]
    for idx,d in enumerate(dates):
        if d<es or d>ee or idx%7!=0: continue
        target=d+timedelta(days=FORWARD_DAYS)
        if target not in prices: continue
        d7=d-timedelta(days=7)
        vals=[ff(real,d),ff(real,d7),ff(nom,d),ff(nom,d7),ff(stable,d),ff(stable,d7),ff(tga,d),ff(tga,d7)]
        if any(v is None for v in vals): continue
        rv,rv0,nv,nv0,sv,sv0,tv,tv0=vals
        state,score,_=classify(rv-rv0,nv-nv0,pct(sv,sv0),pct(tv,tv0))
        if state=='NEUTRAL': continue
        fwd=prices[target]/prices[d]-1; signed=fwd if state in {'LONG','STRONG_LONG'} else -fwd
        recs.append({'date':d,'state':state,'score':score,'signed':signed,'hit':signed>0})
    total=summarize(recs); sm=int(es.timestamp()); em=int(ee.timestamp()); span=em-sm; folds=[]
    for j in range(4):
        lo=sm+span*j//4; hi=sm+span*(j+1)//4; sub=[x for x in recs if lo<=int(x['date'].timestamp())<(hi if j<3 else hi+1)]
        folds.append({'fold':j+1,**summarize(sub)})
    hs=sm+span*3//4; hold=summarize([x for x in recs if int(x['date'].timestamp())>=hs]); pf=sum(1 for f in folds if f['signals'] and (f['avg_signed_forward_return_pct'] or 0)>0)
    checks={'signal_count':total['signals']>=GATES['min_signals'],'hit_rate':(total['hit_rate_pct'] or 0)>=GATES['min_hit_rate_pct'],'positive_return':(total['avg_signed_forward_return_pct'] or 0)>0,'holdout_signal_count':hold['signals']>=GATES['min_holdout_signals'],'holdout_hit_rate':(hold['hit_rate_pct'] or 0)>=GATES['min_holdout_hit_rate_pct'],'holdout_positive_return':(hold['avg_signed_forward_return_pct'] or 0)>0,'fold_stability':pf>=GATES['min_positive_folds']}
    return {'window_start':iso(es),'window_end':iso(ee),'sources':{'nominal':'US Treasury XML','real':'US Treasury XML','tga':'Treasury FiscalData','stablecoin':'DefiLlama','btc':'Bitget'},'total':total,'folds':folds,'holdout':hold,'positive_folds':pf,'checks':checks,'promotion_gate':'PASS' if all(checks.values()) else 'HOLD'}

def build():
    now=datetime.now(timezone.utc); cur=current_feature(); val=oos(now); gate=val['promotion_gate']; summary=load_json(SUMMARY)
    feature={'availability':'CURRENT' if gate=='PASS' else 'N_A_THRESHOLD_UNAPPROVED','state':cur['state'] if gate=='PASS' else None,'feature_timestamp':iso(now),'source_timestamps':{'market_vault_summary':summary.get('snapshot_hour_utc')},'lineage':{'engine':'BTC_TREND_V26_LIQUIDITY_MACRO_R2','current_source':'market_vault/output/latest_summary.json','oos_sources':['US Treasury XML','Treasury FiscalData','DefiLlama','Bitget'],'net_liquidity_score_role':'ZERO_UNTIL_HISTORY_VALIDATED','promotion_gate':gate},'details':cur}
    return {'schema_version':'2.0','engine_id':'BTC_TREND_V26_LIQUIDITY_MACRO_R2','status':'SHADOW_CANDIDATE','asof_utc':iso(now),'deadbands':DEADBANDS,'gates':GATES,'oos':val,'feature':feature,'production_approved':False,'official_state_write_allowed':False}

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    try:o=build()
    except Exception as e:o={'schema_version':'2.0','engine_id':'BTC_TREND_V26_LIQUIDITY_MACRO_R2','status':'VALIDATION_FAIL','error':f'{type(e).__name__}: {e}','production_approved':False,'official_state_write_allowed':False}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(o,indent=2)+'\n');print(json.dumps(o,indent=2));return 0 if o.get('status')!='VALIDATION_FAIL' else 2
if __name__=='__main__': raise SystemExit(main())
