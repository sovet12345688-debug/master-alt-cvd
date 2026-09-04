from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

import run_phase2_four_engines as v01

OUT=Path('btc_trend_v30/output/phase2_v02'); OUT.mkdir(parents=True,exist_ok=True)

ENGINES=['LONG_REVERSAL','LONG_CONTINUATION','SHORT_REVERSAL','SHORT_CONTINUATION']


def clip(v): return float(max(0,min(100,float(v))))


def recov(r,side):
    if side=='LONG':
        return max(float(r.base_l),float(r.hl_l),float(r.retest_l),float(r.momentum_l),float(r.absorb_l),float(r.candle_l))
    return max(float(r.base_s),float(r.lh_s),float(r.retest_s),float(r.momentum_s),float(r.absorb_s),float(r.candle_s))


def build_feature_frame(x):
    z=v01.add_engine_scores(x).copy()
    rows=[]
    for t,r in z.iterrows():
        fl=50.0 if pd.isna(r.get('fractal_long_prior',np.nan)) else float(r.get('fractal_long_prior'))
        fs=50.0 if pd.isna(r.get('fractal_short_prior',np.nan)) else float(r.get('fractal_short_prior'))
        rows.append({
            'time':t,
            'long_candidate':float(r.long_candidate_v11),'short_candidate':float(r.short_candidate_v11),
            'long_turn':float(r.long_turn),'short_turn':float(r.short_turn),
            'long_recovery':recov(r,'LONG'),'short_recovery':recov(r,'SHORT'),
            'wave_long':float(r.wave_l),'wave_short':float(r.wave_s),
            'momentum_long':float(r.momentum_l),'momentum_short':float(r.momentum_s),
            'absorb_long':float(r.absorb_l),'absorb_short':float(r.absorb_s),
            'candle_long':float(r.candle_l),'candle_short':float(r.candle_s),
            'fractal_long':fl,'fractal_short':fs,
            'near_low':float(r.near_low),'near_high':float(r.near_high),
            'prior_down':float(r.prior_down_context),'prior_up':float(r.prior_up_context),
            'ma_long':float(r.MA_LONG),'ma_short':float(r.MA_SHORT),
            'energy_long':float(r.ENERGY_LONG),'energy_short':float(r.ENERGY_SHORT),
            'maturity_long':float(r.MATURITY_LONG),'maturity_short':float(r.MATURITY_SHORT),
            'rsi':float(r.rsi14),
            'volz':clip(50+20*float(r.vol_z)),
            'taker_long':clip(50+250*float(r.taker_imb_3-r.taker_imb_6)),
            'taker_short':clip(50-250*float(r.taker_imb_3-r.taker_imb_6)),
            'ret30_long':clip(50+220*float(r.ret30)),'ret30_short':clip(50-220*float(r.ret30)),
            'ret60_long':clip(50+160*float(r.ret60)),'ret60_short':clip(50-160*float(r.ret60)),
        })
    return pd.DataFrame(rows).set_index('time')


FEATURES={
'LONG_REVERSAL':['long_candidate','long_turn','long_recovery','wave_long','momentum_long','absorb_long','candle_long','fractal_long','near_low','prior_down'],
'SHORT_REVERSAL':['short_candidate','short_turn','short_recovery','wave_short','momentum_short','absorb_short','candle_short','fractal_short','near_high','prior_up'],
'LONG_CONTINUATION':['ma_long','energy_long','momentum_long','candle_long','fractal_long','taker_long','ret30_long','ret60_long','volz','maturity_long'],
'SHORT_CONTINUATION':['ma_short','energy_short','momentum_short','candle_short','fractal_short','taker_short','ret30_short','ret60_short','volz','maturity_short'],
}


def outcome_meta(D,t,price,engine):
    if engine=='LONG_REVERSAL': horizon=180; up=.30; dn=.15; good_up=True
    elif engine=='SHORT_REVERSAL': horizon=180; up=.15; dn=.25; good_up=False
    elif engine=='LONG_CONTINUATION': horizon=120; up=.20; dn=.12; good_up=True
    else: horizon=120; up=.12; dn=.20; good_up=False
    fut=D[(D.index>t)&(D.index<=t+pd.Timedelta(days=horizon))]
    for rt,r in fut.iterrows():
        hit_up=r.high>=price*(1+up); hit_dn=r.low<=price*(1-dn)
        if hit_up and hit_dn: return np.nan,rt
        if hit_up: return (1.0 if good_up else 0.0),rt
        if hit_dn: return (0.0 if good_up else 1.0),rt
    return np.nan,(fut.index.max() if len(fut) else pd.NaT)


def build_samples(D,F):
    rows=[]; last=None
    for t,r in F[F.index.year>=2018].iterrows():
        if last is not None and (t-last).days<21: continue
        last=t; p=float(D.loc[t].close) if t in D.index else np.nan
        if not np.isfinite(p): continue
        for e in ENGINES:
            o,rt=outcome_meta(D,t,p,e)
            q={'time':t,'engine':e,'price':p,'outcome':o,'resolved_time':rt}
            for c in FEATURES[e]: q[c]=float(r[c])
            rows.append(q)
    R=pd.DataFrame(rows); R['time']=pd.to_datetime(R.time,utc=True); R['resolved_time']=pd.to_datetime(R.resolved_time,utc=True)
    return R


def matrix(frame,features):
    X=frame[features].astype(float).fillna(50).values/100.0
    # fixed low-order interactions to express 'good context + actual turn/energy' without test-year tuning
    if len(features)>=4:
        X=np.c_[X,X[:,0]*X[:,1],X[:,0]*X[:,2],X[:,1]*X[:,2],X[:,0]*X[:,3]]
    return X


def fit_logit(train,features,l2=5.0):
    X=matrix(train,features); y=train.outcome.astype(float).values
    mu=X.mean(0); sd=X.std(0); sd[sd<1e-6]=1; Z=(X-mu)/sd; Z=np.c_[np.ones(len(Z)),Z]; w=np.zeros(Z.shape[1])
    for _ in range(1300):
        a=np.clip(Z@w,-16,16); p=1/(1+np.exp(-a)); g=Z.T@(p-y)/len(y); g[1:]+=l2*w[1:]/len(y); w-=.035*g
    return mu,sd,w


def predict(frame,features,m):
    mu,sd,w=m; X=matrix(frame,features); Z=(X-mu)/sd; Z=np.c_[np.ones(len(Z)),Z]; return 1/(1+np.exp(-np.clip(Z@w,-16,16)))


def oos_engine(S,e):
    f=FEATURES[e]; outs=[]; years=[]
    E=S[(S.engine==e)].copy()
    for y in range(2020,2027):
        cut=pd.Timestamp(f'{y}-01-01',tz='UTC')
        tr=E[(E.resolved_time<cut)&E.outcome.notna()].copy(); te=E[(E.time.dt.year==y)&E.outcome.notna()].copy()
        if len(tr)<55 or len(te)<5: continue
        m=fit_logit(tr,f); ptr=predict(tr,f,m); pte=predict(te,f,m)
        # rank is an opportunity score, not a calibrated probability
        te['score']=100*np.searchsorted(np.sort(ptr),pte,side='right')/len(ptr); outs.append(te)
        years.append({'year':y,'train_n':len(tr),'test_n':len(te)})
    O=pd.concat(outs,ignore_index=True) if outs else pd.DataFrame(); buckets=[]
    if O.empty: return O,{'buckets':[],'comparison':{},'years':years,'pass':False}
    for lo,hi in [(0,49),(50,64),(65,79),(80,100)]:
        g=O[(O.score>=lo)&(O.score<=hi)]; buckets.append({'bucket':f'{lo}-{hi}','n':len(g),'success_pct':round(float(g.outcome.mean()*100),2) if len(g) else None})
    low=O[O.score<65]; high=O[O.score>=65]
    c={'evaluable':len(O),'low_n':len(low),'low_success_pct':round(float(low.outcome.mean()*100),2) if len(low) else None,'high_n':len(high),'high_success_pct':round(float(high.outcome.mean()*100),2) if len(high) else None,'high_minus_low_pp':round(float((high.outcome.mean()-low.outcome.mean())*100),2) if len(low) and len(high) else None}
    pas=bool(c['high_n']>=20 and (c['high_success_pct'] or 0)>=55 and (c['high_minus_low_pp'] or -999)>=5)
    return O,{'buckets':buckets,'comparison':c,'years':years,'pass':pas}


def selector(oos):
    # compare engines only on dates for which all available engine scores are truly OOS
    parts=[]
    for e,O in oos.items():
        if O.empty: continue
        q=O[['time','score','outcome']].copy(); q['engine']=e; parts.append(q)
    if not parts: return pd.DataFrame(),{}
    A=pd.concat(parts,ignore_index=True); idx=A.groupby('time').score.idxmax(); R=A.loc[idx].sort_values('time').reset_index(drop=True); ev=R[R.outcome.notna()]; hi=ev[ev.score>=65]
    return R,{'evaluable':len(ev),'selected_success_pct':round(float(ev.outcome.mean()*100),2) if len(ev) else None,'high_n':len(hi),'high_success_pct':round(float(hi.outcome.mean()*100),2) if len(hi) else None}


def main():
    D,x,T,fail=v01.prepare(); F=build_feature_frame(x); S=build_samples(D,F); S.to_csv(OUT/'samples.csv',index=False)
    stats={}; oos={}
    for e in ENGINES:
        O,st=oos_engine(S,e); O.to_csv(OUT/f'oos_{e.lower()}.csv',index=False); stats[e]=st; oos[e]=O
    R,sel=selector(oos); R.to_csv(OUT/'selector_oos.csv',index=False)
    d={'engine':'MASTER_BTC_TREND_V3_PHASE2_FOUR_ENGINE_V0_2','status':'RESEARCH_ONLY_NOT_LIVE','data_failures':fail,'design_changes':['separate prior-year model per opportunity engine','train only on outcomes resolved before each test year','OOS score is percentile rank vs training distribution, not probability','fixed engine-specific features; no test-year threshold tuning'],'outcomes':{'LONG_REVERSAL':'+30% before -15% within 180D','SHORT_REVERSAL':'-25% before +15% within 180D','LONG_CONTINUATION':'+20% before -12% within 120D','SHORT_CONTINUATION':'-20% before +12% within 120D'},'stats':stats,'selector':sel,'overall_stage':'PHASE2_V02_PASS' if all(v['pass'] for v in stats.values()) else 'RESEARCH_PARTIAL_OR_FAIL','master_btc_trend_modified':False,'integration':'FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL','note':'No entry sizing integration until Phase1 Zone Quality is validated.'}
    (OUT/'summary.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(d,ensure_ascii=False))

if __name__=='__main__': main()
