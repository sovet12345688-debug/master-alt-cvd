from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

import run_phase4_target_map as v01
import run_phase3_v02 as p3
import run_phase2_v03 as p2

OUT=Path('btc_trend_v30/output/phase4_v02'); OUT.mkdir(parents=True,exist_ok=True)


def build_registry(D,x):
    R=p3.build_regime_frame(D,x); rows=[]
    for side,g in R.groupby('side'):
        last=None; last_start=None
        for _,r in g[g.time.dt.year>=2018].sort_values('time').iterrows():
            t=pd.Timestamp(r.time); st=pd.Timestamp(r.regime_start)
            if last_start is not None and st==last_start and last is not None and (t-last).days<21: continue
            last=t;last_start=st;p=float(r.price);frcol='fractal_long_prior' if side=='LONG' else 'fractal_short_prior';xr=x.loc[:t].iloc[-1];fr=50.0 if pd.isna(xr.get(frcol,np.nan)) else float(xr.get(frcol));sp=float(D.loc[st].close)
            for c in v01.target_candidates(D,t,side,st,sp):
                prior,tc=v01.prior_reaction(D,t,side,c['center']);dist=abs(c['center']-p)/p;res=v01.resolve(D,t,p,side,c)
                rows.append({'time':t,'side':side,'price':p,'target_rank':c['rank'],'target':c['center'],'zone_low':c['low'],'zone_high':c['high'],'distance_pct':100*dist,
                             'confluence_score':v01.clip(c['confluence']/3*100),'source_importance':c['importance'],'prior_reversal_rate':prior,'prior_touch_count':tc,'fractal_path_score':fr,
                             'trend_age_days':float(r.age_days),'move_since_start_pct':float(r.move_since_start_pct),'raw_maturity':float(r.raw_maturity),'sources':'+'.join(c['sources']),**res})
    A=pd.DataFrame(rows)
    for c in ['time','reach_resolved','touch','reversal_resolved']:A[c]=pd.to_datetime(A[c],utc=True)
    return A


def oos(A,side,label,cols,rescol):
    E=A[A.side==side].copy();outs=[];yrs=[]
    for y in range(2020,2027):
        cut=pd.Timestamp(f'{y}-01-01',tz='UTC');tr=E[(E[rescol]<cut)&E[label].notna()].copy();te=E[(E.time.dt.year==y)&E[label].notna()].copy()
        if len(tr)<35 or len(te)<5:continue
        m=v01.fit(tr,cols,label);ptr=v01.pred(tr,cols,m);pte=v01.pred(te,cols,m);te['score']=100*np.searchsorted(np.sort(ptr),pte,side='right')/len(ptr);outs.append(te);yrs.append({'year':y,'train_n':len(tr),'test_n':len(te)})
    O=pd.concat(outs,ignore_index=True) if outs else pd.DataFrame();b=[]
    if O.empty:return O,{'pass':False,'buckets':[],'years':yrs}
    for lo,hi in [(0,49),(50,64),(65,79),(80,100)]:
        q=O[(O.score>=lo)&(O.score<=hi)];b.append({'bucket':f'{lo}-{hi}','n':len(q),'success_pct':round(float(q[label].mean()*100),2) if len(q) else None})
    low=O[O.score<65];high=O[O.score>=65];gap=round(float((high[label].mean()-low[label].mean())*100),2) if len(low) and len(high) else None
    pop=[q for q in b if q['n']>=8 and q['success_pct'] is not None];mono=all(pop[i+1]['success_pct']+6>=pop[i]['success_pct'] for i in range(len(pop)-1)) if len(pop)>=3 else False
    pas=bool(len(high)>=12 and gap is not None and gap>=10)
    return O,{'pass':pas,'buckets':b,'low_n':len(low),'high_n':len(high),'high_minus_low_pp':gap,'monotonic_with_6pp_tolerance':mono,'years':yrs}


def main():
    D,x,T,fail=p2.prepare();A=build_registry(D,x);A.to_csv(OUT/'target_registry.csv',index=False);d={'engine':'MASTER_BTC_TREND_V3_TARGET_MAP_V0_2','status':'RESEARCH_ONLY_NOT_LIVE','data_failures':fail,'design_changes':['use broader 3-of-5 point-in-time trend episodes from Phase3 V0.2','target snapshots every 21D inside each trend episode','lower modeling minimum only to 35 prior resolved examples; no test-year leakage'],'definitions':{'TARGET_REACH':'target zone reached before 15% adverse invalidation within 180D','TARGET_REVERSAL':'conditional on touch, 15% reversal before additional 10% extension within 90D'},'sides':{},'master_btc_trend_modified':False,'integration':'FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL'};passes=[]
    for side in ['LONG','SHORT']:
        ro,rs=oos(A,side,'reach',v01.REACH_F,'reach_resolved');vo,vs=oos(A[A.reach==1],side,'reversal',v01.REV_F,'reversal_resolved');ro.to_csv(OUT/f'oos_{side.lower()}_reach.csv',index=False);vo.to_csv(OUT/f'oos_{side.lower()}_reversal.csv',index=False);d['sides'][side]={'reach':rs,'reversal':vs};passes += [rs['pass'],vs['pass']]
    d['overall_stage']='PHASE4_V02_PASS' if all(passes) else 'RESEARCH_PARTIAL_OR_FAIL';d['anti_leakage']='target candidates and features registered with data available at t; each test-year model uses only outcomes resolved before Jan1; scores are prior-training percentiles, not probabilities';(OUT/'summary.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(d,ensure_ascii=False))
if __name__=='__main__':main()
