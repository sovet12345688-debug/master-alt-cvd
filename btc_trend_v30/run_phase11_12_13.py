from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

import run_phase1_origin_chart_v02 as v02

OUT=Path('btc_trend_v30/output/phase11_12_13'); OUT.mkdir(parents=True,exist_ok=True)


def clip100(x): return max(0.0,min(100.0,float(x)))


def prepare():
    d0,fd=v02.load_interval('1d'); h0,f4=v02.load_interval('4h')
    D,H4=v02.enrich_context(v02.add_features(d0,'1D'),v02.add_features(h0,'4H'))
    S=v02.stage_scores(D,H4); T=v02.build_truth_events(D); T['time']=pd.to_datetime(T.time,utc=True)
    return D,H4,S,T,fd+f4


def phase11_scores(D,S):
    x=S.copy().join(D[['open','high','low','ema20','sma50','sma200','atr14','rsi14','ret3','ret30','ret60','low20_prev','high20_prev']])
    # Separate real exhaustion/base/retest/HL from ordinary mid-trend bounce.
    x['falling_knife_l']=((x.close<x.ema20)&(x.ret30<-0.12)&(x.ret3<-0.025)).astype(float)*100
    x['blowoff_s']=((x.close>x.ema20)&(x.ret30>0.18)&(x.ret3>0.03)).astype(float)*100
    x['base_l']=np.where((x.close>=x.low20_prev*0.99)&(x.close<=x.low20_prev*1.10)&(x.rsi14>=35),70,0)
    x['base_s']=np.where((x.close<=x.high20_prev*1.01)&(x.close>=x.high20_prev*0.90)&(x.rsi14<=65),70,0)
    x['hl_l']=np.where((x.low>x.low.shift(5).rolling(15).min())&(x.close>x.ema20),80,0)
    x['lh_s']=np.where((x.high<x.high.shift(5).rolling(15).max())&(x.close<x.ema20),80,0)
    x['retest_l']=np.where((x.low<=x.ema20*1.02)&(x.close>=x.ema20)&(x.close>x.open),85,0)
    x['retest_s']=np.where((x.high>=x.ema20*0.98)&(x.close<=x.ema20)&(x.close<x.open),85,0)
    x['long_candidate_v11']=(0.68*x.long_candidate+0.10*x.base_l+0.10*x.hl_l+0.12*x.retest_l-0.18*x.falling_knife_l).clip(0,100)
    x['short_candidate_v11']=(0.68*x.short_candidate+0.10*x.base_s+0.10*x.lh_s+0.12*x.retest_s-0.18*x.blowoff_s).clip(0,100)
    return x


def build_signals(x,side,cand_th,conf_th,fractal_col=None,fractal_th=0):
    cc='long_candidate_v11' if side=='LOW' else 'short_candidate_v11'; cf='long_confirm' if side=='LOW' else 'short_confirm'
    ctxc='prior_down_context' if side=='LOW' else 'prior_up_context'; locc='near_low' if side=='LOW' else 'near_high'
    out=[]; last=None
    for t,r in x.iterrows():
        if r[cf]<conf_th: continue
        hist=x[(x.index>=t-pd.Timedelta(days=21))&(x.index<=t)]
        if hist.empty: continue
        bt=hist[cc].idxmax(); b=hist.loc[bt]
        if b[cc]<cand_th or b[ctxc]<35 or b[locc]<35: continue
        if fractal_col and (pd.isna(b.get(fractal_col)) or float(b[fractal_col])<fractal_th): continue
        q=.60*b[cc]+.40*r[cf]
        sig={'origin_time':bt,'confirm_time':t,'origin_price':float(b.close),'confirm_price':float(r.close),'quality':float(q)}
        if fractal_col: sig['fractal_prior']=float(b[fractal_col])
        if last is None or (t-last).days>28: out.append(sig); last=t
        elif q>out[-1]['quality']: out[-1]=sig; last=t
    return out


def eval_sig(signals,T,side): return v02.evaluate(signals,T,side)


def oos_fixed(x,T,side,fractal_col=None):
    rows=[]
    for y in range(2020,2027):
        tr=x[x.index.year<y]; te=x[x.index.year==y]; tt=T[T.time.dt.year<y]; ty=T[T.time.dt.year==y]
        if len(tr)<500 or len(te)<100: continue
        best=None
        for ct in [45,50,55,60,65]:
            for ft in [50,55,60,65,70]:
                for fr in ([55,60,65] if fractal_col else [0]):
                    s=build_signals(tr,side,ct,ft,fractal_col,fr); e=eval_sig(s,tt,side)
                    p=e['precision_pct'] or 0; r=e['recall_pct'] or 0
                    score=1.5*p+r-0.25*len(s)
                    z=(score,p,r,ct,ft,fr)
                    if best is None or z>best: best=z
        _,_,_,ct,ft,fr=best
        s=build_signals(te,side,ct,ft,fractal_col,fr); e=eval_sig(s,ty,side); e.update({'year':y,'ct':ct,'ft':ft,'fractal_th':fr}); rows.append(e)
    return rows,v02.aggregate(rows)


def first_passage(D,t,p,side,horizon=180):
    fut=D[(D.index>t)&(D.index<=t+pd.Timedelta(days=horizon))]
    if fut.empty:return None
    for _,r in fut.iterrows():
        if side=='LOW': a=r.high>=p*1.30; b=r.low<=p*0.85
        else: a=r.low<=p*0.75; b=r.high>=p*1.15
        if a and b:return None
        if a:return 1
        if b:return 0
    return None


def phase12_fractal(D,x):
    # Price-state analog prior. Query t may only use analogs whose 180D outcome was fully knowable before t.
    f=x.copy()
    cols=['prior_down_context','prior_up_context','near_low','near_high','wave_l','wave_s','momentum_l','momentum_s','absorb_l','absorb_s','candle_l','candle_s']
    Z=f[cols].astype(float)/100.0
    for side in ['LOW','HIGH']:
        vals=[]
        usecols=['prior_down_context','near_low','wave_l','momentum_l','absorb_l','candle_l'] if side=='LOW' else ['prior_up_context','near_high','wave_s','momentum_s','absorb_s','candle_s']
        zi=[cols.index(c) for c in usecols]
        arr=Z.values
        for i,(t,r) in enumerate(f.iterrows()):
            hist_idx=np.where(f.index<=t-pd.Timedelta(days=181))[0]
            if len(hist_idx)<250: vals.append(np.nan); continue
            q=arr[i,zi]; h=arr[hist_idx][:,zi]
            dist=np.sqrt(np.nanmean((h-q)**2,axis=1)); order=hist_idx[np.argsort(dist)[:24]]
            outcomes=[]; weights=[]
            for j in order:
                tj=f.index[j]; o=first_passage(D,tj,float(f.iloc[j].close),side)
                if o is None: continue
                d=float(np.sqrt(np.nanmean((arr[j,zi]-q)**2))); outcomes.append(o); weights.append(1/(0.05+d))
            if len(outcomes)<10: vals.append(np.nan)
            else: vals.append(100*float(np.average(outcomes,weights=weights)))
        f['fractal_long_prior' if side=='LOW' else 'fractal_short_prior']=vals
    return f


def zone_candidates(D,t,side):
    h=D.loc[:t].tail(220)
    if len(h)<200:return []
    r=h.iloc[-1]; p=float(r.close); atr=float(r.atr14) if pd.notna(r.atr14) else p*.03
    items=[]
    if side=='LOW':
        raw=[('EMA20',r.ema20),('SMA50',r.sma50),('SMA200',r.sma200),('LOW20',h.low.tail(20).min()),('LOW60',h.low.tail(60).min()),('LOW120',h.low.tail(120).min())]
        raw=[(n,float(v)) for n,v in raw if pd.notna(v) and v<p]
    else:
        raw=[('EMA20',r.ema20),('SMA50',r.sma50),('SMA200',r.sma200),('HIGH20',h.high.tail(20).max()),('HIGH60',h.high.tail(60).max()),('HIGH120',h.high.tail(120).max())]
        raw=[(n,float(v)) for n,v in raw if pd.notna(v) and v>p]
    clusters=[]
    for n,v in sorted(raw,key=lambda z:z[1]):
        hit=None
        for c in clusters:
            if abs(v-c['center'])/c['center']<=.022: hit=c; break
        if hit: hit['levels'].append(v); hit['sources'].append(n); hit['center']=float(np.mean(hit['levels']))
        else: clusters.append({'center':v,'levels':[v],'sources':[n]})
    out=[]
    for c in clusters:
        half=min(max(.006*c['center'],.22*atr),.015*c['center'])
        c['low']=c['center']-half; c['high']=c['center']+half; c['confluence']=len(c['sources']); out.append(c)
    return sorted(out,key=lambda c:abs(c['center']-p))[:3]


def pretouch_quality(D,t,side,z,fractal_prior):
    r=D.loc[:t].iloc[-1]; p=float(r.close); dist=abs(p-z['center'])/p
    con=clip100(z['confluence']/3*100); dscore=clip100((.18-dist)/.18*100)
    trend=clip100((float(r.drawdown180) if side=='LOW' else float(r.rally180))/(.35 if side=='LOW' else .70)*100)
    fr=50 if pd.isna(fractal_prior) else float(fractal_prior)
    return round(.30*con+.25*dscore+.20*trend+.25*fr,2)


def eval_zones(D,f,side):
    rows=[]
    frcol='fractal_long_prior' if side=='LOW' else 'fractal_short_prior'
    # monthly sampling avoids overlapping pseudo-independent zone observations
    sample=f[(f.index.year>=2020)&(f.index.day<=3)].iloc[::1]
    for t,r in sample.iterrows():
        for z in zone_candidates(D,t,side):
            q=pretouch_quality(D,t,side,z,r.get(frcol,np.nan)); fut=D[(D.index>t)&(D.index<=t+pd.Timedelta(days=90))]
            touch=None; success=None
            for tt,a in fut.iterrows():
                if a.low<=z['high'] and a.high>=z['low']:
                    touch=tt; after=D[(D.index>=tt)&(D.index<=tt+pd.Timedelta(days=45))]
                    if after.empty: break
                    if side=='LOW': success=float(after.high.max()>=z['center']*1.15 and after.low.min()>z['center']*.85)
                    else: success=float(after.low.min()<=z['center']*.85 and after.high.max()<z['center']*1.15)
                    break
            rows.append({'time':t,'side':side,'zone':z['center'],'quality':q,'touch':touch,'success':success,'confluence':z['confluence'],'sources':'+'.join(z['sources'])})
    R=pd.DataFrame(rows)
    out=[]
    for lo,hi in [(0,49),(50,64),(65,79),(80,100)]:
        g=R[(R.quality>=lo)&(R.quality<=hi)&R.success.notna()]
        out.append({'bucket':f'{lo}-{hi}','evaluable':len(g),'success_pct':round(100*g.success.mean(),2) if len(g) else None})
    return R,out


def main():
    D,H4,S,T,fail=prepare()
    p11=phase11_scores(D,S)
    l11,al11=oos_fixed(p11,T,'LOW'); s11,as11=oos_fixed(p11,T,'HIGH')
    p12=phase12_fractal(D,p11)
    l12,al12=oos_fixed(p12,T,'LOW','fractal_long_prior'); s12,as12=oos_fixed(p12,T,'HIGH','fractal_short_prior')
    zl,zlb=eval_zones(D,p12,'LOW'); zs,zsb=eval_zones(D,p12,'HIGH')
    zl.to_csv(OUT/'pretouch_long_zones.csv',index=False); zs.to_csv(OUT/'pretouch_short_zones.csv',index=False)
    p12.tail(800).to_csv(OUT/'phase12_recent_scores.csv')
    summary={
      'engine':'MASTER_BTC_TREND_V3_PHASE11_12_13_V0_1','status':'RESEARCH_ONLY_NOT_LIVE','data_failures':fail,
      'phase_1_1':{'design':'separate base/retest/higher-low from falling-knife; mirror for short','LONG_OOS':al11,'SHORT_OOS':as11,'long_years':l11,'short_years':s11,
        'pass':bool((al11['precision_pct'] or 0)>=30 and (al11['recall_pct'] or 0)>=25 and (as11['precision_pct'] or 0)>=30 and (as11['recall_pct'] or 0)>=25)},
      'phase_1_2':{'design':'point-in-time price-state analog prior; analog outcome must be fully knowable 180D before query','LONG_OOS':al12,'SHORT_OOS':as12,'long_years':l12,'short_years':s12,
        'pass':bool((al12['precision_pct'] or 0)>=30 and (al12['recall_pct'] or 0)>=25 and (as12['precision_pct'] or 0)>=30 and (as12['recall_pct'] or 0)>=25),
        'note':'Research filter only; not the failed V0.3.1 onchain/macro confirmation layer and not MASTER-integrated.'},
      'phase_1_3':{'design':'pre-touch zones from only information available at registration time; quality=confluence+distance+prior trend+fractal prior','LONG_BUCKETS':zlb,'SHORT_BUCKETS':zsb,
        'pass':bool(any((b['evaluable'] or 0)>=20 and (b['success_pct'] or 0)>=55 for b in zlb) and any((b['evaluable'] or 0)>=20 and (b['success_pct'] or 0)>=55 for b in zsb))},
      'integration':'FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL','master_btc_trend_modified':False,
    }
    summary['overall_stage']='PHASE1_1_TO_1_3_VALIDATED' if summary['phase_1_1']['pass'] and summary['phase_1_2']['pass'] and summary['phase_1_3']['pass'] else 'RESEARCH_PARTIAL_OR_FAIL'
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))

if __name__=='__main__': main()
