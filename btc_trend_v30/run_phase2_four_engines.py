from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

import run_phase11_12_13 as b
import run_phase11_12_13_fast as f13
import run_phase14 as p14
import run_phase14_v04 as v04

OUT=Path('btc_trend_v30/output/phase2_v01'); OUT.mkdir(parents=True,exist_ok=True)


def clip(v): return float(max(0,min(100,float(v))))


def prepare():
    D,H4,S,T,fail=b.prepare(); p11=b.phase11_scores(D,S); p12=f13.phase12_fast(D,p11); x=p14.add_turn_scores(p12)
    cols=['open','high','low','close','ema20','sma50','sma200','ema20_slope5','rsi14','ret3','ret30','ret60','vol_z','taker_imb_3','taker_imb_6','close_loc','high20_prev','low20_prev','drawdown180','rally180']
    add=[c for c in cols if c in D.columns and c not in x.columns]
    if add: x=x.join(D[add],how='left')
    return D,x,T,fail


def recov(row,side):
    if side=='LONG':
        return max(float(row.base_l),float(row.hl_l),float(row.retest_l),float(row.momentum_l),float(row.absorb_l),float(row.candle_l))
    return max(float(row.base_s),float(row.lh_s),float(row.retest_s),float(row.momentum_s),float(row.absorb_s),float(row.candle_s))


def ma_structure(r,side):
    if side=='LONG':
        return clip(22*(r.close>r.ema20)+18*(r.ema20>r.sma50)+20*(r.sma50>r.sma200)+15*(r.ema20_slope5>0)+10*(r.ret30>0)+15*(r.close>r.high20_prev))
    return clip(22*(r.close<r.ema20)+18*(r.ema20<r.sma50)+20*(r.sma50<r.sma200)+15*(r.ema20_slope5<0)+10*(r.ret30<0)+15*(r.close<r.low20_prev))


def energy(r,side):
    if side=='LONG':
        s=25*(r.ret3>0)+20*(r.ret30>0)+20*(float(r.candle_l)/100)+15*(r.taker_imb_3>r.taker_imb_6)+20*(r.vol_z>0 and r.close_loc>0.55)
    else:
        s=25*(r.ret3<0)+20*(r.ret30<0)+20*(float(r.candle_s)/100)+15*(r.taker_imb_3<r.taker_imb_6)+20*(r.vol_z>0 and r.close_loc<0.45)
    return clip(s)


def momentum(r,side):
    q=float(r.rsi14)
    if side=='LONG':
        if 52<=q<=68: base=90
        elif 45<=q<52 or 68<q<=74: base=70
        elif 38<=q<45: base=52
        elif q>80: base=20
        else: base=38
        if r.momentum_l>=70: base+=10
    else:
        if 32<=q<=48: base=90
        elif 26<=q<32 or 48<q<=55: base=70
        elif 55<q<=62: base=52
        elif q<20: base=20
        else: base=38
        if r.momentum_s>=70: base+=10
    return clip(base)


def maturity_penalty(r,side):
    if side=='LONG':
        e=max(0,float(r.close/r.ema20-1)); e2=max(0,float(r.close/r.sma50-1)); hot=max(0,float(r.rsi14)-72)/18
    else:
        e=max(0,float(r.ema20/r.close-1)); e2=max(0,float(r.sma50/r.close-1)); hot=max(0,28-float(r.rsi14))/18
    return clip(45*min(1,e/.12)+35*min(1,e2/.22)+20*min(1,hot))


def add_engine_scores(x):
    z=x.copy(); vals=[]
    for _,r in z.iterrows():
        fl=50 if pd.isna(r.get('fractal_long_prior',np.nan)) else float(r.get('fractal_long_prior'))
        fs=50 if pd.isna(r.get('fractal_short_prior',np.nan)) else float(r.get('fractal_short_prior'))
        lr=clip(.36*float(r.long_candidate_v11)+.30*float(r.long_turn)+.14*recov(r,'LONG')+.10*float(r.wave_l)+.10*fl)
        sr=clip(.36*float(r.short_candidate_v11)+.30*float(r.short_turn)+.14*recov(r,'SHORT')+.10*float(r.wave_s)+.10*fs)
        ml=ma_structure(r,'LONG'); ms=ma_structure(r,'SHORT'); el=energy(r,'LONG'); es=energy(r,'SHORT')
        moml=momentum(r,'LONG'); moms=momentum(r,'SHORT'); mpl=maturity_penalty(r,'LONG'); mps=maturity_penalty(r,'SHORT')
        lc=clip(.38*ml+.27*el+.15*moml+.10*fl+.10*float(r.candle_l)-.22*mpl)
        sc=clip(.38*ms+.27*es+.15*moms+.10*fs+.10*float(r.candle_s)-.22*mps)
        vals.append((lr,lc,sr,sc,ml,ms,el,es,mpl,mps))
    z[['LONG_REVERSAL','LONG_CONTINUATION','SHORT_REVERSAL','SHORT_CONTINUATION','MA_LONG','MA_SHORT','ENERGY_LONG','ENERGY_SHORT','MATURITY_LONG','MATURITY_SHORT']]=pd.DataFrame(vals,index=z.index)
    return z


def first_passage(D,t,price,engine):
    if engine=='LONG_REVERSAL': horizon=180; up=.30; dn=.15; good_up=True
    elif engine=='SHORT_REVERSAL': horizon=180; up=.15; dn=.25; good_up=False
    elif engine=='LONG_CONTINUATION': horizon=120; up=.20; dn=.12; good_up=True
    else: horizon=120; up=.12; dn=.20; good_up=False
    fut=D[(D.index>t)&(D.index<=t+pd.Timedelta(days=horizon))]
    for _,r in fut.iterrows():
        hit_up=r.high>=price*(1+up); hit_dn=r.low<=price*(1-dn)
        if hit_up and hit_dn: return np.nan
        if hit_up: return 1.0 if good_up else 0.0
        if hit_dn: return 0.0 if good_up else 1.0
    return np.nan


def independent_samples(z,D):
    tmp=z[z.index.year>=2020].copy(); rows=[]; last=None
    for t,r in tmp.iterrows():
        if last is not None and (t-last).days<21: continue
        last=t; p=float(r.close)
        for e in ['LONG_REVERSAL','LONG_CONTINUATION','SHORT_REVERSAL','SHORT_CONTINUATION']:
            rows.append({'time':t,'engine':e,'score':float(r[e]),'price':p,'outcome':first_passage(D,t,p,e)})
    return pd.DataFrame(rows)


def summarize_engine(S,e):
    q=S[(S.engine==e)&S.outcome.notna()].copy(); buckets=[]
    for lo,hi in [(0,49),(50,64),(65,79),(80,100)]:
        g=q[(q.score>=lo)&(q.score<=hi)]; buckets.append({'bucket':f'{lo}-{hi}','n':len(g),'success_pct':round(float(g.outcome.mean()*100),2) if len(g) else None})
    low=q[q.score<65]; high=q[q.score>=65]
    comp={'evaluable':len(q),'low_n':len(low),'low_success_pct':round(float(low.outcome.mean()*100),2) if len(low) else None,'high_n':len(high),'high_success_pct':round(float(high.outcome.mean()*100),2) if len(high) else None,'high_minus_low_pp':round(float((high.outcome.mean()-low.outcome.mean())*100),2) if len(low) and len(high) else None}
    return {'buckets':buckets,'comparison':comp,'pass':bool(len(high)>=20 and (comp['high_success_pct'] or 0)>=55 and (comp['high_minus_low_pp'] or -999)>=5)}


def selector_test(z,D):
    rows=[]; last=None
    for t,r in z[z.index.year>=2020].iterrows():
        if last is not None and (t-last).days<21: continue
        last=t
        engines=['LONG_REVERSAL','LONG_CONTINUATION','SHORT_REVERSAL','SHORT_CONTINUATION']; vals={e:float(r[e]) for e in engines}; e=max(vals,key=vals.get); s=vals[e]
        o=first_passage(D,t,float(r.close),e); rows.append({'time':t,'engine':e,'score':s,'outcome':o})
    R=pd.DataFrame(rows); ev=R[R.outcome.notna()]; high=ev[ev.score>=65]
    return R,{'evaluable':len(ev),'selected_success_pct':round(float(ev.outcome.mean()*100),2) if len(ev) else None,'high_n':len(high),'high_success_pct':round(float(high.outcome.mean()*100),2) if len(high) else None}


def main():
    D,x,T,fail=prepare(); z=add_engine_scores(x); S=independent_samples(z,D); S.to_csv(OUT/'four_engine_samples.csv',index=False); z.tail(900).to_csv(OUT/'recent_four_engine_scores.csv')
    stats={e:summarize_engine(S,e) for e in ['LONG_REVERSAL','LONG_CONTINUATION','SHORT_REVERSAL','SHORT_CONTINUATION']}
    sel,ss=selector_test(z,D); sel.to_csv(OUT/'selector_samples.csv',index=False)
    d={'engine':'MASTER_BTC_TREND_V3_PHASE2_FOUR_ENGINE_V0_1','status':'RESEARCH_ONLY_NOT_LIVE','data_failures':fail,'score_design':{'LONG_REVERSAL':'origin candidate + turn + recovery + wave exhaustion + price-fractal soft prior','LONG_CONTINUATION':'MA/price structure + wave/flow energy + momentum + fractal path - maturity','SHORT_REVERSAL':'mirror of LONG reversal','SHORT_CONTINUATION':'mirror of LONG continuation'},'outcomes':{'LONG_REVERSAL':'+30% before -15% within 180D','SHORT_REVERSAL':'-25% before +15% within 180D','LONG_CONTINUATION':'+20% before -12% within 120D','SHORT_CONTINUATION':'-20% before +12% within 120D'},'stats':stats,'selector':ss,'overall_stage':'PHASE2_V01_PASS' if all(v['pass'] for v in stats.values()) else 'RESEARCH_PARTIAL_OR_FAIL','master_btc_trend_modified':False,'integration':'FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL','note':'This phase measures opportunity-engine separation only. Entry/position sizing remains blocked until Zone Quality validation passes.'}
    (OUT/'summary.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(d,ensure_ascii=False))

if __name__=='__main__': main()
