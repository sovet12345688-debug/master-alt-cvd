from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

import run_phase3_trend_state as v01
import run_phase2_v03 as p2

OUT=Path('btc_trend_v30/output/phase3_v02'); OUT.mkdir(parents=True,exist_ok=True)


def clip(v): return float(max(0.0,min(100.0,float(v))))


def build_regime_frame(D,x):
    z=x.copy()
    # Broader but still directional point-in-time trend definition: 3 of 5 conditions.
    long_votes=(z.close>z.ema20).astype(int)+(z.close>z.sma50).astype(int)+(z.sma50>z.sma200).astype(int)+(z.ema20_slope5>0).astype(int)+(z.ret60>0).astype(int)
    short_votes=(z.close<z.ema20).astype(int)+(z.close<z.sma50).astype(int)+(z.sma50<z.sma200).astype(int)+(z.ema20_slope5<0).astype(int)+(z.ret60<0).astype(int)
    z['long_regime']=(long_votes>=3).astype(int); z['short_regime']=(short_votes>=3).astype(int)
    out=[]
    for side,col in [('LONG','long_regime'),('SHORT','short_regime')]:
        start=None; start_price=None; miss=0
        for t,r in z.iterrows():
            active=int(r[col])==1
            if active:
                if start is None: start=t; start_price=float(r.close)
                miss=0
            elif start is not None:
                miss+=1
                # allow short neutral pullbacks inside one medium-term episode
                if miss<=5: active=True
                else: start=None; start_price=None; miss=0
            if not active or start is None: continue
            age=(t-start).days; move=(float(r.close)/start_price-1) if side=='LONG' else (start_price/float(r.close)-1)
            ext20=(float(r.close)/float(r.ema20)-1) if side=='LONG' else (float(r.ema20)/float(r.close)-1)
            ext50=(float(r.close)/float(r.sma50)-1) if side=='LONG' else (float(r.sma50)/float(r.close)-1)
            hot=(max(0,float(r.rsi14)-68)/20) if side=='LONG' else (max(0,32-float(r.rsi14))/20)
            if side=='LONG':
                decel=clip((max(0,float(r.ret30))*1.7+max(0,-float(r.ret3))*4.2)*100); oppose=float(r.candle_s); exhaust=float(r.wave_s); flowfail=100.0 if (r.ret30>0 and r.taker_imb_3<r.taker_imb_6) else 35.0
            else:
                decel=clip((max(0,-float(r.ret30))*1.7+max(0,float(r.ret3))*4.2)*100); oppose=float(r.candle_l); exhaust=float(r.wave_l); flowfail=100.0 if (r.ret30<0 and r.taker_imb_3>r.taker_imb_6) else 35.0
            volblow=clip(50+22*float(r.vol_z)); raw=clip(.18*clip(age/180*100)+.19*clip(move/.55*100)+.15*clip(max(0,ext20)/.14*100)+.11*clip(max(0,ext50)/.28*100)+.10*clip(hot*100)+.10*decel+.06*oppose+.05*exhaust+.04*flowfail+.02*volblow)
            out.append({'time':t,'side':side,'price':float(r.close),'regime_start':start,'age_days':age,'move_since_start_pct':100*move,'raw_maturity':raw,'ext20_pct':100*max(0,ext20),'ext50_pct':100*max(0,ext50),'rsi':float(r.rsi14),'deceleration':decel,'opposing_candle':oppose,'opposing_wave':exhaust,'flow_failure':flowfail,'volume_blowoff':volblow})
    R=pd.DataFrame(out); R['time']=pd.to_datetime(R.time,utc=True);R['regime_start']=pd.to_datetime(R.regime_start,utc=True);return R


def build_samples(D,R):
    rows=[]
    for side,g in R.groupby('side'):
        last=None; last_start=None
        for _,r in g[g.time.dt.year>=2018].sort_values('time').iterrows():
            t=pd.Timestamp(r.time); st=pd.Timestamp(r.regime_start)
            # one sample at episode start, then every 14 days while the same trend persists
            if last_start is not None and st==last_start and last is not None and (t-last).days<14: continue
            last=t; last_start=st; o,rt,mfe=v01.resolve_state(D,t,float(r.price),side)
            q=r.to_dict();q.update({'outcome_termination':o,'resolved_time':rt,'remaining_favorable_mfe_pct':100*mfe});rows.append(q)
    S=pd.DataFrame(rows);S['time']=pd.to_datetime(S.time,utc=True);S['resolved_time']=pd.to_datetime(S.resolved_time,utc=True);return S


def oos(S,side):
    E=S[S.side==side].copy();outs=[];yrs=[]
    for y in range(2020,2027):
        cut=pd.Timestamp(f'{y}-01-01',tz='UTC');tr=E[(E.resolved_time<cut)&E.outcome_termination.notna()].copy();te=E[(E.time.dt.year==y)&E.outcome_termination.notna()].copy()
        if len(tr)<30 or len(te)<6:continue
        m=v01.fit(tr,side);ptr=v01.pred(tr,side,m);pte=v01.pred(te,side,m);te['maturity_score']=100*np.searchsorted(np.sort(ptr),pte,side='right')/len(ptr);outs.append(te);yrs.append({'year':y,'train_n':len(tr),'test_n':len(te)})
    O=pd.concat(outs,ignore_index=True) if outs else pd.DataFrame();b=[]
    if O.empty:return O,{'pass':False,'buckets':[],'years':yrs}
    for lo,hi,label in [(0,24,'EARLY'),(25,49,'DEVELOPING'),(50,74,'MATURE'),(75,100,'TERMINATION_BOUNDARY')]:
        g=O[(O.maturity_score>=lo)&(O.maturity_score<=hi)];b.append({'stage':label,'n':len(g),'termination_first_pct':round(float(g.outcome_termination.mean()*100),2) if len(g) else None,'median_remaining_favorable_mfe_pct':round(float(g.remaining_favorable_mfe_pct.median()),2) if len(g) else None})
    low=O[O.maturity_score<50];high=O[O.maturity_score>=75];gap=round(float((high.outcome_termination.mean()-low.outcome_termination.mean())*100),2) if len(low) and len(high) else None;mfegap=round(float(low.remaining_favorable_mfe_pct.median()-high.remaining_favorable_mfe_pct.median()),2) if len(low) and len(high) else None
    pop=[q for q in b if q['n']>=8 and q['termination_first_pct'] is not None];mono=all(pop[i+1]['termination_first_pct']+6>=pop[i]['termination_first_pct'] for i in range(len(pop)-1)) if len(pop)>=3 else False
    pas=bool(len(high)>=12 and gap is not None and gap>=10 and mfegap is not None and mfegap>=0)
    return O,{'pass':pas,'buckets':b,'low_n':len(low),'high_n':len(high),'high_minus_low_termination_pp':gap,'low_minus_high_remaining_mfe_pp':mfegap,'monotonic_with_6pp_tolerance':mono,'years':yrs}


def main():
    D,x,T,fail=p2.prepare();R=build_regime_frame(D,x);S=build_samples(D,R);S.to_csv(OUT/'trend_state_samples.csv',index=False);lo,ls=oos(S,'LONG');so,ss=oos(S,'SHORT');lo.to_csv(OUT/'oos_long.csv',index=False);so.to_csv(OUT/'oos_short.csv',index=False)
    d={'engine':'MASTER_BTC_TREND_V3_TREND_STATE_V0_2','status':'RESEARCH_ONLY_NOT_LIVE','data_failures':fail,'design_changes':['trend episode = 3-of-5 directional conditions rather than full MA stack','allow up to 5 neutral pullback days inside an episode','sample episode start and every 14D, not arbitrary continuous rows','test-year score remains prior-year percentile, not probability'],'state_definition':'EARLY 0-24 / DEVELOPING 25-49 / MATURE 50-74 / TERMINATION_BOUNDARY 75-100','LONG':ls,'SHORT':ss,'overall_stage':'PHASE3_V02_PASS' if ls['pass'] and ss['pass'] else 'RESEARCH_PARTIAL_OR_FAIL','master_btc_trend_modified':False,'integration':'FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL','anti_leakage':'all features are point-in-time and test-year models train only on outcomes resolved before Jan1'}
    (OUT/'summary.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(d,ensure_ascii=False))
if __name__=='__main__':main()
