from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

import run_phase3_trend_state as p3
import run_phase2_v03 as p2

OUT=Path('btc_trend_v30/output/phase4_v01'); OUT.mkdir(parents=True,exist_ok=True)


def clip(v): return float(max(0.0,min(100.0,float(v))))


def cluster_levels(raw,p,tol=.025):
    cs=[]
    for name,v,importance in sorted(raw,key=lambda z:z[1]):
        hit=None
        for c in cs:
            if abs(v-c['center'])/max(c['center'],1e-9)<=tol: hit=c; break
        if hit:
            hit['levels'].append(v); hit['sources'].append(name); hit['importance']=max(hit['importance'],importance); hit['center']=float(np.mean(hit['levels']))
        else: cs.append({'center':float(v),'levels':[float(v)],'sources':[name],'importance':importance})
    for c in cs:
        c['confluence']=len(c['sources'])
    return cs


def target_candidates(D,t,side,regime_start,regime_start_price):
    h=D.loc[:t].copy()
    if len(h)<365:return []
    r=h.iloc[-1]; p=float(r.close); atr=float(r.atr14) if pd.notna(r.atr14) else p*.03
    if side=='LONG':
        raw=[]
        for n,w,imp in [('H20',20,55),('H60',60,70),('H120',120,82),('H365',365,100)]:
            v=float(h.high.shift(1).tail(w).max());
            if np.isfinite(v) and v>p*1.015: raw.append((n,v,imp))
        # point-in-time measured moves from the regime start and prior known volatility/range
        base_range=float(h.high.tail(60).max()-h.low.tail(60).min())
        for mult,imp in [(0.75,55),(1.0,65),(1.5,78),(2.0,90)]:
            v=float(regime_start_price+mult*base_range)
            if v>p*1.03:raw.append((f'MM{mult:g}',v,imp))
    else:
        raw=[]
        for n,w,imp in [('L20',20,55),('L60',60,70),('L120',120,82),('L365',365,100)]:
            v=float(h.low.shift(1).tail(w).min());
            if np.isfinite(v) and v<p*.985:raw.append((n,v,imp))
        base_range=float(h.high.tail(60).max()-h.low.tail(60).min())
        for mult,imp in [(0.75,55),(1.0,65),(1.5,78),(2.0,90)]:
            v=float(regime_start_price-mult*base_range)
            if v>0 and v<p*.97:raw.append((f'MM{mult:g}',v,imp))
    cs=cluster_levels(raw,p)
    cs=sorted(cs,key=lambda c:abs(c['center']-p))[:3]
    for i,c in enumerate(cs,1):
        half=max(.006*c['center'],min(.018*c['center'],.30*atr)); c['low']=c['center']-half; c['high']=c['center']+half; c['rank']=i
    return cs


def prior_reaction(D,t,side,center):
    hist=D[(D.index<t)&(D.index>=t-pd.Timedelta(days=730))]
    if hist.empty:return 50.0,0
    tol=.018; touch=(hist.low<=center*(1+tol))&(hist.high>=center*(1-tol)); starts=list(hist.index[touch&~touch.shift(1,fill_value=False)])
    vals=[]
    for st in starts[-6:]:
        aft=D[(D.index>=st)&(D.index<=min(t-pd.Timedelta(days=1),st+pd.Timedelta(days=60)))]
        if len(aft)<3:continue
        if side=='LONG': # target resistance should historically reject downward
            adverse=(center-float(aft.low.min()))/center; extension=(float(aft.high.max())-center)/center
        else:
            adverse=(float(aft.high.max())-center)/center; extension=(center-float(aft.low.min()))/center
        vals.append(float(adverse>=.12 and extension<.12))
    return (50.0 if not vals else 100*float(np.mean(vals))),len(starts)


def resolve(D,t,p,side,c):
    target=float(c['center']); invalid=p*.85 if side=='LONG' else p*1.15
    fut=D[(D.index>t)&(D.index<=t+pd.Timedelta(days=180))]
    touch=None; reach=None; reach_resolve=None
    for rt,r in fut.iterrows():
        if side=='LONG': a=r.high>=c['low']; b=r.low<=invalid
        else: a=r.low<=c['high']; b=r.high>=invalid
        if a and b:return {'reach':np.nan,'reach_resolved':rt,'touch':rt,'reversal':np.nan,'reversal_resolved':rt}
        if b:return {'reach':0.0,'reach_resolved':rt,'touch':pd.NaT,'reversal':np.nan,'reversal_resolved':pd.NaT}
        if a:touch=rt; reach=1.0; reach_resolve=rt; break
    if touch is None:return {'reach':np.nan,'reach_resolved':fut.index.max() if len(fut) else pd.NaT,'touch':pd.NaT,'reversal':np.nan,'reversal_resolved':pd.NaT}
    aft=D[(D.index>=touch)&(D.index<=touch+pd.Timedelta(days=90))]
    for rt,r in aft.iterrows():
        if side=='LONG': rev=r.low<=target*.85; ext=r.high>=target*1.10
        else: rev=r.high>=target*1.15; ext=r.low<=target*.90
        if rev and ext:return {'reach':reach,'reach_resolved':reach_resolve,'touch':touch,'reversal':np.nan,'reversal_resolved':rt}
        if rev:return {'reach':reach,'reach_resolved':reach_resolve,'touch':touch,'reversal':1.0,'reversal_resolved':rt}
        if ext:return {'reach':reach,'reach_resolved':reach_resolve,'touch':touch,'reversal':0.0,'reversal_resolved':rt}
    return {'reach':reach,'reach_resolved':reach_resolve,'touch':touch,'reversal':np.nan,'reversal_resolved':aft.index.max() if len(aft) else pd.NaT}


def build_registry(D,x):
    R=p3.build_regime_frame(D,x); rows=[]
    for side,g in R.groupby('side'):
        last=None
        for _,r in g[g.time.dt.year>=2018].sort_values('time').iterrows():
            t=pd.Timestamp(r.time)
            if last is not None and (t-last).days<28:continue
            last=t; p=float(r.price); frcol='fractal_long_prior' if side=='LONG' else 'fractal_short_prior'; xr=x.loc[:t].iloc[-1]; fr=50.0 if pd.isna(xr.get(frcol,np.nan)) else float(xr.get(frcol))
            for c in target_candidates(D,t,side,pd.Timestamp(r.regime_start),float(D.loc[pd.Timestamp(r.regime_start)].close)):
                prior,tc=prior_reaction(D,t,side,c['center']); dist=abs(c['center']-p)/p
                res=resolve(D,t,p,side,c)
                rows.append({'time':t,'side':side,'price':p,'target_rank':c['rank'],'target':c['center'],'zone_low':c['low'],'zone_high':c['high'],'distance_pct':100*dist,
                             'confluence_score':clip(c['confluence']/3*100),'source_importance':c['importance'],'prior_reversal_rate':prior,'prior_touch_count':tc,'fractal_path_score':fr,
                             'trend_age_days':float(r.age_days),'move_since_start_pct':float(r.move_since_start_pct),'raw_maturity':float(r.raw_maturity),'sources':'+'.join(c['sources']),**res})
    A=pd.DataFrame(rows)
    for c in ['time','reach_resolved','touch','reversal_resolved']:A[c]=pd.to_datetime(A[c],utc=True)
    return A

REACH_F=['target_rank','distance_pct','confluence_score','source_importance','fractal_path_score','trend_age_days','move_since_start_pct','raw_maturity']
REV_F=['target_rank','distance_pct','confluence_score','source_importance','prior_reversal_rate','prior_touch_count','fractal_path_score','trend_age_days','move_since_start_pct','raw_maturity']


def matrix(f,cols):
    X=f[cols].astype(float).copy()
    if 'target_rank' in X:X['target_rank']=X.target_rank/3*100
    if 'distance_pct' in X:X['distance_pct']=np.clip(X.distance_pct/60*100,0,100)
    if 'trend_age_days' in X:X['trend_age_days']=np.clip(X.trend_age_days/180*100,0,100)
    if 'move_since_start_pct' in X:X['move_since_start_pct']=np.clip(X.move_since_start_pct/60*100,0,100)
    if 'prior_touch_count' in X:X['prior_touch_count']=np.clip(X.prior_touch_count/6*100,0,100)
    return X.fillna(50).values/100


def fit(tr,cols,label,l2=5):
    X=matrix(tr,cols); y=tr[label].astype(float).values; mu=X.mean(0);sd=X.std(0);sd[sd<1e-6]=1;Z=(X-mu)/sd;Z=np.c_[np.ones(len(Z)),Z];w=np.zeros(Z.shape[1])
    for _ in range(1100):
        a=np.clip(Z@w,-16,16);p=1/(1+np.exp(-a));g=Z.T@(p-y)/len(y);g[1:]+=l2*w[1:]/len(y);w-=.035*g
    return mu,sd,w


def pred(te,cols,m):
    mu,sd,w=m;X=matrix(te,cols);Z=(X-mu)/sd;Z=np.c_[np.ones(len(Z)),Z];return 1/(1+np.exp(-np.clip(Z@w,-16,16)))


def oos(A,side,label,cols,rescol):
    E=A[A.side==side].copy();outs=[];yrs=[]
    for y in range(2020,2027):
        cut=pd.Timestamp(f'{y}-01-01',tz='UTC'); tr=E[(E[rescol]<cut)&E[label].notna()].copy();te=E[(E.time.dt.year==y)&E[label].notna()].copy()
        if len(tr)<60 or len(te)<8:continue
        m=fit(tr,cols,label);ptr=pred(tr,cols,m);pte=pred(te,cols,m);te['score']=100*np.searchsorted(np.sort(ptr),pte,side='right')/len(ptr);outs.append(te);yrs.append({'year':y,'train_n':len(tr),'test_n':len(te)})
    O=pd.concat(outs,ignore_index=True) if outs else pd.DataFrame();b=[]
    if O.empty:return O,{'pass':False,'buckets':[],'years':yrs}
    for lo,hi in [(0,49),(50,64),(65,79),(80,100)]:
        g=O[(O.score>=lo)&(O.score<=hi)];b.append({'bucket':f'{lo}-{hi}','n':len(g),'success_pct':round(float(g[label].mean()*100),2) if len(g) else None})
    low=O[O.score<65];high=O[O.score>=65];gap=round(float((high[label].mean()-low[label].mean())*100),2) if len(low) and len(high) else None
    pop=[q for q in b if q['n']>=10 and q['success_pct'] is not None];mono=all(pop[i+1]['success_pct']+5>=pop[i]['success_pct'] for i in range(len(pop)-1)) if len(pop)>=3 else False
    pas=bool(len(high)>=15 and gap is not None and gap>=10)
    return O,{'pass':pas,'buckets':b,'low_n':len(low),'high_n':len(high),'high_minus_low_pp':gap,'monotonic_with_5pp_tolerance':mono,'years':yrs}


def main():
    D,x,T,fail=p2.prepare(); A=build_registry(D,x);A.to_csv(OUT/'target_registry.csv',index=False);d={'engine':'MASTER_BTC_TREND_V3_TARGET_MAP_V0_1','status':'RESEARCH_ONLY_NOT_LIVE','data_failures':fail,'definitions':{'TARGET_REACH':'candidate target touched before 15% structural adverse move within 180D','TARGET_REVERSAL':'conditional on target touch, 15% reversal before further 10% extension within 90D'},'sides':{},'master_btc_trend_modified':False,'integration':'FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL'}
    passes=[]
    for side in ['LONG','SHORT']:
        ro,rs=oos(A,side,'reach',REACH_F,'reach_resolved'); vo,vs=oos(A[A.reach==1],side,'reversal',REV_F,'reversal_resolved');ro.to_csv(OUT/f'oos_{side.lower()}_reach.csv',index=False);vo.to_csv(OUT/f'oos_{side.lower()}_reversal.csv',index=False);d['sides'][side]={'reach':rs,'reversal':vs};passes += [rs['pass'],vs['pass']]
    d['overall_stage']='PHASE4_V01_PASS' if all(passes) else 'RESEARCH_PARTIAL_OR_FAIL';d['anti_leakage']='targets and features registered from data available at t; each test-year model trains only on outcomes resolved before Jan 1; score is prior-training percentile, not probability'
    (OUT/'summary.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(d,ensure_ascii=False))

if __name__=='__main__':main()
