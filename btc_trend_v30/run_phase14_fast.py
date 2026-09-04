from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd

import run_phase11_12_13 as b
import run_phase11_12_13_fast as f13
import run_phase14 as p14

OUT = Path('btc_trend_v30/output/phase14_fast')
OUT.mkdir(parents=True, exist_ok=True)
LOOKBACK_NS = int(pd.Timedelta(days=35).value)


def window_best(idx_ns: np.ndarray, score: np.ndarray) -> np.ndarray:
    q = deque()
    best = np.zeros(len(score), dtype=np.int64)
    for i in range(len(score)):
        while q and idx_ns[i] - idx_ns[q[0]] > LOOKBACK_NS:
            q.popleft()
        while q and score[q[-1]] <= score[i]:
            q.pop()
        q.append(i)
        best[i] = q[0]
    return best


def precompute(x: pd.DataFrame, side: str, fw: float):
    if side == 'LOW':
        base = x.long_candidate_v11.astype(float).values
        fr = x.fractal_long_prior.astype(float).fillna(50).values
        turn = x.long_turn.astype(float).values
        ctx = x.prior_down_context.astype(float).values
        loc = x.near_low.astype(float).values
        rec = np.maximum.reduce([
            x.base_l.astype(float).values, x.hl_l.astype(float).values,
            x.retest_l.astype(float).values, x.momentum_l.astype(float).values,
            x.absorb_l.astype(float).values, x.candle_l.astype(float).values,
        ])
        knife = x.falling_knife_l.astype(float).values
        frraw = x.fractal_long_prior.astype(float).values
    else:
        base = x.short_candidate_v11.astype(float).values
        fr = x.fractal_short_prior.astype(float).fillna(50).values
        turn = x.short_turn.astype(float).values
        ctx = x.prior_up_context.astype(float).values
        loc = x.near_high.astype(float).values
        rec = np.maximum.reduce([
            x.base_s.astype(float).values, x.lh_s.astype(float).values,
            x.retest_s.astype(float).values, x.momentum_s.astype(float).values,
            x.absorb_s.astype(float).values, x.candle_s.astype(float).values,
        ])
        knife = x.blowoff_s.astype(float).values
        frraw = x.fractal_short_prior.astype(float).values
    soft = np.clip((1-fw)*base + fw*fr, 0, 100)
    idx_ns = x.index.asi8
    bi = window_best(idx_ns, soft)
    return {
        'soft': soft, 'turn': turn, 'ctx': ctx, 'loc': loc, 'rec': rec,
        'knife': knife, 'frraw': frraw, 'best': bi,
        'close': x.close.astype(float).values, 'index': x.index,
    }


def build_from_precomp(pc: dict, ct: float, tt: float, fw: float) -> list[dict]:
    out=[]; last=None
    idx=pc['index']; bi=pc['best']
    for i in np.where(pc['turn'] >= tt)[0]:
        j=int(bi[i]); sc=float(pc['soft'][j])
        if sc < ct: continue
        ctx=float(pc['ctx'][j]); loc=float(pc['loc'][j]); rec=float(pc['rec'][j])
        if ctx < 22 and rec < 70: continue
        if loc < 18 and rec < 75: continue
        if pc['knife'][j] >= 100 and rec < 75: continue
        t=idx[i]; bt=idx[j]
        quality=.55*sc+.45*float(pc['turn'][i])
        frraw=pc['frraw'][j]
        sig={'origin_time':bt,'confirm_time':t,'origin_price':float(pc['close'][j]),'confirm_price':float(pc['close'][i]),
             'candidate_soft':round(sc,3),'turn_score':round(float(pc['turn'][i]),3),'fractal_weight':fw,
             'fractal_prior':None if np.isnan(frraw) else float(frraw),'quality':float(quality)}
        if last is None or (t-last).days>28:
            out.append(sig); last=t
        elif quality>out[-1]['quality']:
            out[-1]=sig; last=t
    return out


def choose(train:pd.DataFrame,truth:pd.DataFrame,side:str,allow_fractal:bool):
    weights=[0.0,0.10,0.20,0.30] if allow_fractal else [0.0]
    pcs={fw:precompute(train,side,fw) for fw in weights}
    best=None
    for fw in weights:
      pc=pcs[fw]
      for ct in [38,42,46,50,55,60]:
       for tt in [42,48,54,60,66]:
        sig=build_from_precomp(pc,ct,tt,fw); ev=b.eval_sig(sig,truth,side)
        p=float(ev['precision_pct'] or 0); r=float(ev['recall_pct'] or 0)
        score=p14.fbeta(p,r,1.25)+.10*min(p,40)-.08*max(0,len(sig)-30)
        if p<22: score-=20
        z=(score,p,r,-len(sig),ct,tt,fw)
        if best is None or z>best: best=z
    return {'candidate_threshold':best[4],'turn_threshold':best[5],'fractal_weight':best[6]}


def oos(x,T,side,allow_fractal):
    rows=[]; ws=[]
    for y in range(2020,2027):
        tr=x[x.index.year<y]; te=x[x.index.year==y]; tt=T[T.time.dt.year<y]; ty=T[T.time.dt.year==y]
        if len(tr)<500 or len(te)<100: continue
        pars=choose(tr,tt,side,allow_fractal)
        pc=precompute(te,side,pars['fractal_weight'])
        sig=build_from_precomp(pc,pars['candidate_threshold'],pars['turn_threshold'],pars['fractal_weight'])
        ev=b.eval_sig(sig,ty,side); ev.update({'year':y,**pars}); rows.append(ev); ws.append(pars['fractal_weight'])
    agg=b.v02.aggregate(rows); agg['years']=len(rows); agg['mean_selected_fractal_weight']=round(float(np.mean(ws)),3) if ws else None; agg['years_using_fractal']=int(sum(w>0 for w in ws))
    return rows,agg


def main():
    D,H4,S,T,fail=b.prepare(); p11=b.phase11_scores(D,S); p12=f13.phase12_fast(D,p11); x=p14.add_turn_scores(p12)
    lb_rows,lb=oos(x,T,'LOW',False); sb_rows,sb=oos(x,T,'HIGH',False)
    ls_rows,ls=oos(x,T,'LOW',True); ss_rows,ss=oos(x,T,'HIGH',True)

    # Zone learning needs the original point-in-time trend-location columns from D.
    xz=x.join(D[['drawdown180','rally180']],how='left')
    zl=p14.build_zone_registry(D,xz,'LOW'); zs=p14.build_zone_registry(D,xz,'HIGH')
    zlo,zlb,zlc,zly=p14.zone_oos(zl); zso,zsb,zsc,zsy=p14.zone_oos(zs)
    zl.to_csv(OUT/'zone_registry_long.csv',index=False); zs.to_csv(OUT/'zone_registry_short.csv',index=False)
    zlo.to_csv(OUT/'zone_oos_long.csv',index=False); zso.to_csv(OUT/'zone_oos_short.csv',index=False)
    x.tail(900).to_csv(OUT/'recent_origin_scores.csv')

    origin_pass=p14.gate_origin(ls,ss); zone_long_pass=p14.gate_zone(zlc); zone_short_pass=p14.gate_zone(zsc); zone_pass=zone_long_pass and zone_short_pass
    def f1(a):
        p=float(a.get('precision_pct') or 0); r=float(a.get('recall_pct') or 0); return round(p14.fbeta(p,r,1.0),2)
    fu={'LONG_baseline_F1':f1(lb),'LONG_soft_F1':f1(ls),'LONG_delta_F1':round(f1(ls)-f1(lb),2),
        'SHORT_baseline_F1':f1(sb),'SHORT_soft_F1':f1(ss),'SHORT_delta_F1':round(f1(ss)-f1(sb),2),
        'rule':'soft only; prior-year-selected weight; missing fractal=neutral50; never a hard gate'}
    nonharm=fu['LONG_delta_F1']>=-1 and fu['SHORT_delta_F1']>=-1
    d={'engine':'MASTER_BTC_TREND_V3_PHASE1_4_FAST_V0_2','status':'RESEARCH_ONLY_NOT_LIVE','data_failures':fail,
       'phase_1_4':{'origin_gate_pass':origin_pass,'LONG_NO_FRACTAL_OOS':lb,'LONG_SOFT_FRACTAL_OOS':ls,'SHORT_NO_FRACTAL_OOS':sb,'SHORT_SOFT_FRACTAL_OOS':ss,
          'LONG_years_soft':ls_rows,'SHORT_years_soft':ss_rows,'fractal_utility':fu,'fractal_nonharm_pass':nonharm,
          'zone_long':{'pass':zone_long_pass,'buckets':zlb,'comparison':zlc,'years':zly},'zone_short':{'pass':zone_short_pass,'buckets':zsb,'comparison':zsc,'years':zsy},'zone_gate_pass':zone_pass},
       'overall_stage':'PHASE1_4_VALIDATED' if origin_pass and nonharm and zone_pass else 'RESEARCH_PARTIAL_OR_FAIL',
       'master_btc_trend_modified':False,'integration':'FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL',
       'anti_leakage':{'origin_thresholds':'prior years only','fractal':'analogs<=query-181D with fully known outcomes','zone_learning':'prior resolved zone reactions only'}}
    (OUT/'summary.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(d,ensure_ascii=False))

if __name__=='__main__': main()
