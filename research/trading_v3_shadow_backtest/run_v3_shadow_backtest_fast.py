from __future__ import annotations
import importlib.util
from pathlib import Path
import numpy as np
import pandas as pd

P=Path(__file__).with_name('run_v3_shadow_backtest.py')
spec=importlib.util.spec_from_file_location('v3base',P)
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def asof_fast(df,t):
    j=int(df.index.searchsorted(t,side='left'))-1
    return None if j<0 else df.iloc[j]


def fib_fast(h4,t,direction,lookback=24):
    j=int(h4.index.searchsorted(t,side='left'))
    z=h4.iloc[max(0,j-lookback):j]
    if len(z)<8:return None
    hi_idx=z.high.idxmax(); lo_idx=z.low.idxmin(); hi=float(z.loc[hi_idx,'high']); lo=float(z.loc[lo_idx,'low'])
    if hi<=lo:return None
    if direction=='LONG' and not(lo_idx<hi_idx):return None
    if direction=='SHORT' and not(hi_idx<lo_idx):return None
    ratios=[0.382,0.5,0.618,0.786]
    if direction=='LONG': levels={r:hi-r*(hi-lo) for r in ratios}; ext={1.272:hi+.272*(hi-lo),1.618:hi+.618*(hi-lo)}
    else: levels={r:lo+r*(hi-lo) for r in ratios}; ext={1.272:lo-.272*(hi-lo),1.618:lo-.618*(hi-lo)}
    return {'lo':lo,'hi':hi,'levels':levels,'ext':ext,'lo_idx':str(lo_idx),'hi_idx':str(hi_idx)}


def avwap_fast(h,t,direction,lookback=48):
    j=int(h.index.searchsorted(t,side='left'))
    z=h.iloc[max(0,j-lookback):j]
    if len(z)<12:return np.nan
    anchor=z.low.idxmin() if direction=='LONG' else z.high.idxmax(); q=z.loc[anchor:]
    typ=(q.high+q.low+q.close)/3; den=q.volume.sum()
    return float((typ*q.volume).sum()/den) if den>0 else np.nan


def replay_fast(cands,m30,h1):
    rows=[]; idx=m30.index
    for s in cands:
        a=int(idx.searchsorted(s['sig'],side='left')); b=int(idx.searchsorted(s['sig']+pd.Timedelta(hours=m.WAIT_HOURS),side='left'))
        if b<=a:continue
        z=m30.iloc[a:b]; mask=(z.low<=s['entry'])&(z.high>=s['entry'])
        if not mask.any():continue
        p=a+int(np.argmax(mask.values)); touch_idx=idx[p]
        trig=None; delay=None; rscore=np.nan
        for k in range(p,min(len(m30)-1,p+m.TRIGGER_SEARCH_BARS+1)):
            bar=m30.iloc[k]; nxt=m30.iloc[k+1]
            if s['direction']=='LONG' and bar.low<=s['stop']:break
            if s['direction']=='SHORT' and bar.high>=s['stop']:break
            if s['direction']=='LONG': ok=bar.close>s['entry'] and bar.close>bar.open and bar.low<=s['entry']+.25*s['risk'] and nxt.low>=bar.low
            else: ok=bar.close<s['entry'] and bar.close<bar.open and bar.high>=s['entry']-.25*s['risk'] and nxt.high<=bar.high
            if ok:
                trig=k; delay=k-p; r1=asof_fast(h1,idx[k]+pd.Timedelta(minutes=30)); rscore=m.reaction_score(bar,nxt,r1,s['direction'],delay) if r1 is not None else np.nan; break
        if trig is None:continue
        fill=None
        for k in range(trig+1,min(len(m30),trig+1+m.RETEST_SEARCH_BARS)):
            bar=m30.iloc[k]
            if s['direction']=='LONG' and bar.low<=s['stop']:break
            if s['direction']=='SHORT' and bar.high>=s['stop']:break
            if bar.low<=s['entry']<=bar.high:fill=k;break
        if fill is None:
            zz=m30.iloc[trig+1:min(len(m30),trig+25)]
            miss=(zz.high.max()>=s['entry']+s['risk']) if s['direction']=='LONG' else (zz.low.min()<=s['entry']-s['risk'])
            rows.append({**s,'touch':str(touch_idx),'triggered':True,'filled':False,'missed_move':bool(miss),'delay30':delay,'reaction_score':rscore,'total_score':(s['pre_score']+rscore if pd.notna(s['pre_score']) else np.nan)}); continue
        zz=m30.iloc[fill:min(len(m30),fill+m.TRADE_HORIZON_BARS+1)]
        if zz.empty:continue
        if s['direction']=='LONG':
            sh=np.where(zz.low.values<=s['stop'])[0]; t1=np.where(zz.high.values>=s['entry']+s['risk'])[0]; t2=np.where(zz.high.values>=s['entry']+2*s['risk'])[0]; t3=np.where(zz.high.values>=s['tp'])[0]; mfe=(zz.high.max()-s['entry'])/s['risk']; mae=(s['entry']-zz.low.min())/s['risk']; terminal=(zz.close.iloc[-1]-s['entry'])/s['risk']
        else:
            sh=np.where(zz.high.values>=s['stop'])[0]; t1=np.where(zz.low.values<=s['entry']-s['risk'])[0]; t2=np.where(zz.low.values<=s['entry']-2*s['risk'])[0]; t3=np.where(zz.low.values<=s['tp'])[0]; mfe=(s['entry']-zz.low.min())/s['risk']; mae=(zz.high.max()-s['entry'])/s['risk']; terminal=(s['entry']-zz.close.iloc[-1])/s['risk']
        sp=int(sh[0]) if len(sh) else None; q=int(t3[0]) if len(t3) else None
        if q is not None and (sp is None or q<sp):R=3.; label='TP3'
        elif sp is not None and (q is None or sp<q):R=-1.; label='SL'
        else:R=float(np.clip(terminal,-1,3)); label='TIME'
        total=(s['pre_score']+rscore) if pd.notna(s['pre_score']) else np.nan
        rows.append({**s,'touch':str(touch_idx),'triggered':True,'filled':True,'missed_move':False,'delay30':delay,'reaction_score':rscore,'total_score':total,'fill_time':str(idx[fill]),'R':R,'label':label,'MFE':float(mfe),'MAE':float(mae),'tp1_hit':bool(len(t1)),'tp2_hit':bool(len(t2)),'tp3_hit':bool(len(t3)),'false_start':bool(label=='SL' and sp is not None and sp<=8)})
    return pd.DataFrame(rows)

m.asof=asof_fast; m.last_impulse_fib=fib_fast; m.anchored_vwap=avwap_fast; m.replay=replay_fast
m.main()
