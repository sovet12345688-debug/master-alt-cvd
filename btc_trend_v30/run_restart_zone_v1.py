from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

import run_restart_phase1_v1 as c

OUT=Path('btc_trend_v30/output/restart_zone_v1');OUT.mkdir(parents=True,exist_ok=True)
FEATURES=['pivot_density','role_reversal','freshness','repeated_weakening','displacement','htf_overlap','prior_reaction','confluence','distance_score']


def confirmed_pivots(h,side,lr=5):
    a=[]
    for i in range(lr,len(h)-lr):
        if side=='LONG':
            v=float(h.low.iloc[i]); ok=v<=float(h.low.iloc[i-lr:i+lr+1].min())*(1+1e-10)
        else:
            v=float(h.high.iloc[i]); ok=v>=float(h.high.iloc[i-lr:i+lr+1].max())*(1-1e-10)
        if ok:a.append((h.index[i],v,'PIVOT'))
    return a


def displacement_levels(h,side):
    a=[];r3=h.close.pct_change(3)
    for i in range(20,len(h)-3):
        if pd.isna(h.atr14.iloc[i]):continue
        p=float(h.close.iloc[i]);v=float(h.atr14.iloc[i])/p
        if side=='LONG' and r3.iloc[i+3]>max(.08,2.2*v):a.append((h.index[i],float(h.low.iloc[i]),'DISPLACEMENT'))
        if side=='SHORT' and r3.iloc[i+3]<-max(.08,2.2*v):a.append((h.index[i],float(h.high.iloc[i]),'DISPLACEMENT'))
    return a[-20:]


def cluster(levels,p,atrv,side):
    gap=max(.018*p,.75*atrv);out=[]
    for tm,v,src in sorted(levels,key=lambda q:q[1]):
        if (side=='LONG' and v>=p) or (side=='SHORT' and v<=p):continue
        hit=None
        for z in out:
            if abs(v-z['center'])<=gap:hit=z;break
        if hit:
            hit['items'].append((tm,v,src));hit['center']=float(np.mean([q[1] for q in hit['items']]))
        else:out.append({'center':v,'items':[(tm,v,src)]})
    return sorted(out,key=lambda z:abs(z['center']-p))[:4]


def zone_features(d,t,side,z):
    h=d.loc[:t];r=h.iloc[-1];p=float(r.close);av=float(r.atr14);mid=float(z['center']);items=z['items'];src=[q[2] for q in items]
    width=max(.012*mid,.65*av);lo=mid-width;hi=mid+width;source=min(q[0] for q in items);age=max(0,(t-max(q[0] for q in items)).days)
    hh=h[h.index>=source];touch=(hh.low<=hi)&(hh.high>=lo);tests=0;last=None;react=[];cross=0;prev=None
    for tt,row in hh.iterrows():
        ss=1 if row.close>mid else -1
        if prev is not None and ss!=prev:cross+=1
        prev=ss
        if touch.loc[tt] and (last is None or (tt-last).days>5):
            tests+=1;aft=h[(h.index>tt)&(h.index<=tt+pd.Timedelta(days=20))]
            if len(aft):
                if side=='LONG':fav=aft.high.max()/mid-1;adv=max(0,1-aft.low.min()/mid)
                else:fav=1-aft.low.min()/mid;adv=max(0,aft.high.max()/mid-1)
                react.append(float(fav-.6*adv))
            last=tt
    pe=float(np.mean(react)) if react else 0
    ma=max(float(pd.notna(r.sma50) and abs(mid-r.sma50)/p<.025),float(pd.notna(r.sma200) and abs(mid-r.sma200)/p<.03))
    return {'time':t,'side':side,'zone':mid,'low':lo,'high':hi,'pivot_density':min(1,sum(s=='PIVOT' for s in src)/4),'role_reversal':min(1,cross/4),'freshness':min(1,age/180),'repeated_weakening':min(1,max(0,tests-1)/4),'displacement':min(1,sum(s=='DISPLACEMENT' for s in src)/2),'htf_overlap':ma,'prior_reaction':float(np.clip((pe+.10)/.30,0,1)),'confluence':float(np.clip(len(set(src))/4+min(len(items),6)/12,0,1)),'distance_score':float(1-np.clip(abs(p-mid)/(8*av+c.EPS),0,1)),'tests':tests,'age':age}


def registry(d,side):
    rows=[];dates=d[d.index>=pd.Timestamp('2019-01-01T00:00:00Z')].index[::21]
    for t in dates:
        h=d.loc[:t].tail(500)
        if len(h)<220 or pd.isna(h.atr14.iloc[-1]):continue
        r=h.iloc[-1];p=float(r.close);av=float(r.atr14);levels=confirmed_pivots(h,side)+displacement_levels(h,side)
        if side=='LONG':raw=[('EMA20',r.ema20),('SMA50',r.sma50),('SMA200',r.sma200),('LOW20',h.low.tail(20).min()),('LOW60',h.low.tail(60).min()),('LOW120',h.low.tail(120).min())]
        else:raw=[('EMA20',r.ema20),('SMA50',r.sma50),('SMA200',r.sma200),('HIGH20',h.high.tail(20).max()),('HIGH60',h.high.tail(60).max()),('HIGH120',h.high.tail(120).max())]
        levels += [(t-pd.Timedelta(days=1),float(v),n) for n,v in raw if pd.notna(v)]
        for z in cluster(levels,p,av,side):
            q=zone_features(d,t,side,z)
            if abs(p-q['zone'])/p<=.35:rows.append(q)
    return pd.DataFrame(rows)


def outcome(d,r):
    t=pd.Timestamp(r.time);mid=float(r.zone);lo=float(r.low);hi=float(r.high);av=float(d.loc[:t].iloc[-1].atr14);f=d[(d.index>t)&(d.index<=t+pd.Timedelta(days=90))];touch=None
    for tt,a in f.iterrows():
        if a.low<=hi and a.high>=lo:touch=tt;break
    if touch is None:return np.nan,np.nan,np.nan
    if r.side=='LONG':stop=lo-.5*av;risk=max(.025*mid,mid-stop);target=mid+3*risk
    else:stop=hi+.5*av;risk=max(.025*mid,stop-mid);target=mid-3*risk
    if risk/mid>.15:return np.nan,np.nan,np.nan
    a=d[(d.index>=touch)&(d.index<=touch+pd.Timedelta(days=60))];mr=0.;res=np.nan
    for _,x in a.iterrows():
        if r.side=='LONG':rr=(x.high-mid)/risk;bad=x.low<=stop;good=x.high>=target
        else:rr=(mid-x.low)/risk;bad=x.high>=stop;good=x.low<=target
        mr=max(mr,float(rr))
        if good and bad:break
        if good:res=1.;break
        if bad:res=0.;break
    if pd.isna(res) and len(a):res=float(mr>=2.)
    return res,mr,100*risk/mid


def add_outcomes(d,z):
    x=z.copy();v=[outcome(d,r) for _,r in x.iterrows()];x['success']=[q[0] for q in v];x['max_r']=[q[1] for q in v];x['risk_pct']=[q[2] for q in v];return x


def fit(train):
    g=train.dropna(subset=['success']);
    if len(g)<50 or g.success.nunique()<2:return None
    X=g[FEATURES].astype(float).values;X[:,FEATURES.index('repeated_weakening')]*=-1;y=g.success.values.astype(float);mu=X.mean(0);sd=X.std(0)+1e-6;Z=(X-mu)/sd;w=[]
    for j in range(Z.shape[1]):
        a=Z[y==1,j];b=Z[y==0,j];w.append(float(np.clip(a.mean()-b.mean(),-1.5,1.5)))
    return mu,sd,np.array(w)


def raw(model,f):
    if model is None:return np.full(len(f),np.nan)
    mu,sd,w=model;X=f[FEATURES].astype(float).values;X[:,FEATURES.index('repeated_weakening')]*=-1;Z=(X-mu)/sd;return np.nansum(Z*w,axis=1)/(np.sum(abs(w))+c.EPS)


def score(v,train):return c.pct(v,train)


def oos(z):
    allx=[];years=[]
    for y in range(2020,2027):
        tr=z[(z.time.dt.year<y)&z.success.notna()];te=z[(z.time.dt.year==y)&z.success.notna()].copy()
        if len(tr)<60 or len(te)<8:continue
        m=fit(tr);trr=raw(m,tr);ter=raw(m,te);te['score']=[score(v,trr) for v in ter];allx.append(te);years.append({'year':y,'n':len(te),'success_pct':round(100*te.success.mean(),2)})
    if not allx:return pd.DataFrame(),years,{}
    q=pd.concat(allx,ignore_index=True);b=[]
    for lo,hi in [(0,49.999),(50,69.999),(70,84.999),(85,100.001)]:
        g=q[(q.score>=lo)&(q.score<=hi)];b.append({'bucket':f'{int(lo)}-{int(min(hi,100))}','n':len(g),'success_pct':round(100*g.success.mean(),2) if len(g) else None,'avg_max_r':round(float(g.max_r.mean()),2) if len(g) else None})
    valid=[x for x in b if x['n']>=15 and x['success_pct'] is not None];mono=bool(len(valid)>=3 and all(valid[i]['success_pct']<=valid[i+1]['success_pct']+3 for i in range(len(valid)-1)))
    return q,years,{'buckets':b,'monotonic_3pp_tolerance':mono}


def main():
    d0,fail=c.load_interval('1d');d=c.enrich(d0,'1D');zl=add_outcomes(d,registry(d,'LONG'));zs=add_outcomes(d,registry(d,'SHORT'));zl['time']=pd.to_datetime(zl.time,utc=True);zs['time']=pd.to_datetime(zs.time,utc=True);ol,yl,sl=oos(zl);os,ys,ss=oos(zs)
    zl.to_csv(OUT/'long_registry.csv',index=False);zs.to_csv(OUT/'short_registry.csv',index=False);ol.to_csv(OUT/'long_oos.csv',index=False);os.to_csv(OUT/'short_oos.csv',index=False)
    summary={'engine':'MASTER_BTC_TREND_V3_RESTART_ZONE_V1','status':'RESEARCH_ONLY_NOT_LIVE','data_failures':fail,'LONG':sl,'SHORT':ss,'years_LONG':yl,'years_SHORT':ys,'definition':'pre-touch only; score learned from earlier years; success=3R before 1R stop or >=2R unresolved by 60D','zone_quality_gate':bool(sl.get('monotonic_3pp_tolerance') and ss.get('monotonic_3pp_tolerance')),'reaction_strength_separate':True,'master_btc_trend_v26_modified':False,'production_modified':False,'integration':'FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL'};(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False))

if __name__=='__main__':main()
