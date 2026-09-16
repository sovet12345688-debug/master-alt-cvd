from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

import run_daily_entry_v3_shadow as bt
import run_v3_ab_quick as finalv3

OUT = Path('research/trading_v3_shadow_backtest/feature_attribution_output')
OUT.mkdir(parents=True, exist_ok=True)

bt.START = pd.Timestamp('2025-03-01T00:00:00Z')
bt.TRAIN_START = pd.Timestamp('2025-06-01T00:00:00Z')
bt.END = pd.Timestamp('2026-09-01T00:00:00Z')
DEV_END = pd.Timestamp('2026-01-01T00:00:00Z')


def before(df, t):
    q = df[df.index <= t]
    return None if q.empty else q.iloc[-1]


def metrics(x):
    if x.empty:
        return {'n':0}
    r=x.R.astype(float); neg=-r[r<0].sum(); pos=r[r>0].sum(); pf=float(pos/neg) if neg>0 else float('inf')
    eq=r.cumsum(); dd=float((eq-eq.cummax()).min())
    return {'n':int(len(x)),'expectancy_R':float(r.mean()),'PF':pf,'maxDD_R':dd,
            'win_rate':float((r>0).mean()),'sl_rate':float((x.outcome=='SL').mean()),
            'false_start_rate':float(x.false_start.mean()),'avg_MFE_R':float(x.MFE.mean()),'avg_MAE_R':float(x.MAE.mean())}


def main():
    m15, failures = bt.load_15m('BTCUSDT')
    m15=bt.add_session_vwap(bt.enrich(m15))
    h1=bt.enrich(bt.resample(m15,'1h')); h4=bt.enrich(bt.resample(m15,'4h')); d1=bt.enrich(bt.resample(m15,'1d'))
    sv1=m15.session_vwap.resample('1h',label='right',closed='right').last().dropna(); pdl=bt.prior_day_levels(d1)
    u=finalv3.make_common_universe('BTCUSDT',m15,h1,h4,d1,sv1,pdl)
    if u.empty: raise RuntimeError('BTC common universe empty')
    u=u[pd.to_datetime(u.setup_time,utc=True)>=DEV_END].copy()

    feats=[]
    for idx,row in u.iterrows():
        t=row.setup_time; d=row.direction; long=d=='LONG'; r=before(h1,t); r4=before(h4,t); rd=before(d1,t); pdv=before(pdl,t)
        if r is None or r4 is None or rd is None or pdv is None or pd.isna(r.atr) or r.atr<=0:
            continue
        av=float(r.atr); px=float(r.close)
        sv=sv1.loc[:t].iloc[-1] if len(sv1.loc[:t]) else np.nan
        pd_ref=float(pdv.pdl if long else pdv.pdh)
        imb=float(r.taker_imb4) if pd.notna(r.taker_imb4) else 0.0
        cvd=float(r.cvd12) if pd.notna(r.cvd12) else 0.0
        rv=float(r.rv) if pd.notna(r.rv) else 0.0
        rr=float(r.rsi) if pd.notna(r.rsi) else 50.0
        mh=float(r.macdh) if pd.notna(r.macdh) else 0.0
        kk=float(r.kdjk) if pd.notna(r.kdjk) else 50.0
        rank=float(r.atr_pct_rank) if pd.notna(r.atr_pct_rank) else 0.5
        feature={
            'daily_align': bool((rd.close>=rd.ema20) if long else (rd.close<=rd.ema20)),
            'h1_ema_ma_align': bool((r.ema20>=r.ma50) if long else (r.ema20<=r.ma50)),
            'ema20_near': abs(px-float(r.ema20))<=0.65*av,
            'ma50_near': abs(px-float(r.ma50))<=0.90*av,
            'h4_ema20_near': abs(px-float(r4.ema20))<=1.20*av,
            'session_vwap_near': bool(pd.notna(sv) and abs(px-float(sv))<=0.75*av),
            'prior_day_level_near': bool(pd.notna(pd_ref) and abs(px-pd_ref)<=1.0*av),
            'rv_ge_08': rv>=0.8,
            'rv_ge_10': rv>=1.0,
            'taker_align': bool((imb>0) if long else (imb<0)),
            'cvd_align': bool((cvd>0) if long else (cvd<0)),
            'both_flow_align': bool(((imb>0 and cvd>0) if long else (imb<0 and cvd<0))),
            'rsi_quality': bool((45<=rr<=68) if long else (32<=rr<=55)),
            'macd_align': bool((mh>=0) if long else (mh<=0)),
            'kdj_nonhot': bool((kk<80) if long else (kk>20)),
            'atr_mid': 0.15<=rank<=0.85,
            'extension_le_06atr': abs(px-float(r.ema20))/av<=0.60,
            'price_ma50_side': bool((px>=r.ma50) if long else (px<=r.ma50)),
        }
        feats.append((idx,feature))

    for idx,feature in feats:
        for k,v in feature.items():
            u.loc[idx,k]=bool(v)

    feature_names=list(feats[0][1].keys()) if feats else []
    attribution={}
    useful=[]
    for f in feature_names:
        yes=metrics(u[u[f]==True]); no=metrics(u[u[f]==False])
        de=yes.get('expectancy_R',np.nan)-no.get('expectancy_R',np.nan) if yes.get('n',0) and no.get('n',0) else np.nan
        dpf=yes.get('PF',np.nan)-no.get('PF',np.nan) if yes.get('n',0) and no.get('n',0) else np.nan
        dmfe=yes.get('avg_MFE_R',np.nan)-no.get('avg_MFE_R',np.nan) if yes.get('n',0) and no.get('n',0) else np.nan
        dmae=yes.get('avg_MAE_R',np.nan)-no.get('avg_MAE_R',np.nan) if yes.get('n',0) and no.get('n',0) else np.nan
        attribution[f]={'pass':yes,'fail':no,'delta_expectancy_R':de,'delta_PF':dpf,'delta_MFE_R':dmfe,'delta_MAE_R':dmae}
        if yes.get('n',0)>=40 and no.get('n',0)>=40 and pd.notna(de) and de>0 and pd.notna(dpf) and dpf>0:
            useful.append(f)

    # Count only empirically useful features. No Fib, no arbitrary composite weights.
    u['edge_count']=0
    for f in useful:
        u['edge_count'] += u[f].fillna(False).astype(int)
    count_metrics={str(k):metrics(u[u.edge_count==k]) for k in sorted(u.edge_count.unique())}
    base=metrics(u)

    summary={'method':'BTC OOS feature attribution on preserved baseline setup universe','window':'2026 OOS only','download_failures':failures,
             'baseline':base,'useful_features':useful,'attribution':attribution,'edge_count_metrics':count_metrics,
             'rule':'Only features with positive expectancy AND PF separation and >=40 samples on both pass/fail sides qualify for Baseline+ overlay. Fib excluded.',
             'production':'UNCHANGED'}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2,default=str),encoding='utf-8')
    u.to_csv(OUT/'btc_feature_rows.csv',index=False)

    print('USEFUL_FEATURES', useful)
    rows=[]
    for f in feature_names:
        a=attribution[f]
        rows.append({'feature':f,'n_pass':a['pass'].get('n',0),'exp_pass':a['pass'].get('expectancy_R',np.nan),'exp_fail':a['fail'].get('expectancy_R',np.nan),'dExp':a['delta_expectancy_R'],'pf_pass':a['pass'].get('PF',np.nan),'pf_fail':a['fail'].get('PF',np.nan),'dPF':a['delta_PF'],'dMFE':a['delta_MFE_R'],'dMAE':a['delta_MAE_R']})
    print(pd.DataFrame(rows).sort_values('dExp',ascending=False).to_string(index=False))

if __name__=='__main__': main()
