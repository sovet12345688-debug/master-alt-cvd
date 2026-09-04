from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

import run_phase2_v03 as p2

OUT=Path('btc_trend_v30/output/phase3_v01'); OUT.mkdir(parents=True,exist_ok=True)


def clip(v): return float(max(0.0,min(100.0,float(v))))


def build_regime_frame(D,x):
    z=x.copy()
    # Point-in-time regime only. No future data is used to start/continue an episode.
    z['long_regime']=((z.close>z.ema20)&(z.ema20>z.sma50)&(z.sma50>z.sma200)&(z.ema20_slope5>0)).astype(int)
    z['short_regime']=((z.close<z.ema20)&(z.ema20<z.sma50)&(z.sma50<z.sma200)&(z.ema20_slope5<0)).astype(int)
    out=[]
    for side,col in [('LONG','long_regime'),('SHORT','short_regime')]:
        start=None; start_price=None; age=0
        for t,r in z.iterrows():
            active=int(r[col])==1
            if active and start is None:
                start=t; start_price=float(r.close); age=0
            elif active:
                age=(t-start).days
            else:
                start=None; start_price=None; age=0
            if not active: continue
            move=(float(r.close)/start_price-1) if side=='LONG' else (start_price/float(r.close)-1)
            ext20=(float(r.close)/float(r.ema20)-1) if side=='LONG' else (float(r.ema20)/float(r.close)-1)
            ext50=(float(r.close)/float(r.sma50)-1) if side=='LONG' else (float(r.sma50)/float(r.close)-1)
            hot=(max(0,float(r.rsi14)-68)/20) if side=='LONG' else (max(0,32-float(r.rsi14))/20)
            # Energy deceleration: established trend but recent 3D impulse no longer confirms 30D trend.
            if side=='LONG':
                decel=clip((max(0,float(r.ret30))*2.2 + max(0,-float(r.ret3))*4.0)*100)
                oppose=float(r.candle_s); exhaust=float(r.wave_s)
                flowfail=100.0 if (r.ret30>0 and r.taker_imb_3<r.taker_imb_6) else 35.0
            else:
                decel=clip((max(0,-float(r.ret30))*2.2 + max(0,float(r.ret3))*4.0)*100)
                oppose=float(r.candle_l); exhaust=float(r.wave_l)
                flowfail=100.0 if (r.ret30<0 and r.taker_imb_3>r.taker_imb_6) else 35.0
            volblow=clip(50+22*float(r.vol_z))
            raw=clip(.18*clip(age/180*100)+.19*clip(move/.55*100)+.15*clip(ext20/.14*100)+.11*clip(ext50/.28*100)+.10*clip(hot*100)+.10*decel+.06*oppose+.05*exhaust+.04*flowfail+.02*volblow)
            out.append({'time':t,'side':side,'price':float(r.close),'regime_start':start,'age_days':age,'move_since_start_pct':100*move,'raw_maturity':raw,
                        'ext20_pct':100*ext20,'ext50_pct':100*ext50,'rsi':float(r.rsi14),'deceleration':decel,'opposing_candle':oppose,'opposing_wave':exhaust,'flow_failure':flowfail,'volume_blowoff':volblow})
    R=pd.DataFrame(out); R['time']=pd.to_datetime(R.time,utc=True); R['regime_start']=pd.to_datetime(R.regime_start,utc=True)
    return R


def resolve_state(D,t,p,side,horizon=120):
    # State outcome is deliberately symmetric enough to test maturity discrimination.
    if side=='LONG': fav=p*1.20; adverse=p*.85
    else: fav=p*.80; adverse=p*1.15
    fut=D[(D.index>t)&(D.index<=t+pd.Timedelta(days=horizon))]
    maxfav=0.0
    for rt,r in fut.iterrows():
        if side=='LONG':
            maxfav=max(maxfav,float(r.high/p-1)); a=r.high>=fav; b=r.low<=adverse
        else:
            maxfav=max(maxfav,float(1-r.low/p)); a=r.low<=fav; b=r.high>=adverse
        if a and b:return np.nan,rt,maxfav
        if b:return 1.0,rt,maxfav  # termination/reversal first
        if a:return 0.0,rt,maxfav  # continuation first
    return np.nan,(fut.index.max() if len(fut) else pd.NaT),maxfav


def build_samples(D,R):
    rows=[]
    for side,g in R.groupby('side'):
        last=None
        for _,r in g[g.time.dt.year>=2018].sort_values('time').iterrows():
            t=pd.Timestamp(r.time)
            if last is not None and (t-last).days<21: continue
            last=t
            o,rt,mfe=resolve_state(D,t,float(r.price),side)
            q=r.to_dict(); q.update({'outcome_termination':o,'resolved_time':rt,'remaining_favorable_mfe_pct':100*mfe}); rows.append(q)
    S=pd.DataFrame(rows); S['time']=pd.to_datetime(S.time,utc=True); S['resolved_time']=pd.to_datetime(S.resolved_time,utc=True)
    return S

FEATURES=['age_days','move_since_start_pct','ext20_pct','ext50_pct','rsi','deceleration','opposing_candle','opposing_wave','flow_failure','volume_blowoff']


def feature_matrix(frame,side):
    X=frame[FEATURES].astype(float).copy()
    X['age_days']=np.clip(X.age_days/180*100,0,100)
    X['move_since_start_pct']=np.clip(X.move_since_start_pct/55*100,0,100)
    X['ext20_pct']=np.clip(X.ext20_pct/14*100,0,100); X['ext50_pct']=np.clip(X.ext50_pct/28*100,0,100)
    if side=='LONG': X['rsi']=np.clip((X.rsi-45)/40*100,0,100)
    else: X['rsi']=np.clip((55-X.rsi)/40*100,0,100)
    A=X.fillna(50).values/100.0
    return np.c_[A,A[:,0]*A[:,1],A[:,1]*A[:,2],A[:,4]*A[:,5]]


def fit(train,side,l2=5.0):
    X=feature_matrix(train,side); y=train.outcome_termination.astype(float).values; mu=X.mean(0); sd=X.std(0); sd[sd<1e-6]=1; Z=(X-mu)/sd; Z=np.c_[np.ones(len(Z)),Z]; w=np.zeros(Z.shape[1])
    for _ in range(1200):
        a=np.clip(Z@w,-16,16); p=1/(1+np.exp(-a)); g=Z.T@(p-y)/len(y); g[1:]+=l2*w[1:]/len(y); w-=.035*g
    return mu,sd,w


def pred(frame,side,m):
    mu,sd,w=m; X=feature_matrix(frame,side); Z=(X-mu)/sd; Z=np.c_[np.ones(len(Z)),Z]; return 1/(1+np.exp(-np.clip(Z@w,-16,16)))


def oos(S,side):
    E=S[(S.side==side)].copy(); outs=[]; yrs=[]
    for y in range(2020,2027):
        cut=pd.Timestamp(f'{y}-01-01',tz='UTC'); tr=E[(E.resolved_time<cut)&E.outcome_termination.notna()].copy(); te=E[(E.time.dt.year==y)&E.outcome_termination.notna()].copy()
        if len(tr)<50 or len(te)<5:continue
        m=fit(tr,side); ptr=pred(tr,side,m); pte=pred(te,side,m); te['maturity_score']=100*np.searchsorted(np.sort(ptr),pte,side='right')/len(ptr); outs.append(te); yrs.append({'year':y,'train_n':len(tr),'test_n':len(te)})
    O=pd.concat(outs,ignore_index=True) if outs else pd.DataFrame(); buckets=[]
    if O.empty:return O,{'pass':False,'buckets':[],'years':yrs}
    for lo,hi,label in [(0,24,'EARLY'),(25,49,'DEVELOPING'),(50,74,'MATURE'),(75,100,'TERMINATION_BOUNDARY')]:
        g=O[(O.maturity_score>=lo)&(O.maturity_score<=hi)]; buckets.append({'stage':label,'n':len(g),'termination_first_pct':round(float(g.outcome_termination.mean()*100),2) if len(g) else None,'median_remaining_favorable_mfe_pct':round(float(g.remaining_favorable_mfe_pct.median()),2) if len(g) else None})
    low=O[O.maturity_score<50]; high=O[O.maturity_score>=75]
    gap=round(float((high.outcome_termination.mean()-low.outcome_termination.mean())*100),2) if len(low) and len(high) else None
    mfegap=round(float(low.remaining_favorable_mfe_pct.median()-high.remaining_favorable_mfe_pct.median()),2) if len(low) and len(high) else None
    populated=[b for b in buckets if b['n']>=10 and b['termination_first_pct'] is not None]
    mono=all(populated[i+1]['termination_first_pct']+5>=populated[i]['termination_first_pct'] for i in range(len(populated)-1)) if len(populated)>=3 else False
    pas=bool(len(high)>=15 and gap is not None and gap>=12 and mono)
    return O,{'pass':pas,'buckets':buckets,'low_n':len(low),'high_n':len(high),'high_minus_low_termination_pp':gap,'low_minus_high_remaining_mfe_pp':mfegap,'monotonic_with_5pp_tolerance':mono,'years':yrs}


def main():
    D,x,T,fail=p2.prepare(); R=build_regime_frame(D,x); S=build_samples(D,R); S.to_csv(OUT/'trend_state_samples.csv',index=False)
    lo,ls=oos(S,'LONG'); so,ss=oos(S,'SHORT'); lo.to_csv(OUT/'oos_long.csv',index=False); so.to_csv(OUT/'oos_short.csv',index=False)
    d={'engine':'MASTER_BTC_TREND_V3_TREND_STATE_V0_1','status':'RESEARCH_ONLY_NOT_LIVE','data_failures':fail,'state_definition':'EARLY 0-24 / DEVELOPING 25-49 / MATURE 50-74 / TERMINATION_BOUNDARY 75-100; score is prior-year OOS rank, not probability','outcome':'within 120D, adverse 15% reversal before favorable 20% continuation','LONG':ls,'SHORT':ss,'overall_stage':'PHASE3_V01_PASS' if ls['pass'] and ss['pass'] else 'RESEARCH_PARTIAL_OR_FAIL','master_btc_trend_modified':False,'integration':'FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL','anti_leakage':'test-year model trains only on samples whose outcome resolved before Jan 1; all features are point-in-time; score is percentile vs prior training distribution'}
    (OUT/'summary.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(d,ensure_ascii=False))

if __name__=='__main__':main()
