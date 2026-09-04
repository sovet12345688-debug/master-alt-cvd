from __future__ import annotations

import json, math
from pathlib import Path
import numpy as np
import pandas as pd

import run_phase2_four_engines as v01
import run_phase11_12_13 as b
import run_phase11_12_13_fast as f13
import run_phase14 as p14
import run_phase14_v04 as v04

OUT=Path('btc_trend_v30/output/phase2_v03'); OUT.mkdir(parents=True,exist_ok=True)


def wilson_lower(k,n,z=1.959963984540054):
    if n<=0:return None
    p=k/n; den=1+z*z/n; cen=p+z*z/(2*n); adj=z*math.sqrt((p*(1-p)+z*z/(4*n))/n)
    return (cen-adj)/den


def outcome(D,t,price,engine):
    if engine=='LONG_REVERSAL': horizon=180; good=price*1.30; bad=price*.85; side='LONG'; be=1/3
    elif engine=='SHORT_REVERSAL': horizon=180; good=price*.75; bad=price*1.15; side='SHORT'; be=.375
    elif engine=='LONG_CONTINUATION': horizon=120; good=price*1.20; bad=price*.88; side='LONG'; be=.375
    else: horizon=120; good=price*.80; bad=price*1.12; side='SHORT'; be=.375
    fut=D[(D.index>t)&(D.index<=t+pd.Timedelta(days=horizon))]
    for rt,r in fut.iterrows():
        if side=='LONG': a=r.high>=good; z=r.low<=bad
        else: a=r.low<=good; z=r.high>=bad
        if a and z:return np.nan,rt,be
        if a:return 1.0,rt,be
        if z:return 0.0,rt,be
    return np.nan,(fut.index.max() if len(fut) else pd.NaT),be


def prepare():
    D,H4,S,T,fail=b.prepare(); p11=b.phase11_scores(D,S); p12=f13.phase12_fast(D,p11); x=p14.add_turn_scores(p12)
    add=[c for c in ['open','high','low','ema20','sma50','sma200','ema20_slope5','rsi14','ret3','ret30','ret60','vol_z','taker_imb_3','taker_imb_6','close_loc','high20_prev','low20_prev','drawdown180','rally180'] if c in D.columns and c not in x.columns]
    if add:x=x.join(D[add],how='left')
    return D,x,T,fail


def reversal_events(D,x,T,side):
    eng='LONG_REVERSAL' if side=='LOW' else 'SHORT_REVERSAL'; rows=[]
    for y in range(2020,2027):
        tr=x[x.index.year<y]; te=x[x.index.year==y]; tt=T[T.time.dt.year<y]
        if len(tr)<500 or len(te)<100:continue
        pars=v04.choose_base(tr,tt,side); fw=v04.choose_fractal_weight(tr,tt,side,pars)
        sigs=v04.build_episode_signals(te,side,pars['ct'],pars['tt'],pars['cooldown'],fw)
        # quality percentile is formed only from prior-year signal qualities, not future outcomes
        train_sigs=v04.build_episode_signals(tr,side,pars['ct'],pars['tt'],pars['cooldown'],fw)
        tq=np.array([float(s['quality']) for s in train_sigs])
        for s in sigs:
            t=pd.Timestamp(s['confirm_time']); p=float(s['confirm_price']); o,rt,be=outcome(D,t,p,eng)
            score=50.0 if len(tq)<10 else 100*float(np.searchsorted(np.sort(tq),float(s['quality']),side='right')/len(tq))
            rows.append({'time':t,'engine':eng,'score':score,'outcome':o,'resolved_time':rt,'price':p,'break_even':be,'origin_time':s['origin_time'],'origin_price':s['origin_price'],'raw_quality':s['quality'],'fractal_weight':fw})
    return pd.DataFrame(rows)


def ma_score(r,side):
    if side=='LONG':return 25*(r.close>r.ema20)+20*(r.ema20>r.sma50)+20*(r.sma50>r.sma200)+15*(r.ema20_slope5>0)+20*(r.ret30>0)
    return 25*(r.close<r.ema20)+20*(r.ema20<r.sma50)+20*(r.sma50<r.sma200)+15*(r.ema20_slope5<0)+20*(r.ret30<0)


def continuation_candidate(r,side):
    if side=='LONG':
        trend=ma_score(r,'LONG'); retest=100 if (r.low<=r.ema20*1.025 and r.close>=r.ema20) else (85 if r.low<=r.sma50*1.025 and r.close>=r.sma50 else 0)
        force=.45*float(r.candle_l)+.25*float(r.momentum_l)+.20*float(r.absorb_l)+.10*float(r.wave_l)
        flow=100 if (r.taker_imb_3>r.taker_imb_6 and r.close_loc>.52) else 45
        hot=max(0,float(r.rsi14)-76)/14*100
    else:
        trend=ma_score(r,'SHORT'); retest=100 if (r.high>=r.ema20*.975 and r.close<=r.ema20) else (85 if r.high>=r.sma50*.975 and r.close<=r.sma50 else 0)
        force=.45*float(r.candle_s)+.25*float(r.momentum_s)+.20*float(r.absorb_s)+.10*float(r.wave_s)
        flow=100 if (r.taker_imb_3<r.taker_imb_6 and r.close_loc<.48) else 45
        hot=max(0,24-float(r.rsi14))/14*100
    return max(0,min(100,.38*trend+.28*retest+.20*force+.14*flow-.18*min(100,hot)))


def select_cont_params(train,D,side):
    eng='LONG_CONTINUATION' if side=='LONG' else 'SHORT_CONTINUATION'; best=None
    for th in [50,60,70]:
      for cd in [21,35,49]:
        sig=[]; last=None
        for t,r in train.iterrows():
            s=continuation_candidate(r,side)
            if s<th:continue
            if last is not None and (t-last).days<cd:continue
            o,rt,be=outcome(D,t,float(r.close),eng)
            if pd.isna(o):continue
            sig.append(o); last=t
        n=len(sig); p=float(np.mean(sig)) if n else 0; be=.375
        # prior-data objective: economic utility + enough events; no test-year use
        expectancy=p*(1/be-1)-(1-p) if n else -99
        obj=expectancy+min(n,25)/100
        z=(obj,n,p,-th,-cd,th,cd)
        if best is None or z>best:best=z
    return {'threshold':best[5],'cooldown':best[6]}


def continuation_events(D,x,side):
    eng='LONG_CONTINUATION' if side=='LONG' else 'SHORT_CONTINUATION'; rows=[]
    for y in range(2020,2027):
        tr=x[x.index.year<y]; te=x[x.index.year==y]
        if len(tr)<500 or len(te)<100:continue
        pars=select_cont_params(tr,D,side)
        train_scores=[]; last=None
        for t,r in tr.iterrows():
            s=continuation_candidate(r,side)
            if s<pars['threshold']:continue
            if last is not None and (t-last).days<pars['cooldown']:continue
            train_scores.append(s); last=t
        train_scores=np.array(train_scores)
        last=None
        for t,r in te.iterrows():
            s=continuation_candidate(r,side)
            if s<pars['threshold']:continue
            if last is not None and (t-last).days<pars['cooldown']:continue
            o,rt,be=outcome(D,t,float(r.close),eng)
            score=50.0 if len(train_scores)<10 else 100*float(np.searchsorted(np.sort(train_scores),s,side='right')/len(train_scores))
            rows.append({'time':t,'engine':eng,'score':score,'outcome':o,'resolved_time':rt,'price':float(r.close),'break_even':be,'raw_quality':s,'threshold':pars['threshold'],'cooldown':pars['cooldown']}); last=t
    return pd.DataFrame(rows)


def summarize(R):
    q=R[R.outcome.notna()].copy()
    if q.empty:return {'n':0,'status':'FAIL'}
    n=len(q); k=int(q.outcome.sum()); p=k/n; be=float(q.break_even.iloc[0]); lb=wilson_lower(k,n)
    hi=q[q.score>=65]; hk=int(hi.outcome.sum()) if len(hi) else 0; hp=hk/len(hi) if len(hi) else np.nan; hlb=wilson_lower(hk,len(hi)) if len(hi) else None
    # validated only when 95% lower bound clears break-even. promising is kept separate.
    status='VALIDATED' if n>=20 and lb is not None and lb>be else ('PROMISING' if n>=10 and p>=be+.05 else 'FAIL')
    return {'n':n,'wins':k,'success_pct':round(100*p,2),'break_even_pct':round(100*be,2),'wilson95_lower_pct':round(100*lb,2) if lb is not None else None,'status':status,'high_n':len(hi),'high_success_pct':round(100*hp,2) if len(hi) else None,'high_wilson95_lower_pct':round(100*hlb,2) if hlb is not None else None}


def main():
    D,x,T,fail=prepare(); parts=[reversal_events(D,x,T,'LOW'),reversal_events(D,x,T,'HIGH'),continuation_events(D,x,'LONG'),continuation_events(D,x,'SHORT')]; A=pd.concat(parts,ignore_index=True); A.to_csv(OUT/'event_samples.csv',index=False)
    stats={e:summarize(A[A.engine==e]) for e in ['LONG_REVERSAL','LONG_CONTINUATION','SHORT_REVERSAL','SHORT_CONTINUATION']}
    d={'engine':'MASTER_BTC_TREND_V3_PHASE2_EVENT_DRIVEN_V0_3','status':'RESEARCH_ONLY_NOT_LIVE','data_failures':fail,'design_changes':['reversal evaluated only on OOS Trend-Origin episodes','continuation evaluated only on MA-trend retest/reclaim episodes','episode cooldown replaces arbitrary 21D grid','acceptance benchmark uses each payoff ratio break-even and Wilson lower bound'],'stats':stats,'overall_stage':'PHASE2_EVENT_PROMISING' if sum(v['status'] in ('VALIDATED','PROMISING') for v in stats.values())>=2 else 'RESEARCH_PARTIAL_OR_FAIL','master_btc_trend_modified':False,'integration':'FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL','note':'PROMISING is not production validation. Position sizing remains blocked until reservation Zone utility/calibration is finalized.'}
    (OUT/'summary.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(d,ensure_ascii=False))

if __name__=='__main__':main()
