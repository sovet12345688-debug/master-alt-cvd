from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import run_v25_backtest as bt

OUT=Path('btc_backtest/output'); OUT.mkdir(parents=True,exist_ok=True)
GRIDS={
 'strict':{'zone':82,'ex':70,'er':75,'indep':3},
 'moderate':{'zone':80,'ex':65,'er':70,'indep':3},
 'loose':{'zone':78,'ex':60,'er':65,'indep':2},
}


def rr(e,s,t):
    return (t-e)/(e-s) if e>s>0 and t>e else -999.0

def safety(e,s):
    if not e>s>0:return 0
    d=(e-s)/e
    return 90 if d<=.12 else (85 if d<=.15 else (80 if d<=.18 else 70))

def groups(h,pos,z,er):
    r=h.iloc[pos]; p3=h.ret3.iloc[pos-3] if pos>=3 and pd.notna(h.ret3.iloc[pos-3]) else np.nan
    g=[
      (r.low<=z['high'] and r.close>=z['center']) or r.close>z['high'],
      pd.notna(r.vol_z) and r.vol_z>=1 and r.close_loc>=.55,
      pd.notna(r.taker_imb6) and r.taker_imb6<=-.05 and r.close>=z['center'],
      r.rsi14<=38 and er>=70,
      pd.notna(p3) and pd.notna(r.ret3) and r.ret3>p3+.008,
      r.lower_wick_ratio>=.30 or r.close>z['high']]
    return sum(bool(x) for x in g)

def outcome(h,t,e,sl,target,expiry):
    fut=h[(h.index>=t)&(h.index<=min(expiry,t+pd.Timedelta(days=30)))]
    if fut.empty:return {}
    o={}
    for n in [4,24,72]:o[f'ret{n}h']=float(fut.close.iloc[min(n,len(fut)-1)]/e-1)
    o['mfe']=float(fut.high.max()/e-1);o['mae']=float(fut.low.min()/e-1);o['first_hit']='none'
    for _,b in fut.iterrows():
        if b.low<=sl:o['first_hit']='SL';break
        if b.high>=target:o['first_hit']='TP1';break
    return o

def summary(df,prefix):
    s=df[df[f'{prefix}_time'].notna()]
    o={'signals':int(len(s))}
    if not len(s):return o
    o['sl_first_rate_pct']=round(100*(s[f'{prefix}_first_hit']=='SL').mean(),1)
    o['tp1_first_rate_pct']=round(100*(s[f'{prefix}_first_hit']=='TP1').mean(),1)
    for k in ['ret24h','ret72h','mfe','mae']:o[f'median_{k}_pct']=round(100*s[f'{prefix}_{k}'].median(),2)
    o['median_rr']=round(float(s[f'{prefix}_rr'].median()),2)
    return o

def run():
    h,fail=bt.load_data();h=bt.enrich_hourly(h);h4=bt.resample_ohlcv(h,'4h');d=bt.resample_ohlcv(h,'1D');w=bt.resample_ohlcv(h,'W-SUN');plans=bt.build_plans(h,d,w,h4);ht=h.index;rows=[]
    for p in plans:
      win=h[(h.index>=p['start'])&(h.index<=p['expiry'])]
      if win.empty:continue
      dr=d.loc[d.index<=p['reg']].iloc[-1];wr=bt.latest_row_before(w,p['reg']);r4=bt.latest_row_before(h4,p['reg'])
      for z in p['zones']:
        hits=win[(win.low<=z['high'])&(win.high>=z['low'])]
        if hits.empty:continue
        touch=hits.index[0];tp=ht.get_loc(touch);zq=bt.zone_quality(p,z,h.iloc[tp],dr,wr,r4)
        rec={'plan':p['id'],'zone':z['n'],'touch':touch,'zone_quality':zq,'common_sl':p['sl'],'target':p['anchor_high']}
        for gn in GRIDS:
          for rrt in [0,2,2.5,3,4]:
            key=f'{gn}_tactical_rr{str(rrt).replace(".","p")}'
            rec[f'{key}_time']=pd.NaT;rec[f'{key}_price']=np.nan;rec[f'{key}_sl']=np.nan;rec[f'{key}_rr']=np.nan
        end=min(len(h)-1,tp+72)
        for pos in range(tp,end+1):
          r=h.iloc[pos];fear=sum(bt.fear_flags(h,pos).values())>=2;ex=bt.exhaust_score(h,pos,z);er=bt.early_score(h,pos,z,ex);ind=groups(h,pos,z,er);nc=bt.nonchase(r)
          recent_low=float(h.low.iloc[max(tp,pos-6):pos+1].min());at=float(r.atr14) if pd.notna(r.atr14) else float(r.close)*.01
          tsl=recent_low-.25*at
          if not (float(r.close)>tsl>0):continue
          sp=safety(float(r.close),tsl);rval=rr(float(r.close),tsl,p['anchor_high'])
          for gn,g in GRIDS.items():
            core=zq>=g['zone'] and fear and ex>=g['ex'] and er>=g['er'] and ind>=g['indep'] and nc and sp>=85
            if not core:continue
            for rrt in [0,2,2.5,3,4]:
              key=f'{gn}_tactical_rr{str(rrt).replace(".","p")}'
              if pd.isna(rec[f'{key}_time']) and (rrt==0 or rval>=rrt):
                rec[f'{key}_time']=h.index[pos];rec[f'{key}_price']=float(r.close);rec[f'{key}_sl']=tsl;rec[f'{key}_rr']=rval
        for gn in GRIDS:
          for rrt in [0,2,2.5,3,4]:
            key=f'{gn}_tactical_rr{str(rrt).replace(".","p")}'
            if pd.notna(rec[f'{key}_time']):
              o=outcome(h,rec[f'{key}_time'],float(rec[f'{key}_price']),float(rec[f'{key}_sl']),p['anchor_high'],p['expiry'])
              for k,v in o.items():rec[f'{key}_{k}']=v
        rows.append(rec)
    df=pd.DataFrame(rows);res={'schema':'MASTER_BTC_TREND_V2.5_TACTICAL_SENSITIVITY','generated_utc':datetime.now(timezone.utc).isoformat(),'rows':int(len(h)),'plans':int(len(plans)),'events':int(len(df)),'download_failures':fail,'definition':'Tactical SL = lowest low from touch/last 6h through signal minus 0.25×1H ATR; target=pre-registered prior structural high; signal window=72h; SL-first conservative','grids':{}}
    for gn in GRIDS:
      res['grids'][gn]={}
      for rrt in [0,2,2.5,3,4]:
        key=f'{gn}_tactical_rr{str(rrt).replace(".","p")}'
        res['grids'][gn][str(rrt)]=summary(df,key)
    (OUT/'v25_tactical_summary.json').write_text(json.dumps(res,ensure_ascii=False,indent=2,default=str),encoding='utf-8');df.to_csv(OUT/'v25_tactical_events.csv',index=False)
    lines=['# V2.5 Tactical Invalidation Sensitivity','',f"- {res['definition']}",'','| Grid | RR min | Signals | SL-first | TP1-first | Median RR | +24h | +72h | MFE | MAE |','|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for gn in GRIDS:
      for rrn,s in res['grids'][gn].items():lines.append(f"| {gn} | {rrn} | {s.get('signals',0)} | {s.get('sl_first_rate_pct','N/A')} | {s.get('tp1_first_rate_pct','N/A')} | {s.get('median_rr','N/A')} | {s.get('median_ret24h_pct','N/A')} | {s.get('median_ret72h_pct','N/A')} | {s.get('median_mfe_pct','N/A')} | {s.get('median_mae_pct','N/A')} |")
    (OUT/'v25_tactical_report.md').write_text('\n'.join(lines),encoding='utf-8');print(json.dumps(res,ensure_ascii=False,indent=2,default=str))

if __name__=='__main__':run()
