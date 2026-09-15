from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

import run_daily_entry_v3_shadow as bt

# MASTER TRADING DAILY ENTRY ENGINE V3 — FAST PAIRED FILTER TEST
# Research only. Production canonical/UI unchanged.
# Both BASELINE and V3 use the SAME point-in-time setup/outcome universe.
# V3 is tested only as a quality filter. If it wins, one stricter confirmation follows.

OUT = Path('research/trading_v3_shadow_backtest/ab_quick_output')
OUT.mkdir(parents=True, exist_ok=True)
ASSETS = ['BTCUSDT', 'ETHUSDT']
bt.START = pd.Timestamp('2025-03-01T00:00:00Z')
bt.TRAIN_START = pd.Timestamp('2025-06-01T00:00:00Z')
bt.END = pd.Timestamp('2026-09-01T00:00:00Z')
DEV_END = pd.Timestamp('2026-01-01T00:00:00Z')
HORIZON_HOURS = 24
COOLDOWN_HOURS = 6
THRESHOLDS = [55.0, 60.0, 65.0, 70.0]


def before(df, t):
    q = df[df.index <= t]
    return None if q.empty else q.iloc[-1]


def fib_bonus(h4, t, direction, price, atr1):
    # Auxiliary only: never creates Entry/SL/TP.
    q = h4[h4.index <= t].tail(36)
    if len(q) < 12 or atr1 <= 0:
        return 0.0
    H, L = float(q.high.max()), float(q.low.min())
    if H <= L:
        return 0.0
    rng = H - L
    levels = ([H-rng*x for x in (0.382,0.5,0.618,0.786)] if direction=='LONG'
              else [L+rng*x for x in (0.382,0.5,0.618,0.786)])
    return 5.0 if min(abs(price-x) for x in levels) <= 0.30*atr1 else 0.0


def v3_score(t, direction, h1, h4, d1, sv1, pdl):
    r, r4, rd, pdv = before(h1,t), before(h4,t), before(d1,t), before(pdl,t)
    if r is None or r4 is None or rd is None or pdv is None or pd.isna(r.atr) or r.atr <= 0:
        return np.nan, np.nan
    long = direction == 'LONG'; av = float(r.atr); px = float(r.close); score = 0.0

    # Regime + multi-TF structure 20
    reg = bt.regime_4h(r4)
    score += 8 if ((long and reg=='BULL_TREND') or ((not long) and reg=='BEAR_TREND')) else 0
    score += 6 if ((long and r.ema20 >= r.ma50) or ((not long) and r.ema20 <= r.ma50)) else 0
    score += 6 if ((long and rd.close >= rd.ema20) or ((not long) and rd.close <= rd.ema20)) else 0

    # Location/value 20
    score += 5 if abs(px-float(r.ema20)) <= 0.65*av else 1
    score += 4 if abs(px-float(r.ma50)) <= 0.90*av else 0
    score += 4 if abs(px-float(r4.ema20)) <= 1.20*av else 0
    sv = sv1.loc[:t].iloc[-1] if len(sv1.loc[:t]) else np.nan
    score += 4 if pd.notna(sv) and abs(px-float(sv)) <= 0.75*av else 0
    pd_ref = float(pdv.pdl if long else pdv.pdh)
    score += 3 if pd.notna(pd_ref) and abs(px-pd_ref) <= 1.00*av else 0

    # Volume / taker / CVD proxy 20
    rv = float(r.rv) if pd.notna(r.rv) else 0.0
    score += 8 if rv >= 1.0 else (5 if rv >= 0.8 else 2)
    imb = float(r.taker_imb4) if pd.notna(r.taker_imb4) else 0.0
    score += 7 if ((long and imb > 0) or ((not long) and imb < 0)) else 1
    cvd = float(r.cvd12) if pd.notna(r.cvd12) else 0.0
    score += 5 if ((long and cvd > 0) or ((not long) and cvd < 0)) else 0

    # Momentum/non-hot 15
    rr = float(r.rsi) if pd.notna(r.rsi) else 50.0
    score += (5 if 45 <= rr <= 68 else (2 if rr < 75 else 0)) if long else (5 if 32 <= rr <= 55 else (2 if rr > 25 else 0))
    mh = float(r.macdh) if pd.notna(r.macdh) else 0.0
    score += 5 if ((long and mh >= 0) or ((not long) and mh <= 0)) else 0
    kk = float(r.kdjk) if pd.notna(r.kdjk) else 50.0
    score += 5 if ((long and kk < 80) or ((not long) and kk > 20)) else 0

    # Volatility + extension 20
    rank = float(r.atr_pct_rank) if pd.notna(r.atr_pct_rank) else 0.5
    score += 8 if 0.15 <= rank <= 0.85 else 3
    ext = abs(px-float(r.ema20))/av
    score += 7 if ext <= 0.60 else (3 if ext <= 1.0 else 0)
    score += 5 if ((long and px >= r.ma50) or ((not long) and px <= r.ma50)) else 0

    nofib = float(score)
    return nofib, nofib + fib_bonus(h4,t,direction,px,av)


def outcome_from_setup(t, direction, h1, m15):
    # Common execution proxy: entry at next 15m open, structural 12H SL, core TP=3R.
    r = before(h1,t)
    if r is None or pd.isna(r.atr) or r.atr <= 0:
        return None
    z = m15[(m15.index > t) & (m15.index <= t + pd.Timedelta(hours=HORIZON_HOURS))]
    if len(z) < 8:
        return None
    ep = float(z.open.iloc[0]); av = float(r.atr)
    if direction == 'LONG':
        stop = float(r.lo12) - 0.10*av; risk = ep-stop
    else:
        stop = float(r.hi12) + 0.10*av; risk = stop-ep
    if risk <= 0 or risk/ep > 0.06 or risk < 0.20*av:
        return None
    tp = ep + 3*risk if direction=='LONG' else ep - 3*risk
    if direction == 'LONG':
        si=np.where(z.low.values<=stop)[0]; ti=np.where(z.high.values>=tp)[0]
        mfe=(float(z.high.max())-ep)/risk; mae=(ep-float(z.low.min()))/risk; final_r=(float(z.close.iloc[-1])-ep)/risk
    else:
        si=np.where(z.high.values>=stop)[0]; ti=np.where(z.low.values<=tp)[0]
        mfe=(ep-float(z.low.min()))/risk; mae=(float(z.high.max())-ep)/risk; final_r=(ep-float(z.close.iloc[-1]))/risk
    sidx=int(si[0]) if len(si) else None; tidx=int(ti[0]) if len(ti) else None
    if tidx is not None and (sidx is None or tidx<sidx): R,outcome=3.0,'TP3R'
    elif sidx is not None and (tidx is None or sidx<tidx): R,outcome=-1.0,'SL'
    else: R,outcome=float(np.clip(final_r,-1.0,3.0)),'TIME'
    return dict(entry=ep,stop=stop,tp=tp,R=R,outcome=outcome,MFE=float(mfe),MAE=float(mae),false_start=bool(outcome=='SL' and mfe<0.5))


def make_common_universe(asset, m15, h1, h4, d1, sv1, pdl):
    rows=[]; last_t=None
    for i,(t,r) in enumerate(h1.iterrows()):
        if t < bt.TRAIN_START or i < 220: continue
        if last_t is not None and (t-last_t) < pd.Timedelta(hours=COOLDOWN_HOURS): continue
        r4=before(h4,t)
        if r4 is None or any(pd.isna(x) for x in [r.ema20,r.ma50,r.atr,r4.ema20,r4.ma50]): continue
        reg=bt.regime_4h(r4)
        long_ok=reg=='BULL_TREND' and r.close>r.ema20 and r.ema20>=r.ma50*0.99
        short_ok=reg=='BEAR_TREND' and r.close<r.ema20 and r.ema20<=r.ma50*1.01
        if not (long_ok or short_ok): continue
        direction='LONG' if long_ok else 'SHORT'
        ex=outcome_from_setup(t,direction,h1,m15)
        if ex is None: continue
        nf,fb=v3_score(t,direction,h1,h4,d1,sv1,pdl)
        if pd.isna(nf): continue
        rows.append(dict(asset=asset,setup_time=t,direction=direction,regime=reg,v3_nofib_score=nf,v3_fib_score=fb,**ex)); last_t=t
    return pd.DataFrame(rows)


def metrics(x):
    if x.empty: return {'n':0}
    r=x.R.astype(float); neg=-r[r<0].sum(); pos=r[r>0].sum(); pf=float(pos/neg) if neg>0 else float('inf')
    eq=r.cumsum(); dd=float((eq-eq.cummax()).min())
    return {'n':int(len(x)),'expectancy_R':float(r.mean()),'PF':pf,'maxDD_R':dd,'win_rate':float((r>0).mean()),
            'sl_rate':float((x.outcome=='SL').mean()),'false_start_rate':float(x.false_start.mean()),
            'avg_MFE_R':float(x.MFE.mean()),'avg_MAE_R':float(x.MAE.mean())}


def choose_threshold(dev, score_col):
    best=None
    for th in THRESHOLDS:
        x=dev[dev[score_col]>=th]; m=metrics(x)
        if m.get('n',0)<30: continue
        val=m['expectancy_R']+0.10*min(m['PF'],3.0)+0.01*m['maxDD_R']; cand=(val,m['n'],th)
        if best is None or cand>best: best=cand
    return 60.0 if best is None else float(best[2])


def main():
    parts=[]; failures=[]
    for asset in ASSETS:
        m15,bad=bt.load_15m(asset); failures+=bad; m15=bt.add_session_vwap(bt.enrich(m15))
        h1=bt.enrich(bt.resample(m15,'1h')); h4=bt.enrich(bt.resample(m15,'4h')); d1=bt.enrich(bt.resample(m15,'1d'))
        sv1=m15.session_vwap.resample('1h',label='right',closed='right').last().dropna(); pdl=bt.prior_day_levels(d1)
        q=make_common_universe(asset,m15,h1,h4,d1,sv1,pdl)
        if not q.empty: parts.append(q)
    if not parts: raise RuntimeError('common universe empty')
    u=pd.concat(parts,ignore_index=True); u['split']=np.where(pd.to_datetime(u.setup_time,utc=True)<DEV_END,'DEV','OOS')
    dev=u[u.split=='DEV']; oos=u[u.split=='OOS']; th_nf=choose_threshold(dev,'v3_nofib_score'); th_fb=choose_threshold(dev,'v3_fib_score')
    def subset(x,ver):
        if ver=='BASELINE': return x
        return x[x.v3_nofib_score>=th_nf] if ver=='V3_NOFIB' else x[x.v3_fib_score>=th_fb]
    result={s:{v:metrics(subset(u[u.split==s],v)) for v in ['BASELINE','V3_NOFIB','V3_FIB']} for s in ['DEV','OOS']}
    asset_metrics={a:{v:metrics(subset(oos[oos.asset==a],v)) for v in ['BASELINE','V3_NOFIB','V3_FIB']} for a in ASSETS}
    direction_metrics={d:{v:metrics(subset(oos[oos.direction==d],v)) for v in ['BASELINE','V3_NOFIB','V3_FIB']} for d in ['LONG','SHORT']}
    b=result['OOS']['BASELINE']; v=result['OOS']['V3_FIB']; nf=result['OOS']['V3_NOFIB']; enough=b.get('n',0)>=40 and v.get('n',0)>=15
    checks=[v['expectancy_R']>b['expectancy_R'],v['PF']>b['PF'],abs(v['maxDD_R'])<=abs(b['maxDD_R'])*1.20+0.5,v['false_start_rate']<=b['false_start_rate']+0.05] if enough else []
    verdict=('QUICK FAVOR V3 — FINAL CONFIRMATION CANDIDATE' if enough and checks[0] and checks[1] and sum(checks)>=3
             else 'QUICK NOT FAVOR V3 — KEEP BASELINE FOR NOW' if enough else 'QUICK INCONCLUSIVE — SAMPLE LOW')
    fib_effect='NEUTRAL'
    if nf.get('n',0)>=15 and v.get('n',0)>=15:
        if v['expectancy_R']>nf['expectancy_R'] and v['PF']>=nf['PF']*0.95: fib_effect='HELPFUL'
        elif v['expectancy_R']<nf['expectancy_R'] and v['PF']<nf['PF']: fib_effect='HARMFUL'
    summary={'method':'paired point-in-time quality-filter screen on same BTC/ETH setup universe','window':'2025-06-01..2026-08-31; DEV before 2026, OOS 2026',
             'thresholds':{'V3_NOFIB':th_nf,'V3_FIB':th_fb},'download_failures':failures,'result':result,'asset_metrics':asset_metrics,
             'direction_metrics':direction_metrics,'fib_effect':fib_effect,'verdict':verdict,
             'limitations':['quick screen validates V3 filtering power, not exact production Entry Zone simulation','OI/funding/depth/liquidation heatmap/on-chain/ETF/whale/options historical axes N/A','Fib retracement is auxiliary +5 only; Fib does not create Entry/SL/TP']}
    u.to_csv(OUT/'common_universe.csv',index=False); (OUT/'summary.json').write_text(json.dumps(summary,indent=2,default=str),encoding='utf-8')
    lines=['# Trading V3 FAST PAIRED A/B','',f'**{verdict}**','',f'Fib effect: **{fib_effect}**',f'Thresholds: nofib={th_nf:.0f}, fib={th_fb:.0f}','','| Split | Version | n | ExpR | PF | MaxDD | Win | SL | FalseStart |','|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for s in ['DEV','OOS']:
        for ver in ['BASELINE','V3_NOFIB','V3_FIB']:
            m=result[s][ver]; lines.append(f"| {s} | {ver} | {m.get('n',0)} | {m.get('expectancy_R',float('nan')):.3f} | {m.get('PF',float('nan')):.2f} | {m.get('maxDD_R',float('nan')):.2f} | {m.get('win_rate',float('nan')):.1%} | {m.get('sl_rate',float('nan')):.1%} | {m.get('false_start_rate',float('nan')):.1%} |")
    (OUT/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(verdict); print('FIB_EFFECT',fib_effect); print('THRESHOLDS',th_nf,th_fb)
    print(pd.DataFrame([{'split':s,'version':ver,**result[s][ver]} for s in ['DEV','OOS'] for ver in ['BASELINE','V3_NOFIB','V3_FIB']]).to_string(index=False))

if __name__=='__main__': main()
