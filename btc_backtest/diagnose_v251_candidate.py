from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import run_v25_backtest as bt

OUT=Path('btc_backtest/output');OUT.mkdir(parents=True,exist_ok=True)
CANDS={
 'A_strict_pre':{'zone':82,'ex':65,'pre':60,'ind':2},
 'B_balanced_pre':{'zone':80,'ex':60,'pre':55,'ind':2},
 'C_deep_pre':{'zone':80,'ex':60,'pre':55,'ind':2,'zones':[2,3]},
}

def safety(e,s):
    if not e>s>0:return 0
    d=(e-s)/e;return 90 if d<=.12 else (85 if d<=.15 else (80 if d<=.18 else 70))
def persist(h,pos,z):
    r=h.iloc[pos];return bool((pos>=1 and r.close>=z['center'] and h.close.iloc[pos-1]>=z['center']) or (r.close>z['high'] and pos>=2 and h.low.iloc[pos-2:pos+1].min()>=z['low']*.997))
def pre_score(h,pos,z,ex):
    if pos<6:return 0,0
    r=h.iloc[pos];prev=h.iloc[pos-6:pos];prev_low=float(prev.low.min());score=0;gs=[]
    # No requirement for full zone reclaim / Price Persistence here.
    price_def=(r.low<=prev_low*1.002 and r.close>r.low+(r.high-r.low)*.45) or (r.low<=z['high'] and r.close>=z['low'])
    if price_def:score+=25;gs.append('price_defense')
    rsi_turn=(r.rsi14<=42 and r.rsi14>=h.rsi14.iloc[pos-2]+3) or (r.low<=prev_low*1.002 and r.rsi14>=float(prev.rsi14.min())+4)
    if rsi_turn:score+=20;gs.append('momentum_turn')
    wick=bool(r.lower_wick_ratio>=.25)
    if wick:score+=20;gs.append('lower_wick')
    prev3=h.ret3.iloc[pos-3] if pd.notna(h.ret3.iloc[pos-3]) else np.nan;speed=bool(pd.notna(prev3) and pd.notna(r.ret3) and prev3<-.006 and r.ret3>prev3+.006)
    if speed:score+=15;gs.append('speed_decel')
    vol=bool(pd.notna(r.vol_z) and r.vol_z>=.8 and r.close_loc>=.50)
    if vol:score+=10;gs.append('volume_absorb')
    tak=bool(pd.notna(r.taker_imb6) and r.taker_imb6<=-.04 and r.close>=z['low'])
    if tak:score+=10;gs.append('taker_price_div')
    # exhaustion is context but not duplicated into pre-score.
    return score,len(set(gs))
def outcome(h,t,e,sl,target,expiry):
    fut=h[(h.index>=t)&(h.index<=min(expiry,t+pd.Timedelta(days=30)))];o={}
    if fut.empty:return o
    for n in [4,24,72]:o[f'ret{n}h']=float(fut.close.iloc[min(n,len(fut)-1)]/e-1)
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
        for n in CANDS:rec[f'{n}_time']=pd.NaT;rec[f'{n}_price']=np.nan
        rec['fast_time']=pd.NaT;rec['fast_price']=np.nan
        end=min(len(h)-1,tp+72)
        for pos in range(tp,end+1):
          r=h.iloc[pos];fear=sum(bt.fear_flags(h,pos).values())>=2;ex=bt.exhaust_score(h,pos,z);pre,ig=pre_score(h,pos,z,ex);sp=safety(float(r.close),p['sl']);nc=bt.nonchase(r);re=bt.reaction_score(h,pos,z,dr,ex)
          for name,c in CANDS.items():
            z_ok=('zones' not in c or z['n'] in c['zones'])
            if pd.isna(rec[f'{name}_time']) and z_ok and zq>=c['zone'] and fear and ex>=c['ex'] and pre>=c['pre'] and ig>=c['ind'] and sp>=85 and nc:
              rec[f'{name}_time']=h.index[pos];rec[f'{name}_price']=float(r.close);rec[f'{name}_pre_score']=pre;rec[f'{name}_ex']=ex
          if pd.isna(rec['fast_time']) and zq>=80 and r.close>z['high'] and persist(h,pos,z) and re>=65 and sp>=80 and nc:
            rec['fast_time']=h.index[pos];rec['fast_price']=float(r.close)
        for name in CANDS:
          et=rec[f'{name}_time']
          if pd.notna(et):
            o=outcome(h,et,float(rec[f'{name}_price']),p['sl'],p['anchor_high'],p['expiry'])
            for k,v in o.items():rec[f'{name}_{k}']=v
            if pd.notna(rec['fast_time']):rec[f'{name}_lead_h']=(rec['fast_time']-et).total_seconds()/3600
        if pd.notna(rec['fast_time']):
          o=outcome(h,rec['fast_time'],float(rec['fast_price']),p['sl'],p['anchor_high'],p['expiry'])
          for k,v in o.items():rec[f'fast_{k}']=v
        rows.append(rec)
    df=pd.DataFrame(rows);res={'schema':'MASTER_BTC_TREND_V2.5.1_PRECONF_CANDIDATE','generated_utc':datetime.now(timezone.utc).isoformat(),'events':int(len(df)),'definition':'Candidate EARLY intentionally removes Price Persistence/full reclaim/R:R and uses common Final Structural SL + plan-level risk-cap concept. This is TEST ONLY, not MASTER change.','candidates':{}}
    for name in CANDS:
      sig=df[df[f'{name}_time'].notna()];pair=sig[sig.fast_time.notna()];before=pair[pair[f'{name}_lead_h']>0];same=pair[pair[f'{name}_lead_h']==0];after=pair[pair[f'{name}_lead_h']<0]
      s={'signals':int(len(sig)),'paired_fast':int(len(pair)),'early_before_fast':int(len(before)),'same_time':int(len(same)),'early_after_fast':int(len(after))}
      if len(before):s['median_lead_h']=round(float(before[f'{name}_lead_h'].median()),1)
      if len(sig):
        s['sl_first_pct']=round(100*(sig[f'{name}_first_hit']=='SL').mean(),1);s['tp1_first_pct']=round(100*(sig[f'{name}_first_hit']=='TP1').mean(),1)
        for k in ['ret4h','ret24h','ret72h','mfe','mae']:s[f'median_{k}_pct']=round(100*sig[f'{name}_{k}'].median(),2)
      res['candidates'][name]=s
    fs=df[df.fast_time.notna()];res['fast']={'signals':int(len(fs))}
    if len(fs):res['fast'].update({'sl_first_pct':round(100*(fs.fast_first_hit=='SL').mean(),1),'tp1_first_pct':round(100*(fs.fast_first_hit=='TP1').mean(),1),'median_24h_pct':round(100*fs.fast_ret24h.median(),2),'median_72h_pct':round(100*fs.fast_ret72h.median(),2)})
    (OUT/'v251_candidate_summary.json').write_text(json.dumps(res,ensure_ascii=False,indent=2,default=str),encoding='utf-8');df.to_csv(OUT/'v251_candidate_events.csv',index=False);print(json.dumps(res,ensure_ascii=False,indent=2,default=str))

if __name__=='__main__':run()
