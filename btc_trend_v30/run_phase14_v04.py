from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

import run_phase11_12_13 as b
import run_phase11_12_13_fast as f13
import run_phase14 as p14
import run_phase14_v03 as v03

OUT=Path('btc_trend_v30/output/phase14_v04'); OUT.mkdir(parents=True,exist_ok=True)


def f1(a):
    p=float(a.get('precision_pct') or 0); r=float(a.get('recall_pct') or 0)
    return round(2*p*r/(p+r),2) if p>0 and r>0 else 0.0


def recov(r,side):
    return v03.recovery(r,side)


def build_episode_signals(x,side,ct,tt,cooldown,fw=0.0,lookback=42):
    basecol='long_candidate_v11' if side=='LOW' else 'short_candidate_v11'
    ctxcol='prior_down_context' if side=='LOW' else 'prior_up_context'
    loccol='near_low' if side=='LOW' else 'near_high'
    turncol='long_turn' if side=='LOW' else 'short_turn'
    frcol='fractal_long_prior' if side=='LOW' else 'fractal_short_prior'
    prev7=x[turncol].shift(1).rolling(7,min_periods=1).max()
    out=[]; last=None
    for t,r in x.iterrows():
        cur=float(r[turncol]); prior=float(prev7.loc[t]) if pd.notna(prev7.loc[t]) else -1
        # Trigger only a NEW turn impulse; repeated high readings are one episode, not new origins.
        if cur<tt or not (prior<tt or cur>=prior+6):
            continue
        h=x[(x.index>=t-pd.Timedelta(days=lookback))&(x.index<=t)].copy()
        if h.empty: continue
        h=h[h[basecol]>=ct].copy()
        if h.empty: continue
        rv=h.apply(lambda q: recov(q,side),axis=1)
        h=h[((h[ctxcol]>=28)|(rv>=80)) & ((h[loccol]>=15)|(rv>=82))].copy()
        if h.empty: continue
        rv=h.apply(lambda q: recov(q,side),axis=1)
        if side=='LOW': h=h[~((h.falling_knife_l>=100)&(rv<82))]
        else: h=h[~((h.blowoff_s>=100)&(rv<82))]
        if h.empty: continue
        if side=='LOW':
            extreme=float(h.low.min()); near=h[h.low<=extreme*1.035].copy()
        else:
            extreme=float(h.high.max()); near=h[h.high>=extreme*0.965].copy()
        fr=near[frcol].fillna(50).astype(float)
        # Fractal is a small rank/tie contribution only.
        rank=near[basecol].astype(float)+fw*(fr-50)
        ot=rank.idxmax(); br=near.loc[ot]
        op=float(br.low if side=='LOW' else br.high)
        frv=float(fr.loc[ot]); quality=.45*float(br[basecol])+.45*cur+.10*recov(br,side)+fw*(frv-50)
        sig={'origin_time':ot,'confirm_time':t,'origin_price':op,'confirm_price':float(r.close),
             'candidate_base':float(br[basecol]),'turn_score':cur,'fractal_prior':frv,
             'fractal_weight':fw,'quality':float(quality)}
        if last is None or (t-last).days>cooldown:
            out.append(sig); last=t
        elif quality>out[-1]['quality']:
            out[-1]=sig; last=t
    return out


def choose_base(train,T,side):
    best=None
    for ct in [35,45,55]:
      for tt in [55,65,75]:
       for cd in [49,77,105]:
        s=build_episode_signals(train,side,ct,tt,cd,0.0); e=b.eval_sig(s,T,side)
        p=float(e['precision_pct'] or 0); r=float(e['recall_pct'] or 0)
        # Precision matters because truth events are sparse, but retain usable recall.
        obj=1.30*p+0.85*r-0.12*len(s)
        if r<20: obj-=18
        if p<25: obj-=12
        z=(obj,p,r,-len(s),ct,tt,cd)
        if best is None or z>best: best=z
    return {'ct':best[4],'tt':best[5],'cooldown':best[6]}


def choose_fractal_weight(train,T,side,pars):
    base=b.eval_sig(build_episode_signals(train,side,pars['ct'],pars['tt'],pars['cooldown'],0),T,side)
    basef=f1(base); best=(basef,float(base.get('precision_pct') or 0),0.0)
    for fw in [0.05,0.10]:
        e=b.eval_sig(build_episode_signals(train,side,pars['ct'],pars['tt'],pars['cooldown'],fw),T,side)
        ef=f1(e); ep=float(e.get('precision_pct') or 0)
        # Non-harm guard: positive weight only if train F1 improves materially without losing precision.
        if ef>=basef+1.0 and ep+1>=best[1] and ef>best[0]: best=(ef,ep,fw)
    return best[2]


def oos_origin(x,T,side,use_fractal):
    rows=[]; ws=[]
    for y in range(2020,2027):
        tr=x[x.index.year<y]; te=x[x.index.year==y]; tt=T[T.time.dt.year<y]; ty=T[T.time.dt.year==y]
        if len(tr)<500 or len(te)<100: continue
        pars=choose_base(tr,tt,side); fw=choose_fractal_weight(tr,tt,side,pars) if use_fractal else 0.0
        s=build_episode_signals(te,side,pars['ct'],pars['tt'],pars['cooldown'],fw)
        e=b.eval_sig(s,ty,side); e.update({'year':y,**pars,'fractal_weight':fw}); rows.append(e); ws.append(fw)
    a=b.v02.aggregate(rows); a['years']=len(rows); a['years_using_fractal']=int(sum(w>0 for w in ws)); a['mean_selected_fractal_weight']=round(float(np.mean(ws)),3) if ws else None
    return rows,a


def clip100(v): return float(max(0,min(100,v)))


def augment_zone_features(D,R,side):
    R=R.copy(); vals=[]
    for _,r in R.iterrows():
        t=pd.Timestamp(r.time); z=float(r.zone); hist=D[(D.index<t)&(D.index>=t-pd.Timedelta(days=220))].copy()
        if hist.empty:
            vals.append((50,50,50,50)); continue
        tol=.018
        touch=((hist.low<=z*(1+tol))&(hist.high>=z*(1-tol)))
        episodes=int((touch & ~touch.shift(1,fill_value=False)).sum())
        freshness=100 if episodes<=1 else (75 if episodes==2 else (45 if episodes==3 else 20))
        fav=[]
        starts=list(hist.index[touch & ~touch.shift(1,fill_value=False)])[-4:]
        for st in starts:
            aft=D[(D.index>=st)&(D.index<=min(t,st+pd.Timedelta(days=21)))]
            if len(aft)<3: continue
            if side=='LOW': move=float(aft.high.max()/z-1)
            else: move=float(1-aft.low.min()/z)
            fav.append(move)
        prior=clip100((np.median(fav) if fav else .05)/.15*100)
        cur=hist.iloc[-1]
        if side=='LOW':
            align=40*float(cur.close>cur.sma200)+30*float(cur.close>cur.sma50)+30*float(cur.ema20>cur.sma50)
            role=100 if ((hist.close<z).rolling(30,min_periods=5).mean().iloc[-1]>.25 and cur.close>z) else 45
        else:
            align=40*float(cur.close<cur.sma200)+30*float(cur.close<cur.sma50)+30*float(cur.ema20<cur.sma50)
            role=100 if ((hist.close>z).rolling(30,min_periods=5).mean().iloc[-1]>.25 and cur.close<z) else 45
        vals.append((freshness,prior,clip100(align),role))
    R[['freshness_score','prior_reaction_score','alignment_score','roleflip_score']]=pd.DataFrame(vals,index=R.index)
    return R


FEATURES=['confluence_score','trend_score','origin_score','source_score','freshness_score','prior_reaction_score','alignment_score','roleflip_score']

def fit_logit(train,l2=2.0):
    X=train[FEATURES].astype(float).values/100.; y=train.success.astype(float).values
    mu=X.mean(0); sd=X.std(0); sd[sd<1e-6]=1; Z=(X-mu)/sd; Z=np.c_[np.ones(len(Z)),Z]
    w=np.zeros(Z.shape[1])
    for _ in range(1100):
        a=np.clip(Z@w,-20,20); p=1/(1+np.exp(-a)); g=Z.T@(p-y)/len(y); g[1:]+=l2*w[1:]/len(y); w-=.05*g
    return mu,sd,w

def predict(frame,m):
    mu,sd,w=m; X=frame[FEATURES].astype(float).values/100.; Z=(X-mu)/sd; Z=np.c_[np.ones(len(Z)),Z]; return 1/(1+np.exp(-np.clip(Z@w,-20,20)))

def zone_oos(D,R,side):
    R=augment_zone_features(D,R,side); outs=[]; years=[]
    for y in range(2020,2027):
        cut=pd.Timestamp(f'{y}-01-01',tz='UTC'); tr=R[(R.resolved_time<cut)&R.success.notna()].copy(); te=R[(R.time.dt.year==y)&R.success.notna()].copy()
        if len(tr)<55 or len(te)<5: continue
        m=fit_logit(tr); pt=predict(tr,m); pe=predict(te,m); ranks=np.searchsorted(np.sort(pt),pe,side='right')/len(pt); te['reaction_quality']=100*ranks
        outs.append(te); years.append({'year':y,'train_n':len(tr),'test_n':len(te)})
    O=pd.concat(outs,ignore_index=True) if outs else pd.DataFrame(); buckets=[]
    if len(O):
        for lo,hi in [(0,49),(50,64),(65,79),(80,100)]:
            g=O[(O.reaction_quality>=lo)&(O.reaction_quality<=hi)]; buckets.append({'bucket':f'{lo}-{hi}','evaluable':len(g),'success_pct':round(float(g.success.mean()*100),2) if len(g) else None})
        low=O[O.reaction_quality<65]; high=O[O.reaction_quality>=65]
        comp={'low_n':len(low),'low_success_pct':round(float(low.success.mean()*100),2),'high_n':len(high),'high_success_pct':round(float(high.success.mean()*100),2),'high_minus_low_pp':round(float((high.success.mean()-low.success.mean())*100),2)}
    else: comp={'low_n':0,'low_success_pct':None,'high_n':0,'high_success_pct':None,'high_minus_low_pp':None}
    return O,buckets,comp,years


def origin_gate(l,s): return (l.get('precision_pct') or 0)>=30 and (l.get('recall_pct') or 0)>=25 and (s.get('precision_pct') or 0)>=30 and (s.get('recall_pct') or 0)>=25
def zone_gate(c): return c['high_n']>=20 and (c['high_success_pct'] or 0)>=50 and (c['high_minus_low_pp'] or -999)>=8


def main():
    D,H4,S,T,fail=b.prepare(); p11=b.phase11_scores(D,S); p12=f13.phase12_fast(D,p11); x=p14.add_turn_scores(p12); x=x.join(D[['drawdown180','rally180']],how='left')
    l0r,l0=oos_origin(x,T,'LOW',False); s0r,s0=oos_origin(x,T,'HIGH',False); lfr,lf=oos_origin(x,T,'LOW',True); sfr,sf=oos_origin(x,T,'HIGH',True)
    zl=p14.build_zone_registry(D,x,'LOW'); zs=p14.build_zone_registry(D,x,'HIGH'); zlo,zlb,zlc,zly=zone_oos(D,zl,'LOW'); zso,zsb,zsc,zsy=zone_oos(D,zs,'HIGH')
    zlo.to_csv(OUT/'zone_oos_long.csv',index=False); zso.to_csv(OUT/'zone_oos_short.csv',index=False); x.tail(900).to_csv(OUT/'recent_origin_scores.csv')
    fu={'LONG_baseline_F1':f1(l0),'LONG_soft_F1':f1(lf),'LONG_delta_F1':round(f1(lf)-f1(l0),2),'SHORT_baseline_F1':f1(s0),'SHORT_soft_F1':f1(sf),'SHORT_delta_F1':round(f1(sf)-f1(s0),2),'rule':'fractal weight is zero unless prior-year training shows >=1 F1 improvement without precision harm'}
    nonharm=fu['LONG_delta_F1']>=-1 and fu['SHORT_delta_F1']>=-1; og=origin_gate(lf,sf); zlg=zone_gate(zlc); zsg=zone_gate(zsc); zg=zlg and zsg
    d={'engine':'MASTER_BTC_TREND_V3_PHASE1_4_V0_4','status':'RESEARCH_ONLY_NOT_LIVE','data_failures':fail,'design_changes':['new-turn episode trigger','49/77/105D de-dup cooldown optimized on prior years','fractal non-harm weight guard','zone prior-touch freshness/history/alignment/role-flip features'],
       'phase_1_4':{'origin_gate_pass':og,'LONG_BASELINE':l0,'LONG_SOFT_FRACTAL':lf,'SHORT_BASELINE':s0,'SHORT_SOFT_FRACTAL':sf,'LONG_years':lfr,'SHORT_years':sfr,'fractal_utility':fu,'fractal_nonharm_pass':nonharm,'zone_long':{'pass':zlg,'buckets':zlb,'comparison':zlc,'years':zly},'zone_short':{'pass':zsg,'buckets':zsb,'comparison':zsc,'years':zsy},'zone_gate_pass':zg},
       'overall_stage':'PHASE1_4_VALIDATED' if og and nonharm and zg else 'RESEARCH_PARTIAL_OR_FAIL','master_btc_trend_modified':False,'integration':'FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL','anti_leakage':{'origin':'all thresholds/cooldown/fractal weights selected on prior years only','fractal':'positive weight only after prior-year non-harm test','zone':'all added features computed strictly before registration; model trained only on outcomes resolved before test year'}}
    (OUT/'summary.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(d,ensure_ascii=False))

if __name__=='__main__': main()
