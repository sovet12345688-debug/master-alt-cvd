from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import run_phase11_12_13 as b

OUT=Path('btc_trend_v30/output/phase11_12_13_fast'); OUT.mkdir(parents=True,exist_ok=True)


def future_outcomes(D,idx,side):
    out=np.full(len(idx),np.nan)
    for k,t in enumerate(idx):
        if t not in D.index: continue
        p=float(D.loc[t].close); fut=D[(D.index>t)&(D.index<=t+pd.Timedelta(days=180))]
        for _,r in fut.iterrows():
            if side=='LOW': good=r.high>=p*1.30; bad=r.low<=p*0.85
            else: good=r.low<=p*0.75; bad=r.high>=p*1.15
            if good and bad: break
            if good: out[k]=1; break
            if bad: out[k]=0; break
    return out


def phase12_fast(D,x):
    f=x.copy()
    cols=['prior_down_context','prior_up_context','near_low','near_high','wave_l','wave_s','momentum_l','momentum_s','absorb_l','absorb_s','candle_l','candle_s']
    Z=f[cols].astype(float).fillna(0).values/100.0
    for side in ['LOW','HIGH']:
        cc='long_candidate_v11' if side=='LOW' else 'short_candidate_v11'
        outcol='fractal_long_prior' if side=='LOW' else 'fractal_short_prior'
        use=[0,2,4,6,8,10] if side=='LOW' else [1,3,5,7,9,11]
        outcomes=future_outcomes(D,f.index,side)
        vals=np.full(len(f),np.nan)
        eligible=np.where(f[cc].values>=35)[0]
        for i in eligible:
            t=f.index[i]
            hist=np.where((f.index<=t-pd.Timedelta(days=181))&np.isfinite(outcomes))[0]
            if len(hist)<180: continue
            q=Z[i,use]; h=Z[hist][:,use]; dist=np.sqrt(np.mean((h-q)**2,axis=1))
            take=np.argsort(dist)[:24]; ids=hist[take]
            if len(ids)<10: continue
            w=1/(.05+dist[take]); vals[i]=100*float(np.average(outcomes[ids],weights=w))
        f[outcol]=vals
    return f


def choose_fast(train,T,side,frcol=None):
    best=None
    for ct in [45,55,65]:
      for ft in [55,65,70]:
       for fr in ([55,65] if frcol else [0]):
        s=b.build_signals(train,side,ct,ft,frcol,fr); e=b.eval_sig(s,T,side)
        p=e['precision_pct'] or 0; r=e['recall_pct'] or 0
        score=1.4*p+r-.2*len(s); z=(score,p,r,ct,ft,fr)
        if best is None or z>best: best=z
    return best[3],best[4],best[5]


def oos_fast(x,T,side,frcol=None):
    rows=[]
    for y in range(2020,2027):
        tr=x[x.index.year<y]; te=x[x.index.year==y]; tt=T[T.time.dt.year<y]; ty=T[T.time.dt.year==y]
        if len(tr)<500 or len(te)<100: continue
        ct,ft,fr=choose_fast(tr,tt,side,frcol)
        s=b.build_signals(te,side,ct,ft,frcol,fr); e=b.eval_sig(s,ty,side); e.update({'year':y,'ct':ct,'ft':ft,'fractal_th':fr}); rows.append(e)
    return rows,b.v02.aggregate(rows)


def zones_fast(D,f,side):
    rows=[]; frcol='fractal_long_prior' if side=='LOW' else 'fractal_short_prior'
    # first available observation per month; genuinely pre-touch and non-overlapping enough for research audit
    tmp=f[f.index.year>=2020].copy(); tmp['ym']=tmp.index.to_period('M'); sample=tmp.groupby('ym').head(1)
    for t,r in sample.iterrows():
        for z in b.zone_candidates(D,t,side):
            q=b.pretouch_quality(D,t,side,z,r.get(frcol,np.nan)); fut=D[(D.index>t)&(D.index<=t+pd.Timedelta(days=90))]
            success=np.nan; touched=False
            for tt,a in fut.iterrows():
                if a.low<=z['high'] and a.high>=z['low']:
                    touched=True; aft=D[(D.index>=tt)&(D.index<=tt+pd.Timedelta(days=45))]
                    if len(aft):
                        if side=='LOW': success=float(aft.high.max()>=z['center']*1.15 and aft.low.min()>z['center']*.85)
                        else: success=float(aft.low.min()<=z['center']*.85 and aft.high.max()<z['center']*1.15)
                    break
            rows.append({'time':t,'side':side,'quality':q,'zone':z['center'],'touched':touched,'success':success,'confluence':z['confluence']})
    R=pd.DataFrame(rows); buckets=[]
    for lo,hi in [(0,49),(50,64),(65,79),(80,100)]:
        g=R[(R.quality>=lo)&(R.quality<=hi)&R.success.notna()]
        buckets.append({'bucket':f'{lo}-{hi}','evaluable':int(len(g)),'success_pct':round(float(g.success.mean()*100),2) if len(g) else None})
    return R,buckets


def main():
    D,H4,S,T,fail=b.prepare(); p11=b.phase11_scores(D,S)
    l11,al11=oos_fast(p11,T,'LOW'); s11,as11=oos_fast(p11,T,'HIGH')
    p12=phase12_fast(D,p11)
    l12,al12=oos_fast(p12,T,'LOW','fractal_long_prior'); s12,as12=oos_fast(p12,T,'HIGH','fractal_short_prior')
    zl,zlb=zones_fast(D,p12,'LOW'); zs,zsb=zones_fast(D,p12,'HIGH')
    zl.to_csv(OUT/'pretouch_long_zones.csv',index=False); zs.to_csv(OUT/'pretouch_short_zones.csv',index=False); p12.tail(800).to_csv(OUT/'recent_scores.csv')
    p11pass=bool((al11['precision_pct'] or 0)>=30 and (al11['recall_pct'] or 0)>=25 and (as11['precision_pct'] or 0)>=30 and (as11['recall_pct'] or 0)>=25)
    p12pass=bool((al12['precision_pct'] or 0)>=30 and (al12['recall_pct'] or 0)>=25 and (as12['precision_pct'] or 0)>=30 and (as12['recall_pct'] or 0)>=25)
    p13pass=bool(any((q['evaluable']>=15 and (q['success_pct'] or 0)>=55) for q in zlb) and any((q['evaluable']>=15 and (q['success_pct'] or 0)>=55) for q in zsb))
    d={'engine':'MASTER_BTC_TREND_V3_PHASE11_12_13_FAST_V0_1','status':'RESEARCH_ONLY_NOT_LIVE','data_failures':fail,
       'phase_1_1':{'pass':p11pass,'LONG_OOS':al11,'SHORT_OOS':as11,'years_long':l11,'years_short':s11},
       'phase_1_2':{'pass':p12pass,'LONG_OOS':al12,'SHORT_OOS':as12,'years_long':l12,'years_short':s12,'pit_rule':'analogs <= query-181D; only fully-known 180D outcomes','note':'price-state prior only; failed onchain/macro confirmation layer excluded'},
       'phase_1_3':{'pass':p13pass,'LONG_BUCKETS':zlb,'SHORT_BUCKETS':zsb,'rule':'zones registered before touch from MA/rolling structure; no future data in quality'},
       'overall_stage':'PHASE1_1_TO_1_3_VALIDATED' if p11pass and p12pass and p13pass else 'RESEARCH_PARTIAL_OR_FAIL',
       'master_btc_trend_modified':False,'integration':'FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL'}
    (OUT/'summary.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(d,ensure_ascii=False))

if __name__=='__main__': main()
