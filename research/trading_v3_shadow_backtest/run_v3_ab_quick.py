from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

import run_daily_entry_v3_shadow as bt

# -----------------------------------------------------------------------------
# MASTER TRADING DAILY ENTRY ENGINE V3 — QUICK A/B FILTER BACKTEST
# Research only. Production canonical/UI unchanged.
#
# Purpose:
#   Compare the CURRENT MASTER core proxy with the SAME executed trade universe
#   after V3 filters are applied. This avoids pretending we can reconstruct old
#   MASTER trades that were never durably stored.
#
# Common execution for both versions:
#   4H regime -> 1H structural zone -> 15m completed trigger -> structural SL
#   -> core target >=3R -> 24h evaluation.
#
# V3 changes only candidate quality filtering in this quick test:
#   MTF structure/location + session VWAP + volume/taker/CVD + momentum +
#   volatility/non-chase + Fib retracement confluence.
# Fib is auxiliary only: max +5 points; it cannot create Entry/SL/TP.
# -----------------------------------------------------------------------------

OUT = Path('research/trading_v3_shadow_backtest/ab_quick_output')
OUT.mkdir(parents=True, exist_ok=True)

# Shorter, still useful window for speed: 2025 development + 2026 OOS.
bt.START = pd.Timestamp('2024-10-01T00:00:00Z')  # warmup
bt.TRAIN_START = pd.Timestamp('2025-01-01T00:00:00Z')
bt.END = pd.Timestamp('2026-09-01T00:00:00Z')
bt.TOUCH_SEARCH_HOURS = 8
bt.TRIGGER_SEARCH_BARS = 8
bt.HORIZON_HOURS = 24

ASSETS = ['BTCUSDT','ETHUSDT']
V3_SCORE_MIN = 65.0


def before(df, t):
    q=df[df.index<=t]
    return None if q.empty else q.iloc[-1]


def baseline_setups(asset,h1,h4,d1):
    out=[]; last=-999
    for i,(t,r) in enumerate(h1.iterrows()):
        if t < bt.TRAIN_START or i<220 or i-last<6:
            continue
        r4=before(h4,t); rd=before(d1,t)
        if r4 is None or rd is None:
            continue
        vals=[r.ema20,r.ma50,r.atr,r.lo12,r.hi12,r4.ema20,r4.ma50]
        if any(pd.isna(x) for x in vals):
            continue
        reg=bt.regime_4h(r4)
        long_ok=(reg=='BULL_TREND' and r.close>r.ema20 and r.ema20>=r.ma50*0.99)
        short_ok=(reg=='BEAR_TREND' and r.close<r.ema20 and r.ema20<=r.ma50*1.01)
        if not (long_ok or short_ok):
            continue
        direction='LONG' if long_ok else 'SHORT'
        av=float(r.atr)
        if av<=0: continue
        if direction=='LONG':
            refs=[float(r.ema20),float(r.ma50),float(r4.ema20),float(r4.ma50),float(r.lo12)]
            refs=[x for x in refs if x<=r.close and r.close-x<=1.25*av]
            if not refs: continue
            center=max(refs)
            structure=min(float(r.lo12),center-0.55*av)
            stop=structure-0.10*av
            risk=center-stop
            if risk<=0 or risk/center>0.06: continue
            tp1=center+risk; tp2=center+3*risk; tp3=center+4*risk
        else:
            refs=[float(r.ema20),float(r.ma50),float(r4.ema20),float(r4.ma50),float(r.hi12)]
            refs=[x for x in refs if x>=r.close and x-r.close<=1.25*av]
            if not refs: continue
            center=min(refs)
            structure=max(float(r.hi12),center+0.55*av)
            stop=structure+0.10*av
            risk=stop-center
            if risk<=0 or risk/center>0.06: continue
            tp1=center-risk; tp2=center-3*risk; tp3=center-4*risk
        half=0.35*av
        out.append(bt.Setup(asset,'BASELINE',t,direction,float(center),float(center-half),float(center+half),
                            float(stop),float(tp1),float(tp2),float(tp3),np.nan,1.0,reg,False,0.0,
                            'MTF_TREND+EMA_MA+RECENT_STRUCTURE'))
        last=i
    return out


def fib_bonus(h4,t,direction,center,atr1):
    q=h4[h4.index<=t].tail(36)
    if len(q)<12 or atr1<=0:
        return 0.0
    H=float(q.high.max()); L=float(q.low.min())
    if H<=L: return 0.0
    rng=H-L
    if direction=='LONG':
        levels=[H-rng*x for x in (0.382,0.5,0.618,0.786)]
    else:
        levels=[L+rng*x for x in (0.382,0.5,0.618,0.786)]
    return 5.0 if min(abs(center-x) for x in levels)<=0.30*atr1 else 0.0


def v3_score(setup,h1,h4,d1,sv1,pdl):
    t=setup.t
    r=before(h1,t); r4=before(h4,t); rd=before(d1,t); pdv=before(pdl,t)
    if r is None or r4 is None or rd is None or pdv is None:
        return np.nan,np.nan
    av=float(r.atr) if pd.notna(r.atr) else 0.0
    if av<=0: return np.nan,np.nan
    d=setup.direction; c=setup.entry_center
    long=(d=='LONG')

    # 1) MTF structure/regime 20
    score=0.0
    score += 8 if ((long and setup.regime=='BULL_TREND') or ((not long) and setup.regime=='BEAR_TREND')) else 0
    score += 6 if ((long and r.ema20>=r.ma50) or ((not long) and r.ema20<=r.ma50)) else 0
    score += 6 if ((long and rd.close>=rd.ema20) or ((not long) and rd.close<=rd.ema20)) else 0

    # 2) Location/confluence 20
    score += 5 if abs(c-float(r.ema20))<=0.30*av else 0
    score += 4 if abs(c-float(r.ma50))<=0.55*av else 0
    score += 4 if abs(c-float(r4.ema20))<=0.70*av else 0
    sv=sv1.loc[:t].iloc[-1] if len(sv1.loc[:t]) else np.nan
    score += 4 if pd.notna(sv) and abs(c-float(sv))<=0.55*av else 0
    pd_ref=float(pdv.pdl if long else pdv.pdh)
    score += 3 if pd.notna(pd_ref) and abs(c-pd_ref)<=0.75*av else 0

    # 3) Participation/order-flow 20
    rv=float(r.rv) if pd.notna(r.rv) else 0.0
    score += 8 if rv>=1.0 else (5 if rv>=0.8 else 2)
    imb=float(r.taker_imb4) if pd.notna(r.taker_imb4) else 0.0
    score += 7 if ((long and imb>0) or ((not long) and imb<0)) else 1
    cvd=float(r.cvd12) if pd.notna(r.cvd12) else 0.0
    score += 5 if ((long and cvd>0) or ((not long) and cvd<0)) else 0

    # 4) Momentum / non-hot 15
    rr=float(r.rsi) if pd.notna(r.rsi) else 50.0
    if long:
        score += 5 if 45<=rr<=68 else (2 if rr<75 else 0)
    else:
        score += 5 if 32<=rr<=55 else (2 if rr>25 else 0)
    mh=float(r.macdh) if pd.notna(r.macdh) else 0.0
    score += 5 if ((long and mh>=0) or ((not long) and mh<=0)) else 0
    kk=float(r.kdjk) if pd.notna(r.kdjk) else 50.0
    score += 5 if ((long and kk<80) or ((not long) and kk>20)) else 0

    # 5) Volatility / non-chase 20
    rank=float(r.atr_pct_rank) if pd.notna(r.atr_pct_rank) else 0.5
    score += 8 if 0.15<=rank<=0.85 else 3
    dist=abs(float(r.close)-c)/av
    score += 7 if dist<=0.60 else (3 if dist<=1.0 else 0)
    score += 5 if ((long and r.close>=c) or ((not long) and r.close<=c)) else 0

    nofib=score
    fib=fib_bonus(h4,t,d,c,av)
    return float(nofib),float(nofib+fib)


def metrics(x):
    if x.empty: return {'n':0}
    r=x.R.astype(float)
    neg=-r[r<0].sum()
    pf=float(r[r>0].sum()/neg) if neg>0 else float('inf')
    eq=r.cumsum(); dd=float((eq-eq.cummax()).min())
    return {
        'n':int(len(x)),'expectancy_R':float(r.mean()),'PF':pf,'maxDD_R':dd,
        'win_rate':float((r>0).mean()),'sl_rate':float((x.outcome=='SL').mean()),
        'false_start_rate':float(x.false_start.mean()),'tp2_rate':float(x.tp2_hit.mean()),
        'avg_MFE_R':float(x.MFE.mean()),'avg_MAE_R':float(x.MAE.mean())
    }


def main():
    rows=[]; raw_all={}; data={}; download_failures=[]
    for asset in ASSETS:
        m15,bad=bt.load_15m(asset); download_failures+=bad
        m15=bt.add_session_vwap(bt.enrich(m15))
        h1=bt.enrich(bt.resample(m15,'1h')); h4=bt.enrich(bt.resample(m15,'4h')); d1=bt.enrich(bt.resample(m15,'1d'))
        sv1=m15.session_vwap.resample('1h',label='right',closed='right').last().dropna()
        pdl=bt.prior_day_levels(d1)
        raw_all[asset]=m15; data[asset]=(h1,h4,d1,sv1,pdl)

    for asset in ASSETS:
        h1,h4,d1,sv1,pdl=data[asset]
        setups=baseline_setups(asset,h1,h4,d1)
        trades,_miss=bt.replay(setups,raw_all[asset])
        if trades.empty: continue
        # Map setup time -> setup for score lookup.
        smap={s.t:s for s in setups}
        for _,tr in trades.iterrows():
            s=smap.get(tr.setup_time)
            if s is None: continue
            nf,fb=v3_score(s,h1,h4,d1,sv1,pdl)
            base=tr.to_dict(); base['version']='BASELINE'; base['v3_nofib_score']=nf; base['v3_fib_score']=fb
            rows.append(base)
            if pd.notna(nf) and nf>=V3_SCORE_MIN:
                x=tr.to_dict(); x['version']='V3_NOFIB'; x['v3_nofib_score']=nf; x['v3_fib_score']=fb; rows.append(x)
            if pd.notna(fb) and fb>=V3_SCORE_MIN:
                x=tr.to_dict(); x['version']='V3_FIB'; x['v3_nofib_score']=nf; x['v3_fib_score']=fb; rows.append(x)

    alltr=pd.DataFrame(rows)
    if alltr.empty: raise RuntimeError('no executed trades generated')
    alltr['split']=np.where(pd.to_datetime(alltr.entry_time,utc=True)<pd.Timestamp('2026-01-01T00:00:00Z'),'DEV','OOS')

    result={}
    for split in ['DEV','OOS']:
        result[split]={}
        for v in ['BASELINE','V3_NOFIB','V3_FIB']:
            result[split][v]=metrics(alltr[(alltr.split==split)&(alltr.version==v)])
    asset_metrics={}
    for a in ASSETS:
        asset_metrics[a]={v:metrics(alltr[(alltr.split=='OOS')&(alltr.asset==a)&(alltr.version==v)]) for v in ['BASELINE','V3_NOFIB','V3_FIB']}
    dir_metrics={d:{v:metrics(alltr[(alltr.split=='OOS')&(alltr.direction==d)&(alltr.version==v)]) for v in ['BASELINE','V3_NOFIB','V3_FIB']} for d in ['LONG','SHORT']}

    b=result['OOS']['BASELINE']; v=result['OOS']['V3_FIB']; nf=result['OOS']['V3_NOFIB']
    enough=b.get('n',0)>=20 and v.get('n',0)>=10
    core=[]
    if enough:
        core=[v['expectancy_R']>b['expectancy_R'],v['PF']>b['PF'],abs(v['maxDD_R'])<=abs(b['maxDD_R'])*1.20+0.5,v['false_start_rate']<=b['false_start_rate']+0.05]
    if enough and sum(core)>=3 and core[0] and core[1]: verdict='QUICK FAVOR V3 — FINAL CONFIRMATION CANDIDATE'
    elif enough: verdict='QUICK NOT FAVOR V3 — KEEP BASELINE FOR NOW'
    else: verdict='QUICK INCONCLUSIVE — SAMPLE LOW'

    fib_effect='NEUTRAL'
    if nf.get('n',0)>=8 and v.get('n',0)>=8:
        if v['expectancy_R']>nf['expectancy_R'] and v['PF']>=nf['PF']*0.95: fib_effect='HELPFUL'
        elif v['expectancy_R']<nf['expectancy_R'] and v['PF']<nf['PF']: fib_effect='HARMFUL'

    summary={
        'method':'same executed baseline universe; V3 acts as a quality filter; point-in-time proxy, not reconstructed historical MASTER trades',
        'window':'2025-01-01..2026-08-31 (warmup from 2024-10-01)','score_min':V3_SCORE_MIN,
        'download_failures':download_failures,'result':result,'asset_metrics':asset_metrics,'direction_metrics':dir_metrics,
        'fib_effect':fib_effect,'verdict':verdict,
        'limitations':['OI/funding/depth/liquidation heatmap/on-chain/ETF/whale/options historical axes are N/A in this quick A/B','AVWAP/Volume Profile omitted from QUICK A/B for speed; reserved for final confirmation']
    }
    alltr.to_csv(OUT/'trades.csv',index=False)
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2,default=str),encoding='utf-8')
    lines=['# Trading V3 QUICK A/B','',f'**{verdict}**','',f'Fib effect: **{fib_effect}**','',
           '| Split | Version | n | ExpR | PF | MaxDD | Win | SL | FalseStart |',
           '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for split in ['DEV','OOS']:
        for ver in ['BASELINE','V3_NOFIB','V3_FIB']:
            m=result[split][ver]
            lines.append(f"| {split} | {ver} | {m.get('n',0)} | {m.get('expectancy_R',float('nan')):.3f} | {m.get('PF',float('nan')):.2f} | {m.get('maxDD_R',float('nan')):.2f} | {m.get('win_rate',float('nan')):.1%} | {m.get('sl_rate',float('nan')):.1%} | {m.get('false_start_rate',float('nan')):.1%} |")
    (OUT/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(verdict)
    print('FIB_EFFECT',fib_effect)
    print(pd.DataFrame([{ 'split':s,'version':v,**result[s][v]} for s in ['DEV','OOS'] for v in ['BASELINE','V3_NOFIB','V3_FIB']]).to_string(index=False))


if __name__=='__main__':
    main()
