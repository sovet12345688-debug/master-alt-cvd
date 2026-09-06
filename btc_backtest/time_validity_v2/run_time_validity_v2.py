from __future__ import annotations
import io,json,math,zipfile
from pathlib import Path
import numpy as np
import pandas as pd
import requests

START=pd.Timestamp('2024-01-01T00:00:00Z'); SPLIT=pd.Timestamp('2025-01-01T00:00:00Z'); END=pd.Timestamp('2026-01-01T00:00:00Z')
OUT=Path('btc_backtest/output/time_validity_v2'); OUT.mkdir(parents=True,exist_ok=True)
COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_buy_base','taker_buy_quote','ignore']
URL='https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/{i}/BTCUSDT-{i}-{ym}.zip'
DELTA={'1H':pd.Timedelta(hours=1),'4H':pd.Timedelta(hours=4),'1D':pd.Timedelta(days=1)}
WAIT={'1H':48,'4H':24,'1D':10}; COOL={'1H':12,'4H':6,'1D':3}; SEARCH={'1H':16,'4H':48,'1D':96}; HORIZ={'1H':72,'4H':30,'1D':12}

def months():
    x=START
    while x<END:
        yield x.strftime('%Y-%m'); x+=pd.offsets.MonthBegin(1)

def load(i):
    a=[]; bad=[]; s=requests.Session(); s.headers['User-Agent']='money-time-validity-v2'
    for ym in months():
        try:
            r=s.get(URL.format(i=i,ym=ym),timeout=60)
            if r.status_code!=200: bad.append(f'{i}:{ym}:HTTP{r.status_code}'); continue
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                n=[x for x in z.namelist() if x.endswith('.csv')][0]; q=pd.read_csv(z.open(n),header=None,names=COLS)
            ts=pd.to_numeric(q.open_time,errors='coerce'); ts=np.where(ts>1e14,ts/1000,ts); q['time']=pd.to_datetime(ts,unit='ms',utc=True,errors='coerce')
            for c in ['open','high','low','close','volume','quote_volume','taker_buy_quote']: q[c]=pd.to_numeric(q[c],errors='coerce')
            a.append(q[['time','open','high','low','close','volume','quote_volume','taker_buy_quote']])
        except Exception as e: bad.append(f'{i}:{ym}:{type(e).__name__}')
    if not a: raise RuntimeError('no data '+i)
    d=pd.concat(a).dropna().drop_duplicates('time').sort_values('time'); d=d[(d.time>=START)&(d.time<END)].set_index('time')
    return d,bad

def atr(d,n=14):
    pc=d.close.shift(); tr=pd.concat([(d.high-d.low).abs(),(d.high-pc).abs(),(d.low-pc).abs()],axis=1).max(axis=1); return tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean()

def enrich(d):
    x=d.copy(); x['ema20']=x.close.ewm(span=20,adjust=False).mean(); x['ma50']=x.close.rolling(50).mean(); x['atr']=atr(x); x['slope']=(x.ema20-x.ema20.shift(3))/x.atr.replace(0,np.nan); x['vr']=x.volume/x.volume.rolling(20).mean(); x['lo8']=x.low.rolling(8).min(); x['hi8']=x.high.rolling(8).max(); rg=(x.high-x.low).replace(0,np.nan); x['lw']=(np.minimum(x.open,x.close)-x.low)/rg; x['uw']=(x.high-np.maximum(x.open,x.close))/rg; x['ext']=(x.close-x.ema20).abs()/x.atr.replace(0,np.nan); return x

def rs(h,r):
    if r=='1h': return enrich(h)
    x=pd.DataFrame({'open':h.open.resample(r,label='left',closed='left').first(),'high':h.high.resample(r,label='left',closed='left').max(),'low':h.low.resample(r,label='left',closed='left').min(),'close':h.close.resample(r,label='left',closed='left').last(),'volume':h.volume.resample(r,label='left',closed='left').sum(),'quote_volume':h.quote_volume.resample(r,label='left',closed='left').sum(),'taker_buy_quote':h.taker_buy_quote.resample(r,label='left',closed='left').sum()}).dropna(); return enrich(x)

def before(d,t):
    x=d[d.index<t]; return None if x.empty else x.iloc[-1]

def we(dir,r,u):
    # Research operationalization only. Source has no numeric formula; Fibonacci Time excluded.
    s=0.
    if u is not None and pd.notna(u.ema20) and pd.notna(u.ma50):
        a=(u.close>u.ema20>u.ma50) if dir=='LONG' else (u.close<u.ema20<u.ma50); p=(u.close>u.ema20) if dir=='LONG' else (u.close<u.ema20); s+=30 if a else (15 if p else 0)
    s+=25*min(max(float(r.vr if pd.notna(r.vr) else 0)/1.5,0),1); sl=float(r.slope if pd.notna(r.slope) else 0); sl=sl if dir=='LONG' else -sl; s+=20*min(max(sl/.8,0),1); ex=float(r.ext if pd.notna(r.ext) else 99); s+=15*max(0,1-min(ex/1.5,1)); wk=float(r.lw if dir=='LONG' else r.uw); s+=10*min(max(wk/.5,0),1); return min(100,s)

def setups(f,b,u):
    a=[]; last=-999
    for i,(t,r) in enumerate(b.iterrows()):
        if i<60 or i-last<COOL[f] or any(pd.isna(x) for x in [r.ema20,r.ma50,r.atr,r.lo8,r.hi8]) or r.atr<=0: continue
        L=r.ema20>r.ma50 and r.slope>0 and r.close>r.ema20 and 0<=(r.close-r.ema20)/r.atr<=1.5; S=r.ema20<r.ma50 and r.slope<0 and r.close<r.ema20 and 0<=(r.ema20-r.close)/r.atr<=1.5
        if not(L or S): continue
        d='LONG' if L else 'SHORT'; e=float(r.ema20); av=float(r.atr)
        if L: st=min(float(r.lo8),e-1.05*av)-.05*av; risk=e-st; tp=e+3*risk
        else: st=max(float(r.hi8),e+1.05*av)+.05*av; risk=st-e; tp=e-3*risk
        if risk<=0: continue
        sig=t+DELTA[f]; a.append(dict(frame=f,dir=d,sig=sig,entry=e,stop=st,tp=tp,risk=risk,we=we(d,r,before(u,sig)))); last=i
    return a

def valid(s,b,tt):
    x=b[(b.index+DELTA[s['frame']]>s['sig'])&(b.index+DELTA[s['frame']]<=tt)]
    for _,r in x.iterrows():
        if s['dir']=='LONG' and (r.close<=s['stop'] or (r.ema20<r.ma50*.995 and r.close<r.ma50*.99)): return False
        if s['dir']=='SHORT' and (r.close>=s['stop'] or (r.ema20>r.ma50*1.005 and r.close>r.ma50*1.01)): return False
    return True

def touch(s,m):
    z=m[(m.index>=s['sig'])&(m.index<s['sig']+WAIT[s['frame']]*DELTA[s['frame']])]; h=np.where((z.low.values<=s['entry'])&(z.high.values>=s['entry']))[0]
    return None if not len(h) else m.index.get_indexer([z.index[h[0]]])[0]

def trigger(s,m,p):
    end=min(len(m)-1,p+SEARCH[s['frame']]+1)
    for k in range(p,end):
        r=m.iloc[k]; n=m.iloc[k+1]
        if (r.low<=s['stop'] if s['dir']=='LONG' else r.high>=s['stop']): return None,None,None
        if s['dir']=='LONG': ok=r.close>s['entry'] and r.close>r.open and r.low<=s['entry']+.2*s['risk'] and n.low>=r.low
        else: ok=r.close<s['entry'] and r.close<r.open and r.high>=s['entry']-.2*s['risk'] and n.high<=r.high
        if ok:return k,k-p,float(r.close)
    return None,None,None

def eval_trade(s,m,p,tr):
    z=m.iloc[p:]; z=z[z.index<=m.index[p]+HORIZ[s['frame']]*DELTA[s['frame']]]
    if s['dir']=='LONG': a=np.where(z.low.values<=s['stop'])[0]; q=np.where(z.high.values>=s['tp'])[0]; mfe=(z.high.max()-s['entry'])/s['risk']; mae=(s['entry']-z.low.min())/s['risk']; er=(z.close.iloc[-1]-s['entry'])/s['risk']
    else: a=np.where(z.high.values>=s['stop'])[0]; q=np.where(z.low.values<=s['tp'])[0]; mfe=(s['entry']-z.low.min())/s['risk']; mae=(z.high.max()-s['entry'])/s['risk']; er=(s['entry']-z.close.iloc[-1])/s['risk']
    sp=int(a[0]) if len(a) else None; qp=int(q[0]) if len(q) else None
    if qp is not None and (sp is None or qp<sp): R=3.; lab='TP3'
    elif sp is not None and (qp is None or sp<qp): R=-1.; lab='SL'
    else:R=float(np.clip(er,-1,3)); lab='OPEN'
    prog={'p025':None,'p05':None}
    if tr is not None:
        zz=m.iloc[tr:]; zz=zz[zz.index<=m.index[tr]+HORIZ[s['frame']]*DELTA[s['frame']]]
        for nm,x in [('p025',.25),('p05',.5)]:
            h=np.where(zz.high.values>=s['entry']+x*s['risk'])[0] if s['dir']=='LONG' else np.where(zz.low.values<=s['entry']-x*s['risk'])[0]; prog[nm]=int(h[0]) if len(h) else None
    return R,lab,float(mfe),float(mae),prog

def rrtr(s,px):
    if px is None:return np.nan
    ri=px-s['stop'] if s['dir']=='LONG' else s['stop']-px; rw=s['tp']-px if s['dir']=='LONG' else px-s['tp']; return rw/ri if ri>0 else np.nan

def replay(f,ss,b,m):
    rows=[]
    for s in ss:
        p=touch(s,m)
        if p is None:continue
        tt=m.index[p]; tr,dl,px=trigger(s,m,p); R,lab,mfe,mae,pg=eval_trade(s,m,p,tr); rows.append(dict(frame=f,dir=s['dir'],touch=tt,is_oos=tt>=SPLIT,rolling=valid(s,b,tt),triggered=tr is not None,delay=dl if dl is not None else 999,rr_trigger=rrtr(s,px),we=s['we'],R=R,label=lab,MFE=mfe,MAE=mae,p025=pg['p025'],p05=pg['p05']))
    return pd.DataFrame(rows)

def met(x,rcol='R'):
    if x.empty:return dict(n=0)
    r=x[rcol].astype(float); neg=-r[r<0].sum(); pf=r[r>0].sum()/neg if neg>0 else np.inf; eq=r.cumsum(); dd=(eq-eq.cummax()).min(); return dict(n=len(x),avg_R=float(r.mean()),tp3_rate=float((x.label=='TP3').mean()),sl_rate=float((x.label=='SL').mean()),PF=float(pf),avg_MFE=float(x.MFE.mean()),avg_MAE=float(x.MAE.mean()),maxDD_R=float(dd))

def learn_delay(x,f):
    x=x[x.rolling&x.triggered]; best=None
    for c in [q for q in [0,1,2,3,4,6,8,12,16,24,32,48,64,96] if q<=SEARCH[f]]:
        y=x[x.delay<=c]
        if len(y)<max(20,int(len(x)*.6)):continue
        z=(y.R.mean(),-c,c)
        if best is None or z>best:best=z
    return 4 if best is None else best[2]

def clock(x,lim):
    x=x[x.rolling&x.triggered&(x.delay<=lim)&(x.R>0)]; out={}
    for c in ['p025','p05']:
        v=pd.to_numeric(x[c],errors='coerce').dropna(); out[c]={k:(None if v.empty else int(math.ceil(v.quantile(q)))) for k,q in [('p50',.5),('p75',.75),('p90',.9)]}
    return out

def learn_we(x):
    if x.empty:return 50.; best=None
    for q in [.3,.4,.5]:
        th=float(x.we.quantile(q)); y=x[x.we>=th]
        if len(y)<max(20,int(len(x)*.5)):continue
        z=(y.R.mean(),len(y),th)
        if best is None or z>best:best=z
    return float(x.we.quantile(.4)) if best is None else best[2]

def bucket(v):
    return '0' if v==0 else '1' if v==1 else '2' if v==2 else '3-4' if v<=4 else '5-8' if v<=8 else '9+' if v<999 else 'NO_TRIGGER'

def run():
    h,b1=load('1h'); m,b15=load('15m'); h=enrich(h); m=enrich(m); bases={'1H':h,'4H':rs(h,'4h'),'1D':rs(h,'1d')}; ups={'1H':bases['4H'],'4H':bases['1D'],'1D':rs(h,'1W')}; reps=[]
    for f in ['1H','4H','1D']: reps.append(replay(f,setups(f,bases[f],ups[f]),bases[f],m))
    a=pd.concat(reps,ignore_index=True); a.to_csv(OUT/'all_proxy_replays.csv',index=False); B=[]; D=[]; W=[]; limits={}; clocks={}; wth={}
    for f in ['1H','4H','1D']:
        z=a[a.frame==f]; tr=z[~z.is_oos]; oo=z[z.is_oos]; lim=learn_delay(tr,f); limits[f]=lim; cl=clock(tr,lim); clocks[f]=cl; p75=cl['p025']['p75'] or 8; b0=oo; b_1=oo[oo.rolling]; b2=b_1[b_1.triggered&(b_1.delay<=lim)]; b3=b2.copy(); b3['w']=np.where(pd.to_numeric(b3.p025,errors='coerce').fillna(10**9)<=p75,1.,.4); b3['WR']=b3.R*b3.w; t2=tr[tr.rolling&tr.triggered&(tr.delay<=lim)].copy(); th=learn_we(t2); wth[f]=th; b4=b3.copy(); b4['wp']=b4.we>=th; b4['WW']=b4.R*np.where(b4.wp,b4.w,np.minimum(b4.w,.4)); B += [dict(frame=f,version='B0',**met(b0)),dict(frame=f,version='B1',**met(b_1)),dict(frame=f,version='B2',**met(b2)),dict(frame=f,version='B3',**met(b3,'WR')),dict(frame=f,version='B4-WE',**met(b4,'WW'))]
        q=b_1.copy(); q['bucket']=q.delay.map(bucket)
        for g,y in q.groupby('bucket'): D.append(dict(frame=f,bucket=g,n=len(y),avg_R=float(y.R.mean()),tp3_rate=float((y.label=='TP3').mean()),sl_rate=float((y.label=='SL').mean()),median_RR_trigger=float(y.rr_trigger.median()) if y.rr_trigger.notna().any() else None,avg_MFE=float(y.MFE.mean()),avg_MAE=float(y.MAE.mean())))
        for g,y in [('WE_PASS',b2[b2.we>=th]),('WE_FAIL',b2[b2.we<th])]: W.append(dict(frame=f,group=g,threshold=th,retention=len(y)/len(b2) if len(b2) else None,**met(y)))
    pd.DataFrame(B).to_csv(OUT/'b0_b4_oos_2025.csv',index=False); pd.DataFrame(D).to_csv(OUT/'trigger_delay_oos_2025.csv',index=False); pd.DataFrame(W).to_csv(OUT/'wave_energy_oos_2025.csv',index=False)
    S=dict(method='Point-in-time PRICE proxy, not reconstructed MASTER signals; 2024 train / 2025 OOS; Fibonacci Time excluded',rows={'1h':len(h),'15m':len(m),'replays':len(a)},download_failures=b1+b15,trigger_limits_15m=limits,progress_clocks_15m=clocks,wave_energy_thresholds=wth,b0_b4=B,wave_energy=W); (OUT/'summary.json').write_text(json.dumps(S,indent=2,ensure_ascii=False,default=str)); print(json.dumps(S,indent=2,ensure_ascii=False,default=str))
if __name__=='__main__':run()
