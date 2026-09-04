from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import run_phase11_12_13 as b
import run_phase11_12_13_fast as f13
import run_phase14 as p14

OUT = Path('btc_trend_v30/output/phase14_v03')
OUT.mkdir(parents=True, exist_ok=True)


def fbeta(p: float, r: float, beta: float = 1.0) -> float:
    if p <= 0 or r <= 0:
        return 0.0
    b2 = beta * beta
    return (1+b2)*p*r/(b2*p+r)


def candidate_strength(r: pd.Series, side: str) -> float:
    base = float(r.long_candidate_v11 if side == 'LOW' else r.short_candidate_v11)
    return base


def recovery(r: pd.Series, side: str) -> float:
    if side == 'LOW':
        vals=[r.base_l,r.hl_l,r.retest_l,r.momentum_l,r.absorb_l,r.candle_l]
    else:
        vals=[r.base_s,r.lh_s,r.retest_s,r.momentum_s,r.absorb_s,r.candle_s]
    return float(np.nanmax(np.asarray(vals,dtype=float)))


def turn_score(r: pd.Series, side: str) -> float:
    return float(r.long_turn if side == 'LOW' else r.short_turn)


def build_signals(x: pd.DataFrame, side: str, ct: float, tt: float, fw: float, lookback: int=35):
    """Two-stage origin: confirmation happens later, but origin is anchored to the price extreme
    among plausible candidates already observable inside the trailing window. Fractal is only a
    soft tie/ranking term and never deletes a candidate."""
    out=[]; last_confirm=None
    for t,r in x.iterrows():
        if turn_score(r,side) < tt:
            continue
        h=x[(x.index>=t-pd.Timedelta(days=lookback))&(x.index<=t)].copy()
        if h.empty: continue
        basecol='long_candidate_v11' if side=='LOW' else 'short_candidate_v11'
        ctxcol='prior_down_context' if side=='LOW' else 'prior_up_context'
        loccol='near_low' if side=='LOW' else 'near_high'
        h=h[h[basecol]>=ct].copy()
        if h.empty: continue
        rec=h.apply(lambda q: recovery(q,side),axis=1)
        h=h[((h[ctxcol]>=22)|(rec>=70)) & ((h[loccol]>=18)|(rec>=75))].copy()
        if h.empty: continue
        if side=='LOW':
            ok=~((h.falling_knife_l>=100)&(h.apply(lambda q: recovery(q,side),axis=1)<75))
        else:
            ok=~((h.blowoff_s>=100)&(h.apply(lambda q: recovery(q,side),axis=1)<75))
        h=h[ok]
        if h.empty: continue

        # The origin is a PRICE origin, so anchor it to the actual local extreme among plausible
        # candidates, not to the day with the highest indicator score.
        if side=='LOW':
            extreme=float(h.low.min())
            near=h[h.low<=extreme*1.03].copy()
            # among near-equal lows, use score/fractal to select the best contextual date
            fr=near.fractal_long_prior.fillna(50).astype(float)
            rank=near[basecol].astype(float)+fw*(fr-50)
            ot=rank.idxmax(); op=float(near.loc[ot,'low'])
        else:
            extreme=float(h.high.max())
            near=h[h.high>=extreme*0.97].copy()
            fr=near.fractal_short_prior.fillna(50).astype(float)
            rank=near[basecol].astype(float)+fw*(fr-50)
            ot=rank.idxmax(); op=float(near.loc[ot,'high'])
        br=near.loc[ot]
        frv=50.0 if pd.isna(fr.loc[ot]) else float(fr.loc[ot])
        soft=float(br[basecol])+fw*(frv-50.0)
        quality=.45*soft+.55*turn_score(r,side)
        sig={'origin_time':ot,'confirm_time':t,'origin_price':op,'confirm_price':float(r.close),
             'candidate_base':float(br[basecol]),'fractal_prior':None if pd.isna(fr.loc[ot]) else frv,
             'fractal_weight':fw,'turn_score':turn_score(r,side),'quality':quality}
        if last_confirm is None or (t-last_confirm).days>28:
            out.append(sig); last_confirm=t
        elif quality>out[-1]['quality']:
            out[-1]=sig; last_confirm=t
    return out


def choose_stable(train:pd.DataFrame,truth:pd.DataFrame,side:str,allow_fractal:bool):
    ws=[0.0,0.05,0.10,0.15] if allow_fractal else [0.0]
    best=None
    for ct in [35,40,45,50,55]:
      for tt in [42,48,54,60,66]:
       for fw in ws:
        sig=build_signals(train,side,ct,tt,fw)
        ev=b.eval_sig(sig,truth,side)
        p=float(ev['precision_pct'] or 0); r=float(ev['recall_pct'] or 0)
        # Cross-year stability penalty: do not optimize on one lucky cycle.
        fys=[]
        for yy in sorted(set(truth.time.dt.year)):
            sy=[z for z in sig if pd.Timestamp(z['confirm_time']).year==yy]
            ty=truth[truth.time.dt.year==yy]
            if len(ty)==0: continue
            ey=b.eval_sig(sy,ty,side); py=float(ey['precision_pct'] or 0); ry=float(ey['recall_pct'] or 0)
            if sy: fys.append(fbeta(py,ry,1.0))
        stab=float(np.median(fys)) if fys else 0.0
        obj=fbeta(p,r,1.15)+0.25*stab+0.08*min(p,45)-0.05*max(0,len(sig)-35)
        if p<20: obj-=15
        z=(obj,p,r,stab,-len(sig),ct,tt,fw)
        if best is None or z>best: best=z
    return {'candidate_threshold':best[5],'turn_threshold':best[6],'fractal_weight':best[7]}


def oos(x,T,side,allow_fractal):
    rows=[]; ws=[]
    for y in range(2020,2027):
        tr=x[x.index.year<y]; te=x[x.index.year==y]
        tt=T[T.time.dt.year<y]; ty=T[T.time.dt.year==y]
        if len(tr)<500 or len(te)<100: continue
        pars=choose_stable(tr,tt,side,allow_fractal)
        sig=build_signals(te,side,pars['candidate_threshold'],pars['turn_threshold'],pars['fractal_weight'])
        ev=b.eval_sig(sig,ty,side); ev.update({'year':y,**pars}); rows.append(ev); ws.append(pars['fractal_weight'])
    a=b.v02.aggregate(rows); a['years']=len(rows); a['mean_selected_fractal_weight']=round(float(np.mean(ws)),3) if ws else None; a['years_using_fractal']=int(sum(w>0 for w in ws))
    return rows,a


FEATURES=['confluence_score','trend_score','origin_score','fractal_score','source_score']

def fit_logit(train:pd.DataFrame,l2:float=1.5):
    X=train[FEATURES].astype(float).values/100.0; y=train.success.astype(float).values
    mu=X.mean(0); sd=X.std(0); sd[sd<1e-6]=1
    Z=(X-mu)/sd
    Z=np.column_stack([np.ones(len(Z)),Z])
    w=np.zeros(Z.shape[1]); lr=.05
    for _ in range(1400):
        a=np.clip(Z@w,-20,20); pr=1/(1+np.exp(-a)); grad=Z.T@(pr-y)/len(y)
        grad[1:]+=l2*w[1:]/len(y)
        w-=lr*grad
    return mu,sd,w


def pred_logit(frame:pd.DataFrame,model):
    mu,sd,w=model; X=frame[FEATURES].astype(float).values/100.0; Z=(X-mu)/sd; Z=np.column_stack([np.ones(len(Z)),Z]); a=np.clip(Z@w,-20,20); return 1/(1+np.exp(-a))


def reaction_score(train:pd.DataFrame,test:pd.DataFrame):
    model=fit_logit(train)
    ptr=pred_logit(train,model); pte=pred_logit(test,model)
    # Convert conditional reaction likelihood to a prior-train percentile-like quality score.
    s=np.sort(ptr)
    ranks=np.searchsorted(s,pte,side='right')/max(len(s),1)
    return 100*ranks, pte


def zone_oos_v03(R:pd.DataFrame):
    outs=[]; years=[]
    for y in range(2020,2027):
        cut=pd.Timestamp(f'{y}-01-01',tz='UTC')
        tr=R[(R.resolved_time<cut)&R.success.notna()].copy(); te=R[(R.time.dt.year==y)&R.success.notna()].copy()
        if len(tr)<50 or len(te)<5: continue
        q,p=reaction_score(tr,te); te['reaction_quality']=q; te['reaction_prob_raw']=p
        # Reach score is intentionally separate; it must not contaminate conditional reaction quality.
        te['reach_score']=te.distance_score
        outs.append(te); years.append({'year':y,'train_n':len(tr),'test_n':len(te)})
    O=pd.concat(outs,ignore_index=True) if outs else pd.DataFrame()
    buckets=[]
    if not O.empty:
        for lo,hi in [(0,49),(50,64),(65,79),(80,100)]:
            g=O[(O.reaction_quality>=lo)&(O.reaction_quality<=hi)]
            buckets.append({'bucket':f'{lo}-{hi}','evaluable':len(g),'success_pct':round(float(g.success.mean()*100),2) if len(g) else None})
        low=O[O.reaction_quality<65]; high=O[O.reaction_quality>=65]
        comp={'low_n':len(low),'low_success_pct':round(float(low.success.mean()*100),2) if len(low) else None,
              'high_n':len(high),'high_success_pct':round(float(high.success.mean()*100),2) if len(high) else None,
              'high_minus_low_pp':round(float((high.success.mean()-low.success.mean())*100),2) if len(low) and len(high) else None}
    else: comp={'low_n':0,'low_success_pct':None,'high_n':0,'high_success_pct':None,'high_minus_low_pp':None}
    return O,buckets,comp,years


def origin_gate(l,s):
    return (l.get('precision_pct') or 0)>=30 and (l.get('recall_pct') or 0)>=25 and (s.get('precision_pct') or 0)>=30 and (s.get('recall_pct') or 0)>=25

def zone_gate(c):
    return c['high_n']>=20 and (c['high_success_pct'] or 0)>=50 and (c['high_minus_low_pp'] or -999)>=8


def main():
    D,H4,S,T,fail=b.prepare(); p11=b.phase11_scores(D,S); p12=f13.phase12_fast(D,p11); x=p14.add_turn_scores(p12)
    x=x.join(D[['low','high','drawdown180','rally180']],how='left')
    l0r,l0=oos(x,T,'LOW',False); s0r,s0=oos(x,T,'HIGH',False); lfr,lf=oos(x,T,'LOW',True); sfr,sf=oos(x,T,'HIGH',True)

    zl=p14.build_zone_registry(D,x,'LOW'); zs=p14.build_zone_registry(D,x,'HIGH')
    zlo,zlb,zlc,zly=zone_oos_v03(zl); zso,zsb,zsc,zsy=zone_oos_v03(zs)
    zlo.to_csv(OUT/'zone_oos_long.csv',index=False); zso.to_csv(OUT/'zone_oos_short.csv',index=False)
    x.tail(900).to_csv(OUT/'recent_origin_scores.csv')

    def F(a): return round(fbeta(float(a.get('precision_pct') or 0),float(a.get('recall_pct') or 0),1),2)
    fu={'LONG_baseline_F1':F(l0),'LONG_soft_F1':F(lf),'LONG_delta_F1':round(F(lf)-F(l0),2),'SHORT_baseline_F1':F(s0),'SHORT_soft_F1':F(sf),'SHORT_delta_F1':round(F(sf)-F(s0),2),
        'rule':'fractal only ranks near-equal origin candidates; never deletes a chart setup'}
    nonharm=fu['LONG_delta_F1']>=-1 and fu['SHORT_delta_F1']>=-1
    og=origin_gate(lf,sf); zlg=zone_gate(zlc); zsg=zone_gate(zsc); zg=zlg and zsg
    d={'engine':'MASTER_BTC_TREND_V3_PHASE1_4_V0_3','status':'RESEARCH_ONLY_NOT_LIVE','data_failures':fail,
       'design_changes':['origin anchored to actual trailing price extreme among plausible candidates','fractal reduced to soft ranking/tie term','pre-touch zone split into reach score vs conditional reaction quality','zone reaction quality learned only from prior resolved reactions'],
       'phase_1_4':{'origin_gate_pass':og,'LONG_BASELINE':l0,'LONG_SOFT_FRACTAL':lf,'SHORT_BASELINE':s0,'SHORT_SOFT_FRACTAL':sf,'LONG_years':lfr,'SHORT_years':sfr,'fractal_utility':fu,'fractal_nonharm_pass':nonharm,
          'zone_long':{'pass':zlg,'buckets':zlb,'comparison':zlc,'years':zly},'zone_short':{'pass':zsg,'buckets':zsb,'comparison':zsc,'years':zsy},'zone_gate_pass':zg},
       'overall_stage':'PHASE1_4_VALIDATED' if og and nonharm and zg else 'RESEARCH_PARTIAL_OR_FAIL','master_btc_trend_modified':False,'integration':'FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL',
       'anti_leakage':{'origin':'thresholds and fractal weight prior-years only; origin extreme is inside trailing window already known at confirmation','fractal':'PIT prior from analog outcomes fully known before query','zone':'test-year reaction model trained only on reactions resolved before test year'}}
    (OUT/'summary.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(d,ensure_ascii=False))

if __name__=='__main__': main()
