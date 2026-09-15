from __future__ import annotations
import io, json, math, os, sys, time, zipfile
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pandas as pd
import requests

START = pd.Timestamp('2024-01-01T00:00:00Z')
SPLIT = pd.Timestamp('2025-01-01T00:00:00Z')
END = pd.Timestamp('2026-01-01T00:00:00Z')
ASSETS = ['BTCUSDT','ETHUSDT']
OUT = Path('research/trading_v3_shadow_backtest/output')
OUT.mkdir(parents=True, exist_ok=True)
COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_buy_base','taker_buy_quote','ignore']
URL='https://data.binance.vision/data/futures/um/monthly/klines/{asset}/{interval}/{asset}-{interval}-{ym}.zip'

WAIT_HOURS=24
TRIGGER_SEARCH_BARS=8
RETEST_SEARCH_BARS=4
TRADE_HORIZON_BARS=144
COOLDOWN_HOURS=6


def months():
    x=START
    while x<END:
        yield x.strftime('%Y-%m')
        x += pd.offsets.MonthBegin(1)


def load_monthly(asset, interval):
    parts=[]; failures=[]
    s=requests.Session(); s.headers['User-Agent']='money-master-trading-v3-shadow/1.0'
    for ym in months():
        u=URL.format(asset=asset, interval=interval, ym=ym)
        ok=False
        for attempt in range(3):
            try:
                r=s.get(u,timeout=60)
                if r.status_code!=200:
                    raise RuntimeError(f'HTTP{r.status_code}')
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    name=[n for n in z.namelist() if n.endswith('.csv')][0]
                    q=pd.read_csv(z.open(name),header=None,names=COLS)
                ts=pd.to_numeric(q.open_time,errors='coerce')
                ts=np.where(ts>1e14, ts/1000, ts)
                q['time']=pd.to_datetime(ts,unit='ms',utc=True,errors='coerce')
                for c in ['open','high','low','close','volume','quote_volume','taker_buy_quote']:
                    q[c]=pd.to_numeric(q[c],errors='coerce')
                parts.append(q[['time','open','high','low','close','volume','quote_volume','taker_buy_quote']])
                ok=True; break
            except Exception as e:
                if attempt==2: failures.append(f'{asset}:{interval}:{ym}:{type(e).__name__}:{e}')
                time.sleep(1.5*(attempt+1))
        print(asset, interval, ym, 'OK' if ok else 'FAIL', flush=True)
    if not parts:
        raise RuntimeError(f'no data for {asset} {interval}')
    d=pd.concat(parts,ignore_index=True).dropna().drop_duplicates('time').sort_values('time')
    d=d[(d.time>=START)&(d.time<END)].set_index('time')
    return d, failures


def atr(df,n=14):
    pc=df.close.shift(1)
    tr=pd.concat([(df.high-df.low).abs(),(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean()


def rsi(s,n=14):
    d=s.diff(); up=d.clip(lower=0); dn=(-d).clip(lower=0)
    au=up.ewm(alpha=1/n,adjust=False,min_periods=n).mean(); ad=dn.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    rs=au/ad.replace(0,np.nan)
    return 100-(100/(1+rs))


def enrich_1h(h):
    x=h.copy()
    x['ema20']=x.close.ewm(span=20,adjust=False).mean(); x['ma50']=x.close.rolling(50).mean(); x['ma200']=x.close.rolling(200).mean()
    x['atr']=atr(x); x['rsi']=rsi(x.close)
    x['macd']=x.close.ewm(span=12,adjust=False).mean()-x.close.ewm(span=26,adjust=False).mean()
    x['macd_sig']=x.macd.ewm(span=9,adjust=False).mean()
    x['vr']=x.volume/x.volume.rolling(20).mean()
    x['tb_ratio']=x.taker_buy_quote/x.quote_volume.replace(0,np.nan)
    x['swing_lo12']=x.low.shift(1).rolling(12).min(); x['swing_hi12']=x.high.shift(1).rolling(12).max()
    x['swing_lo24']=x.low.shift(1).rolling(24).min(); x['swing_hi24']=x.high.shift(1).rolling(24).max()
    x['ema_slope']=(x.ema20-x.ema20.shift(3))/x.atr.replace(0,np.nan)
    x['ext']=(x.close-x.ema20).abs()/x.atr.replace(0,np.nan)
    typ=(x.high+x.low+x.close)/3
    day=x.index.floor('D')
    pv=(typ*x.volume).groupby(day).cumsum(); vv=x.volume.groupby(day).cumsum()
    x['vwap']=pv/vv.replace(0,np.nan)
    dd=pd.DataFrame({'high':x.high.resample('1D').max(),'low':x.low.resample('1D').min(),'close':x.close.resample('1D').last()}).shift(1)
    x['pdh']=dd.high.reindex(x.index,method='ffill'); x['pdl']=dd.low.reindex(x.index,method='ffill'); x['pdc']=dd.close.reindex(x.index,method='ffill')
    x['atrp']=x.atr/x.close
    x['atr_pct']=x.atrp.rolling(24*30,min_periods=24*7).rank(pct=True)
    return x


def resample(h,rule):
    x=pd.DataFrame({
        'open':h.open.resample(rule,label='left',closed='left').first(),
        'high':h.high.resample(rule,label='left',closed='left').max(),
        'low':h.low.resample(rule,label='left',closed='left').min(),
        'close':h.close.resample(rule,label='left',closed='left').last(),
        'volume':h.volume.resample(rule,label='left',closed='left').sum(),
        'quote_volume':h.quote_volume.resample(rule,label='left',closed='left').sum(),
        'taker_buy_quote':h.taker_buy_quote.resample(rule,label='left',closed='left').sum(),
    }).dropna()
    x['ema20']=x.close.ewm(span=20,adjust=False).mean(); x['ma50']=x.close.rolling(50).mean(); x['atr']=atr(x)
    x['ema_slope']=(x.ema20-x.ema20.shift(3))/x.atr.replace(0,np.nan)
    return x


def asof(df,t):
    z=df[df.index<t]
    return None if z.empty else z.iloc[-1]


def last_impulse_fib(h4,t,direction,lookback=24):
    z=h4[h4.index<t].tail(lookback)
    if len(z)<8: return None
    hi_idx=z.high.idxmax(); lo_idx=z.low.idxmin(); hi=float(z.loc[hi_idx,'high']); lo=float(z.loc[lo_idx,'low'])
    if hi<=lo: return None
    if direction=='LONG' and not (lo_idx < hi_idx): return None
    if direction=='SHORT' and not (hi_idx < lo_idx): return None
    ratios=[0.382,0.5,0.618,0.786]
    if direction=='LONG': levels={r:hi-r*(hi-lo) for r in ratios}; ext={1.272:hi+0.272*(hi-lo),1.618:hi+0.618*(hi-lo)}
    else: levels={r:lo+r*(hi-lo) for r in ratios}; ext={1.272:lo-0.272*(hi-lo),1.618:lo-0.618*(hi-lo)}
    return {'lo':lo,'hi':hi,'levels':levels,'ext':ext,'lo_idx':str(lo_idx),'hi_idx':str(hi_idx)}


def anchored_vwap(h,t,direction,lookback=48):
    z=h[h.index<t].tail(lookback)
    if len(z)<12:return np.nan
    anchor=z.low.idxmin() if direction=='LONG' else z.high.idxmax()
    q=z[z.index>=anchor]
    typ=(q.high+q.low+q.close)/3
    den=q.volume.sum()
    return float((typ*q.volume).sum()/den) if den>0 else np.nan


def cluster_levels(levels,atrv):
    arr=[(float(p),src,w) for p,src,w in levels if pd.notna(p)]
    if not arr:return None
    arr.sort(key=lambda a:a[0]); clusters=[]; cur=[arr[0]]
    gap=max(atrv*0.25,1e-12)
    for item in arr[1:]:
        if item[0]-np.mean([x[0] for x in cur])<=gap: cur.append(item)
        else: clusters.append(cur); cur=[item]
    clusters.append(cur)
    def key(c):
        fam=len(set(x[1] for x in c)); wt=sum(x[2] for x in c); return (fam,wt)
    c=max(clusters,key=key)
    return {'price':float(np.average([x[0] for x in c],weights=[x[2] for x in c])),'sources':[x[1] for x in c],'family_count':len(set(x[1] for x in c))}


def regime_label(r4):
    if r4 is None or pd.isna(r4.atr) or r4.atr<=0:return 'UNKNOWN'
    sep=abs(r4.ema20-r4.ma50)/r4.atr if pd.notna(r4.ma50) else 0
    sl=abs(r4.ema_slope) if pd.notna(r4.ema_slope) else 0
    if sep>0.8 and sl>0.15:return 'TREND'
    if sep<0.35 and sl<0.12:return 'RANGE'
    return 'TRANSITION'


def pre_score(r,h4,h1d,entry,cluster,fib,direction,avwap,use_fib=True):
    raw=maxp=0.0
    def add(v,m):
        nonlocal raw,maxp; raw+=max(0,min(m,v)); maxp+=m
    align4=(h4.ema20>h4.ma50 and h4.close>h4.ema20) if direction=='LONG' else (h4.ema20<h4.ma50 and h4.close<h4.ema20)
    align1=(r.ema20>r.ma50 and r.close>r.ema20) if direction=='LONG' else (r.ema20<r.ma50 and r.close<r.ema20)
    alignd=(h1d.ema20>h1d.ma50 and h1d.close>h1d.ema20) if direction=='LONG' else (h1d.ema20<h1d.ma50 and h1d.close<h1d.ema20)
    add(5 if align4 else 0,5); add(4 if align1 else 0,4); add(3 if alignd else 0,3)
    slope=(r.ema_slope>0 if direction=='LONG' else r.ema_slope<0)
    add(8 if slope and cluster['family_count']>=2 else (5 if slope else 2),8)
    src=set(cluster['sources']); add(min(5,2*len(src & {'swing','pday','ema'})),5)
    add(min(3,cluster['family_count']),3)
    val=sum(1 for s in ['vwap','avwap'] if s in src); add(min(3,1.5*val),3)
    add(2 if 'pday' in src else 0,2)
    if use_fib: add(1 if 'fib' in src else 0,1)
    vr=float(r.vr) if pd.notna(r.vr) else np.nan
    if pd.notna(vr): add(min(5,max(0,(vr-0.6)/0.9*5)),5)
    tb=float(r.tb_ratio) if pd.notna(r.tb_ratio) else np.nan
    if pd.notna(tb):
        x=(tb-0.5) if direction=='LONG' else (0.5-tb)
        add(min(5,max(0,2.5+x*25)),5)
    ap=float(r.atr_pct) if pd.notna(r.atr_pct) else np.nan
    if pd.notna(ap): add(3 if 0.15<=ap<=0.9 else (1.5 if ap<0.98 else 0),3)
    ext=abs(r.close-entry)/r.atr if r.atr>0 else 99; add(2 if ext<=0.8 else (1 if ext<=1.2 else 0),2)
    risk_eff=abs(r.close-entry)/r.atr if r.atr>0 else 99; add(2 if risk_eff<=1.0 else 0,2)
    if pd.notna(r.rsi) and pd.notna(r.macd) and pd.notna(r.macd_sig):
        mom=(r.rsi>=50 and r.macd>=r.macd_sig) if direction=='LONG' else (r.rsi<=50 and r.macd<=r.macd_sig)
        add(2 if mom else 0,2)
    add(1 if abs(r.ema_slope)>=0.2 else 0.4,1)
    return (70*raw/maxp if maxp else np.nan, raw, maxp)


def reaction_score(bar,nextbar,r1h,direction,delay):
    raw=maxp=0.0
    def add(v,m):
        nonlocal raw,maxp; raw+=max(0,min(m,v)); maxp+=m
    rng=max(bar.high-bar.low,1e-12)
    body=abs(bar.close-bar.open)/rng
    wick=((min(bar.open,bar.close)-bar.low)/rng) if direction=='LONG' else ((bar.high-max(bar.open,bar.close))/rng)
    patt=(bar.close>bar.open) if direction=='LONG' else (bar.close<bar.open)
    hold=(nextbar.low>=bar.low) if direction=='LONG' else (nextbar.high<=bar.high)
    add(6 if patt else 0,6); add(min(2,(body+wick)*1.5),2); add(2 if hold else 0,2)
    add(4 if patt and body>=0.25 else 1,4)
    tb=float(bar.taker_buy_quote/bar.quote_volume) if bar.quote_volume>0 else np.nan
    if pd.notna(tb):
        ok=tb>=0.52 if direction=='LONG' else tb<=0.48
        add(3 if ok else (1.5 if abs(tb-0.5)<0.02 else 0),3)
    add(2 if delay<=2 else (1 if delay<=4 else 0),2); add(1 if delay<=6 else 0,1); add(2,2)
    return 30*raw/maxp if maxp else np.nan


def build_candidates(asset,h,use_fib,buffer_atr,model):
    h4=resample(h,'4h'); hd=resample(h,'1D')
    cands=[]; last=None
    for i,(t,r) in enumerate(h.iterrows()):
        if i<250 or any(pd.isna(v) for v in [r.ema20,r.ma50,r.atr,r.swing_lo12,r.swing_hi12,r.vr,r.tb_ratio]): continue
        if last is not None and (t-last)<pd.Timedelta(hours=COOLDOWN_HOURS): continue
        r4=asof(h4,t+pd.Timedelta(hours=1)); rd=asof(hd,t+pd.Timedelta(hours=1))
        if r4 is None or rd is None or pd.isna(r4.ma50) or pd.isna(rd.ma50): continue
        if r4.ema20>r4.ma50 and r4.ema_slope>0: direction='LONG'
        elif r4.ema20<r4.ma50 and r4.ema_slope<0: direction='SHORT'
        else: continue
        if model=='baseline':
            ok=(r.ema20>r.ma50 and r.ema_slope>0 and r.close>r.ema20 and r.ext<=1.5) if direction=='LONG' else (r.ema20<r.ma50 and r.ema_slope<0 and r.close<r.ema20 and r.ext<=1.5)
            if not ok: continue
            entry=float(r.ema20); cluster={'price':entry,'sources':['ema'],'family_count':1}; fib=None; avw=np.nan; pre=np.nan
        else:
            conflict=(r.ema20<r.ma50 and r.ema_slope<-0.15) if direction=='LONG' else (r.ema20>r.ma50 and r.ema_slope>0.15)
            if conflict or r.ext>2.2: continue
            fib=last_impulse_fib(h4,t+pd.Timedelta(hours=1),direction)
            avw=anchored_vwap(h,t+pd.Timedelta(hours=1),direction)
            levels=[]
            def addlev(p,src,w=1.0):
                if pd.isna(p): return
                if direction=='LONG' and p<=r.close+0.15*r.atr and p>=r.close-1.5*r.atr: levels.append((p,src,w))
                if direction=='SHORT' and p>=r.close-0.15*r.atr and p<=r.close+1.5*r.atr: levels.append((p,src,w))
            addlev(r.ema20,'ema',1.2); addlev(r.ma50,'ema',1.0)
            addlev(r.swing_lo12 if direction=='LONG' else r.swing_hi12,'swing',1.4)
            addlev(r.pdl if direction=='LONG' else r.pdh,'pday',1.2)
            addlev(r.vwap,'vwap',1.1); addlev(avw,'avwap',1.1)
            if use_fib and fib:
                for p in fib['levels'].values(): addlev(p,'fib',1.0)
            cluster=cluster_levels(levels,float(r.atr))
            if cluster is None or cluster['family_count']<2: continue
            entry=cluster['price']
            pre,_,_=pre_score(r,r4,rd,entry,cluster,fib,direction,avw,use_fib)
            if not pd.notna(pre) or pre<65: continue
        if direction=='LONG':
            base=min(float(r.swing_lo12), entry-0.8*float(r.atr)); stop=base-buffer_atr*float(r.atr); risk=entry-stop
        else:
            base=max(float(r.swing_hi12), entry+0.8*float(r.atr)); stop=base+buffer_atr*float(r.atr); risk=stop-entry
        if risk<=0 or risk>2.5*float(r.atr): continue
        tp=entry+3*risk if direction=='LONG' else entry-3*risk
        if abs(tp-entry)>5*float(r.atr): continue
        sig=t+pd.Timedelta(hours=1)
        cands.append({'asset':asset,'model':model,'fib':use_fib,'direction':direction,'sig':sig,'entry':entry,'stop':stop,'tp':tp,'risk':risk,'pre_score':pre,'regime':regime_label(r4),'atr':float(r.atr),'sources':'|'.join(cluster['sources'])})
        last=t
    return cands


def replay(cands,m30,h1):
    rows=[]
    for s in cands:
        z=m30[(m30.index>=s['sig'])&(m30.index<s['sig']+pd.Timedelta(hours=WAIT_HOURS))]
        if z.empty: continue
        touchmask=(z.low<=s['entry'])&(z.high>=s['entry'])
        if not touchmask.any(): continue
        touch_idx=z.index[np.argmax(touchmask.values)]
        p=m30.index.get_loc(touch_idx)
        trig=None; delay=None; rscore=np.nan
        for k in range(p,min(len(m30)-1,p+TRIGGER_SEARCH_BARS+1)):
            b=m30.iloc[k]; n=m30.iloc[k+1]
            if s['direction']=='LONG' and b.low<=s['stop']: break
            if s['direction']=='SHORT' and b.high>=s['stop']: break
            if s['direction']=='LONG': ok=b.close>s['entry'] and b.close>b.open and b.low<=s['entry']+0.25*s['risk'] and n.low>=b.low
            else: ok=b.close<s['entry'] and b.close<b.open and b.high>=s['entry']-0.25*s['risk'] and n.high<=b.high
            if ok:
                trig=k; delay=k-p
                r1=asof(h1,m30.index[k]+pd.Timedelta(minutes=30))
                rscore=reaction_score(b,n,r1,s['direction'],delay) if r1 is not None else np.nan
                break
        if trig is None: continue
        fill=None
        for k in range(trig+1,min(len(m30),trig+1+RETEST_SEARCH_BARS)):
            b=m30.iloc[k]
            if s['direction']=='LONG' and b.low<=s['stop']: break
            if s['direction']=='SHORT' and b.high>=s['stop']: break
            if b.low<=s['entry']<=b.high: fill=k; break
        if fill is None:
            zz=m30.iloc[trig+1:min(len(m30),trig+25)]
            miss=(zz.high.max()>=s['entry']+s['risk']) if s['direction']=='LONG' else (zz.low.min()<=s['entry']-s['risk'])
            rows.append({**s,'touch':str(touch_idx),'triggered':True,'filled':False,'missed_move':bool(miss),'delay30':delay,'reaction_score':rscore,'total_score':(s['pre_score']+rscore if pd.notna(s['pre_score']) else np.nan)})
            continue
        zz=m30.iloc[fill:min(len(m30),fill+TRADE_HORIZON_BARS+1)]
        if zz.empty: continue
        if s['direction']=='LONG':
            stop_hits=np.where(zz.low.values<=s['stop'])[0]; tp1=np.where(zz.high.values>=s['entry']+s['risk'])[0]; tp2=np.where(zz.high.values>=s['entry']+2*s['risk'])[0]; tp3=np.where(zz.high.values>=s['tp'])[0]
            mfe=(zz.high.max()-s['entry'])/s['risk']; mae=(s['entry']-zz.low.min())/s['risk']; terminal=(zz.close.iloc[-1]-s['entry'])/s['risk']
        else:
            stop_hits=np.where(zz.high.values>=s['stop'])[0]; tp1=np.where(zz.low.values<=s['entry']-s['risk'])[0]; tp2=np.where(zz.low.values<=s['entry']-2*s['risk'])[0]; tp3=np.where(zz.low.values<=s['tp'])[0]
            mfe=(s['entry']-zz.low.min())/s['risk']; mae=(zz.high.max()-s['entry'])/s['risk']; terminal=(s['entry']-zz.close.iloc[-1])/s['risk']
        sp=int(stop_hits[0]) if len(stop_hits) else None; t3=int(tp3[0]) if len(tp3) else None
        if t3 is not None and (sp is None or t3<sp): R=3.0; label='TP3'
        elif sp is not None and (t3 is None or sp<t3): R=-1.0; label='SL'
        else: R=float(np.clip(terminal,-1,3)); label='TIME'
        false_start=(label=='SL' and sp is not None and sp<=8)
        total=(s['pre_score']+rscore) if pd.notna(s['pre_score']) else np.nan
        rows.append({**s,'touch':str(touch_idx),'triggered':True,'filled':True,'missed_move':False,'delay30':delay,'reaction_score':rscore,'total_score':total,'fill_time':str(m30.index[fill]),'R':R,'label':label,'MFE':float(mfe),'MAE':float(mae),'tp1_hit':bool(len(tp1)),'tp2_hit':bool(len(tp2)),'tp3_hit':bool(len(tp3)),'false_start':bool(false_start)})
    return pd.DataFrame(rows)


def metrics(df):
    if df.empty:return {'n':0}
    x=df[df.filled==True].copy() if 'filled' in df else df.copy()
    if x.empty:return {'n':0,'candidates':len(df),'missed_move_rate':float(df.missed_move.mean()) if 'missed_move' in df else None}
    r=pd.to_numeric(x.R,errors='coerce').dropna(); neg=-r[r<0].sum(); pos=r[r>0].sum(); eq=r.cumsum(); dd=(eq-eq.cummax()).min() if len(eq) else 0
    eff=(x.MFE/(x.MFE+x.MAE).replace(0,np.nan)).clip(0,1)
    return {'n':int(len(x)),'candidates':int(len(df)),'expectancy_R':float(r.mean()),'PF':float(pos/neg) if neg>0 else None,'maxDD_R':float(dd),'win_rate':float((r>0).mean()),'false_start_rate':float(x.false_start.mean()),'avg_MFE_R':float(x.MFE.mean()),'avg_MAE_R':float(x.MAE.mean()),'entry_efficiency':float(eff.mean()),'TP1_rate':float(x.tp1_hit.mean()),'TP2_rate':float(x.tp2_hit.mean()),'TP3_rate':float(x.tp3_hit.mean()),'missed_move_rate':float(df.missed_move.mean()) if 'missed_move' in df and len(df) else 0.0,'median_trigger_delay_30m':float(pd.to_numeric(x.delay30,errors='coerce').median())}


def bootstrap_diff(a,b,nboot=1500,seed=7):
    ra=np.asarray(pd.to_numeric(a.R,errors='coerce').dropna()); rb=np.asarray(pd.to_numeric(b.R,errors='coerce').dropna())
    if len(ra)<20 or len(rb)<20:return None
    rng=np.random.default_rng(seed); vals=[]
    for _ in range(nboot): vals.append(rng.choice(ra,len(ra),replace=True).mean()-rng.choice(rb,len(rb),replace=True).mean())
    return {'mean_diff':float(np.mean(vals)),'ci95':[float(np.quantile(vals,.025)),float(np.quantile(vals,.975))]}


def select_buffer(asset,h,m30,use_fib):
    rows=[]
    for buf in [0.0,0.10,0.15,0.20]:
        c=build_candidates(asset,h,use_fib,buf,'v3'); d=replay(c,m30,h)
        if not d.empty:
            ft=pd.to_datetime(d.get('fill_time'),errors='coerce',utc=True); tr=d[(d.filled==True)&(ft<SPLIT)&(pd.to_numeric(d.total_score,errors='coerce')>=78)]
        else: tr=d
        m=metrics(tr); rows.append((m.get('expectancy_R',-99),m.get('PF') or -99,m.get('n',0),buf,d,m))
    elig=[x for x in rows if x[2]>=40]
    best=max(elig or rows,key=lambda x:(x[0],x[1],x[2]))
    return best[3], best[4], [{'buffer':x[3],**x[5]} for x in rows]


def subset_oos(d,score_filter=False):
    if d.empty:return d
    ft=pd.to_datetime(d.get('fill_time'),errors='coerce',utc=True)
    x=d[(d.filled==True)&(ft>=SPLIT)&(ft<END)].copy()
    if score_filter: x=x[pd.to_numeric(x.total_score,errors='coerce')>=78]
    return x


def run_asset(asset):
    h0,f1=load_monthly(asset,'1h'); m0,f30=load_monthly(asset,'30m')
    h=enrich_1h(h0); m30=m0.copy()
    base_c=build_candidates(asset,h,False,0.05,'baseline'); base=replay(base_c,m30,h)
    bf,vf,gridf=select_buffer(asset,h,m30,False); bfi,vfi,gridfi=select_buffer(asset,h,m30,True)
    bo=subset_oos(base,False); vo=subset_oos(vf,True); vfo=subset_oos(vfi,True)
    base.to_csv(OUT/f'{asset}_baseline_replays.csv',index=False); vf.to_csv(OUT/f'{asset}_v3_no_fib_replays.csv',index=False); vfi.to_csv(OUT/f'{asset}_v3_fib_replays.csv',index=False)
    res={'asset':asset,'failures':f1+f30,'selected_atr_buffer_no_fib':bf,'selected_atr_buffer_fib':bfi,'train_buffer_grid_no_fib':gridf,'train_buffer_grid_fib':gridfi,'oos':{'baseline':metrics(bo),'v3_no_fib':metrics(vo),'v3_fib':metrics(vfo)},'bootstrap_v3fib_minus_baseline':bootstrap_diff(vfo,bo),'bootstrap_v3fib_minus_v3nofib':bootstrap_diff(vfo,vo),'splits':{}}
    for name,d in [('baseline',bo),('v3_no_fib',vo),('v3_fib',vfo)]:
        res['splits'][name]={'direction':{},'regime':{}}
        for g,y in d.groupby('direction'):res['splits'][name]['direction'][g]=metrics(y)
        for g,y in d.groupby('regime'):res['splits'][name]['regime'][g]=metrics(y)
    return res,bo,vo,vfo


def combine(dfs):
    return pd.concat([d for d in dfs if not d.empty],ignore_index=True) if any(not d.empty for d in dfs) else pd.DataFrame()


def verdict(summary):
    b=summary['combined']['baseline']; v=summary['combined']['v3_fib']; nof=summary['combined']['v3_no_fib']; reasons=[]
    if not b.get('n') or not v.get('n') or v['n']<80: return 'V3 PARTIAL — MORE VALIDATION REQUIRED',['insufficient OOS trades']
    exp_ok=v['expectancy_R']>b['expectancy_R']; pf_ok=(v['PF'] or 0)>(b['PF'] or 0); dd_ok=abs(v['maxDD_R'])<=abs(b['maxDD_R'])*1.15; fs_ok=v['false_start_rate']<=b['false_start_rate']+0.03
    asset_ok=True
    for a in ASSETS:
        aa=summary['assets'][a]['oos']
        if aa['v3_fib'].get('n',0)<25: asset_ok=False; reasons.append(f'{a} low sample')
        elif aa['v3_fib']['expectancy_R']<aa['baseline']['expectancy_R']-0.10: asset_ok=False; reasons.append(f'{a} material expectancy degradation')
    regimes=[g for g,m in summary['combined_splits']['v3_fib']['regime'].items() if m.get('n',0)>=20 and m.get('expectancy_R',-99)>-0.05]; regime_ok=len(regimes)>=2
    fib_eff=v['expectancy_R']-nof['expectancy_R']; fib_ok=fib_eff>=-0.03
    ci=summary['bootstrap']['v3fib_minus_baseline']; ci_ok=bool(ci and ci['ci95'][0]>-0.05)
    reasons += [f'expectancy {exp_ok}',f'PF {pf_ok}',f'drawdown {dd_ok}',f'false-start {fs_ok}',f'asset stability {asset_ok}',f'regime stability {regime_ok}',f'Fib effect {fib_eff:+.3f}R',f'CI noninferiority {ci_ok}']
    if exp_ok and pf_ok and dd_ok and fs_ok and asset_ok and regime_ok and fib_ok and ci_ok: return 'V3 PASS — WORK MIGRATION CANDIDATE',reasons
    if v['expectancy_R']<b['expectancy_R']-0.10 and (v['PF'] or 0)<(b['PF'] or 0) and not dd_ok: return 'V3 FAIL — KEEP CURRENT BASELINE',reasons
    return 'V3 PARTIAL — MORE VALIDATION REQUIRED',reasons


def main():
    asset_res={}; B=[]; N=[]; F=[]
    for a in ASSETS:
        r,b,n,f=run_asset(a); asset_res[a]=r; B.append(b); N.append(n); F.append(f)
    cb,cn,cf=combine(B),combine(N),combine(F)
    summary={'methodology':{'period':'2024 train / 2025 OOS','assets':ASSETS,'execution_proxy':'1D context -> 4H regime -> 1H setup/zone -> 30m completed trigger + retest','point_in_time':True,'optional_missing':'OI/funding-depth/liquidation heatmap/on-chain not reconstructed; kline-native taker_buy_quote used as historical order-flow proxy','score_threshold':'V3 total score >=78 research seed only','baseline':'CURRENT/TIME VALIDITY style price proxy on same execution path','fib':'0.382/0.5/0.618/0.786 retracement from objectively completed 4H impulse; Time Fib OFF'},'assets':asset_res,'combined':{'baseline':metrics(cb),'v3_no_fib':metrics(cn),'v3_fib':metrics(cf)},'combined_splits':{},'bootstrap':{}}
    for name,d in [('baseline',cb),('v3_no_fib',cn),('v3_fib',cf)]:
        summary['combined_splits'][name]={'direction':{},'regime':{}}
        for g,y in d.groupby('direction'):summary['combined_splits'][name]['direction'][g]=metrics(y)
        for g,y in d.groupby('regime'):summary['combined_splits'][name]['regime'][g]=metrics(y)
    summary['bootstrap']['v3fib_minus_baseline']=bootstrap_diff(cf,cb); summary['bootstrap']['v3fib_minus_v3nofib']=bootstrap_diff(cf,cn)
    vd,rs=verdict(summary); summary['verdict']=vd; summary['verdict_reasons']=rs
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    spec={'status':vd,'period':'2024 train / 2025 OOS','selected':{},'fib_verdict':None}
    for a in ASSETS: spec['selected'][a]={'atr_buffer_no_fib':asset_res[a]['selected_atr_buffer_no_fib'],'atr_buffer_fib':asset_res[a]['selected_atr_buffer_fib']}
    fibdiff=summary['combined']['v3_fib'].get('expectancy_R',np.nan)-summary['combined']['v3_no_fib'].get('expectancy_R',np.nan); spec['fib_verdict']='KEEP' if pd.notna(fibdiff) and fibdiff>=-0.03 else 'REMOVE_OR_REDUCE'
    (OUT/'VALIDATED_ENGINE_SPEC.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding='utf-8')
    def fm(m):
        if not m or not m.get('n'): return 'n=0'
        return f"n={m['n']} | Exp={m['expectancy_R']:.3f}R | PF={m['PF']:.3f} | DD={m['maxDD_R']:.1f}R | FS={m['false_start_rate']:.1%} | MFE={m['avg_MFE_R']:.2f} | MAE={m['avg_MAE_R']:.2f} | TP3={m['TP3_rate']:.1%}"
    lines=['# MASTER TRADING V3 SHADOW BACKTEST','',f"**Verdict: {vd}**",'', '## OOS 2025 Combined',f"- Baseline: {fm(summary['combined']['baseline'])}",f"- V3 no Fib: {fm(summary['combined']['v3_no_fib'])}",f"- V3 + Fib: {fm(summary['combined']['v3_fib'])}",'']
    for a in ASSETS: lines += [f'## {a}',f"- Baseline: {fm(asset_res[a]['oos']['baseline'])}",f"- V3 no Fib: {fm(asset_res[a]['oos']['v3_no_fib'])}",f"- V3 + Fib: {fm(asset_res[a]['oos']['v3_fib'])}",'']
    lines += ['## Verdict reasons']+[f'- {x}' for x in rs]+['','## Data honesty','- No historical OI/depth/liquidation heatmap/on-chain values were reconstructed.','- Historical Binance kline taker-buy quote is used only as a native order-flow proxy.','- This is a point-in-time price/volume/order-flow proxy shadow backtest, not a reconstruction of historical human chart annotations.']
    (OUT/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'verdict':vd,'combined':summary['combined'],'bootstrap':summary['bootstrap']},indent=2),flush=True)

if __name__=='__main__': main()
