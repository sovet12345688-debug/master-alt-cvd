from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import run_v25_backtest as bt

OUT=Path('btc_backtest/output');OUT.mkdir(parents=True,exist_ok=True)
GRIDS={'strict':(82,70,75,3),'moderate':(80,65,70,3),'loose':(78,60,65,2)}

def safety(e,s):
    if not e>s>0:return 0
    d=(e-s)/e;return 90 if d<=.12 else (85 if d<=.15 else (80 if d<=.18 else 70))
def indep(h,pos,z,er):
    r=h.iloc[pos];p3=h.ret3.iloc[pos-3] if pos>=3 and pd.notna(h.ret3.iloc[pos-3]) else np.nan
    return sum(bool(x) for x in [
      (r.low<=z['high'] and r.close>=z['center']) or r.close>z['high'],
      pd.notna(r.vol_z) and r.vol_z>=1 and r.close_loc>=.55,
      pd.notna(r.taker_imb6) and r.taker_imb6<=-.05 and r.close>=z['center'],
      r.rsi14<=38 and er>=70,
      pd.notna(p3) and pd.notna(r.ret3) and r.ret3>p3+.008,
      r.lower_wick_ratio>=.30 or r.close>z['high']])
def persist(h,pos,z):
    r=h.iloc[pos];return bool((pos>=1 and r.close>=z['center'] and h.close.iloc[pos-1]>=z['center']) or (r.close>z['high'] and pos>=2 and h.low.iloc[pos-2:pos+1].min()>=z['low']*.997))
def outcome(h,t,e,sl,target,expiry):
    fut=h[(h.index>=t)&(h.index<=min(expiry,t+pd.Timedelta(days=30)))];o={}
    if fut.empty:return o
    for n in [24,72]:o[f'ret{n}h']=float(fut.close.iloc[min(n,len(fut)-1)]/e-1)
    o['mfe']=float(fut.high.max()/e-1);o['mae']=float(fut.low.min()/e-1);o['first_hit']='none'
    for _,b in fut.iterrows():
      if b.low<=sl:o['first_hit']='SL';break
      if b.high>=target:o['first_hit']='TP1';break
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
        touch=hits.index[0];tp=ht.get_loc(touch);zq=bt.zone_quality(p,z,h.iloc[tp],dr,wr,r4);rec={'plan':p['id'],'zone':z['n'],'touch':touch,'zq':zq,'sl':p['sl'],'target':p['anchor_high']}
        for g in GRIDS:rec[f'{g}_early_time']=pd.NaT;rec[f'{g}_early_price']=np.nan
        rec['fast_time']=pd.NaT;rec['fast_price']=np.nan
        end=min(len(h)-1,tp+72)
        for pos in range(tp,end+1):
          r=h.iloc[pos];fear=sum(bt.fear_flags(h,pos).values())>=2;ex=bt.exhaust_score(h,pos,z);er=bt.early_score(h,pos,z,ex);re=bt.reaction_score(h,pos,z,dr,ex);sp=safety(float(r.close),p['sl']);nc=bt.nonchase(r);ii=indep(h,pos,z,er)
          for gn,(gz,gx,ge,gi) in GRIDS.items():
            if pd.isna(rec[f'{gn}_early_time']) and zq>=gz and fear and ex>=gx and er>=ge and ii>=gi and sp>=85 and nc:
              rec[f'{gn}_early_time']=h.index[pos];rec[f'{gn}_early_price']=float(r.close)
          if pd.isna(rec['fast_time']) and zq>=80 and r.close>z['high'] and persist(h,pos,z) and re>=65 and sp>=80 and nc:
            rec['fast_time']=h.index[pos];rec['fast_price']=float(r.close)
        if pd.notna(rec['fast_time']):
          oo=outcome(h,rec['fast_time'],float(rec['fast_price']),p['sl'],p['anchor_high'],p['expiry'])
          for k,v in oo.items():rec[f'fast_{k}']=v
        for gn in GRIDS:
          et=rec[f'{gn}_early_time']
          if pd.notna(et):
            oo=outcome(h,et,float(rec[f'{gn}_early_price']),p['sl'],p['anchor_high'],p['expiry'])
            for k,v in oo.items():rec[f'{gn}_{k}']=v
            if pd.notna(rec['fast_time']):rec[f'{gn}_lead_h']=(rec['fast_time']-et).total_seconds()/3600
        rows.append(rec)
    df=pd.DataFrame(rows);res={'schema':'MASTER_BTC_TREND_V2.5_STAGE_ORDER','generated_utc':datetime.now(timezone.utc).isoformat(),'events':int(len(df)),'grids':{}}
    for gn in GRIDS:
      sig=df[df[f'{gn}_early_time'].notna()];pair=sig[sig.fast_time.notna()];before=pair[pair[f'{gn}_lead_h']>0];same=pair[pair[f'{gn}_lead_h']==0];after=pair[pair[f'{gn}_lead_h']<0]
      s={'early_signals':int(len(sig)),'paired_with_fast':int(len(pair)),'early_before_fast':int(len(before)),'same_time':int(len(same)),'early_after_fast':int(len(after))}
      if len(before):s['median_lead_hours_when_early_first']=round(float(before[f'{gn}_lead_h'].median()),1)
      if len(sig):
        s['sl_first_pct']=round(100*(sig[f'{gn}_first_hit']=='SL').mean(),1);s['tp1_first_pct']=round(100*(sig[f'{gn}_first_hit']=='TP1').mean(),1);s['median_24h_pct']=round(100*sig[f'{gn}_ret24h'].median(),2);s['median_72h_pct']=round(100*sig[f'{gn}_ret72h'].median(),2);s['median_mfe_pct']=round(100*sig[f'{gn}_mfe'].median(),2);s['median_mae_pct']=round(100*sig[f'{gn}_mae'].median(),2)
      res['grids'][gn]=s
    fs=df[df.fast_time.notna()];res['fast']={'signals':int(len(fs))}
    if len(fs):res['fast'].update({'sl_first_pct':round(100*(fs.fast_first_hit=='SL').mean(),1),'tp1_first_pct':round(100*(fs.fast_first_hit=='TP1').mean(),1),'median_24h_pct':round(100*fs.fast_ret24h.median(),2),'median_72h_pct':round(100*fs.fast_ret72h.median(),2)})
    (OUT/'v25_stage_order_summary.json').write_text(json.dumps(res,ensure_ascii=False,indent=2,default=str),encoding='utf-8');df.to_csv(OUT/'v25_stage_order_events.csv',index=False);print(json.dumps(res,ensure_ascii=False,indent=2,default=str))

if __name__=='__main__':run()
