from __future__ import annotations
import io, os, json, math, zipfile
from pathlib import Path
import requests
import numpy as np
import pandas as pd

ASSETS=['BTCUSDT','ETHUSDT']
START='2023-01-01'; END='2025-12-31 23:59:59'
TRAIN=('2023-01-01','2023-12-31 23:59:59')
VAL=('2024-01-01','2024-12-31 23:59:59')
OOS=('2025-01-01','2025-12-31 23:59:59')
INTERVAL='30m'; HORIZON_BARS=48
CACHE=Path(os.environ.get('V3_CACHE','/tmp/trading_v3_cache')); CACHE.mkdir(parents=True,exist_ok=True)
OUT=Path(os.environ.get('V3_OUT','research/v3_shadow_backtest/output')); OUT.mkdir(parents=True,exist_ok=True)
RATIOS=[0.382,0.5,0.618,0.786]
RNG=np.random.default_rng(260915)

def months(start,end):
    return [str(x) for x in pd.period_range(pd.Timestamp(start),pd.Timestamp(end),freq='M')]

def _norm_ms(x):
    x=pd.to_numeric(x,errors='coerce')
    med=float(x.dropna().median()) if x.notna().any() else 0
    if med>1e14: x=x/1000.0
    return x

def fetch_month(asset,market,ym):
    rel=(f'data/futures/um/monthly/klines/{asset}/{INTERVAL}/{asset}-{INTERVAL}-{ym}.zip'
         if market=='futures' else
         f'data/spot/monthly/klines/{asset}/{INTERVAL}/{asset}-{INTERVAL}-{ym}.zip')
    url='https://data.binance.vision/'+rel
    dest=CACHE/f'{market}-{asset}-{INTERVAL}-{ym}.zip'
    if not dest.exists():
        r=requests.get(url,timeout=45)
        if r.status_code!=200: raise RuntimeError(f'download {r.status_code} {url}')
        dest.write_bytes(r.content)
    return dest

def parse_zip(path):
    cols=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_buy_volume','taker_buy_quote','ignore']
    with zipfile.ZipFile(path) as z: raw=z.read(z.namelist()[0])
    df=pd.read_csv(io.BytesIO(raw),header=None,names=cols)
    df['open_time']=pd.to_numeric(df['open_time'],errors='coerce'); df=df[df.open_time.notna()].copy()
    for c in cols:
        if c not in ('open_time','close_time'): df[c]=pd.to_numeric(df[c],errors='coerce')
    df['close_time']=_norm_ms(df.close_time)
    df['ts']=pd.to_datetime(df.close_time,unit='ms',utc=True)
    return df.set_index('ts').sort_index()

def load_market(asset,market):
    parts=[parse_zip(fetch_month(asset,market,ym)) for ym in months(START,END)]
    x=pd.concat(parts).sort_index(); x=x[~x.index.duplicated(keep='last')]
    x=x.loc[pd.Timestamp(START,tz='UTC'):pd.Timestamp(END,tz='UTC')]
    x['sell_quote']=(x.quote_volume-x.taker_buy_quote).clip(lower=0)
    x['delta_quote']=x.taker_buy_quote-x.sell_quote
    x['taker_ratio']=x.taker_buy_quote/x.quote_volume.replace(0,np.nan)
    return x

def agg_ohlcv(df,rule):
    a=df.resample(rule,label='right',closed='right').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum','quote_volume':'sum','taker_buy_quote':'sum','sell_quote':'sum','delta_quote':'sum'}).dropna(subset=['open','close'])
    a['taker_ratio']=a.taker_buy_quote/a.quote_volume.replace(0,np.nan)
    return a

def ema(s,n): return s.ewm(span=n,adjust=False,min_periods=n).mean()
def sma(s,n): return s.rolling(n,min_periods=n).mean()
def atr(df,n=14):
    pc=df.close.shift(1); tr=pd.concat([(df.high-df.low).abs(),(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1)
    return tr.rolling(n,min_periods=n).mean()
def rsi(s,n=14):
    d=s.diff(); up=d.clip(lower=0); dn=(-d.clip(upper=0));
    rs=up.ewm(alpha=1/n,adjust=False,min_periods=n).mean()/dn.ewm(alpha=1/n,adjust=False,min_periods=n).mean().replace(0,np.nan)
    return 100-100/(1+rs)

def add_ind(df):
    x=df.copy(); x['ema20']=ema(x.close,20); x['ma50']=sma(x.close,50); x['ma200']=sma(x.close,200); x['atr14']=atr(x,14); x['rsi14']=rsi(x.close,14)
    fast=ema(x.close,12); slow=ema(x.close,26); macd=fast-slow; x['macd_hist']=macd-ema(macd,9)
    lo=x.low.rolling(9,min_periods=9).min(); hi=x.high.rolling(9,min_periods=9).max(); k=100*(x.close-lo)/(hi-lo).replace(0,np.nan)
    x['kdj_k']=k.ewm(alpha=1/3,adjust=False).mean(); x['kdj_d']=x.kdj_k.ewm(alpha=1/3,adjust=False).mean()
    x['swing_low_12']=x.low.rolling(12,min_periods=12).min().shift(1); x['swing_high_12']=x.high.rolling(12,min_periods=12).max().shift(1)
    x['swing_low_24']=x.low.rolling(24,min_periods=24).min().shift(1); x['swing_high_24']=x.high.rolling(24,min_periods=24).max().shift(1)
    x['vol_ma20']=x.volume.rolling(20,min_periods=20).mean(); x['delta_4']=x.delta_quote.rolling(4,min_periods=4).sum()
    return x

def confirmed_pivots(h1):
    events=[]; H=h1.high.values; L=h1.low.values; idx=h1.index
    for i in range(4,len(h1)):
        j=i-2
        if H[j]>H[j-1] and H[j]>=H[j-2] and H[j]>H[j+1] and H[j]>=H[j+2]: events.append((idx[i],idx[j],'H',float(H[j])))
        if L[j]<L[j-1] and L[j]<=L[j-2] and L[j]<L[j+1] and L[j]<=L[j+2]: events.append((idx[i],idx[j],'L',float(L[j])))
    return sorted(events,key=lambda x:x[0])

def add_session_vwap(df):
    x=df.copy(); day=x.index.floor('D'); tp=(x.high+x.low+x.close)/3; pv=tp*x.volume
    x['session_vwap']=pv.groupby(day).cumsum()/x.volume.groupby(day).cumsum().replace(0,np.nan)
    return x

def add_avwap_fib(df30,h1):
    x=df30.copy(); ev=confirmed_pivots(h1); av=np.full(len(x),np.nan); fdir=np.zeros(len(x),dtype=int); flvls=[np.nan]*len(x)
    p=0; active_anchor=None; piv=[]; cum_pv=0.0; cum_v=0.0; idx=x.index; tp=((x.high+x.low+x.close)/3).values; vol=x.volume.values
    for i,t in enumerate(idx):
        changed=False
        while p<len(ev) and ev[p][0]<=t:
            _,anchor,typ,price=ev[p]; piv.append((anchor,typ,price)); active_anchor=anchor; p+=1; changed=True
        if active_anchor is not None:
            if changed:
                loc=idx.searchsorted(active_anchor,side='left'); loc=min(loc,i); vv=vol[loc:i+1]; tt=tp[loc:i+1]; cum_v=float(np.nansum(vv)); cum_pv=float(np.nansum(vv*tt))
            else:
                cum_v+=0 if np.isnan(vol[i]) else float(vol[i]); cum_pv+=0 if np.isnan(vol[i]) or np.isnan(tp[i]) else float(vol[i]*tp[i])
            av[i]=cum_pv/cum_v if cum_v>0 else np.nan
        if len(piv)>=2:
            a=piv[-1]; b=None
            for q in range(len(piv)-2,-1,-1):
                if piv[q][1]!=a[1]: b=piv[q]; break
            if b is not None and b[0]<a[0]:
                if b[1]=='L' and a[1]=='H' and a[2]>b[2]:
                    low,high=b[2],a[2]; fdir[i]=1; flvls[i]=[high-r*(high-low) for r in RATIOS]
                elif b[1]=='H' and a[1]=='L' and b[2]>a[2]:
                    high,low=b[2],a[2]; fdir[i]=-1; flvls[i]=[low+r*(high-low) for r in RATIOS]
    x['avwap']=av; x['fib_dir']=fdir; x['fib_levels']=flvls
    return x

def ffill_map(base,src,cols,prefix):
    z=src[cols].copy(); z.columns=[prefix+c for c in cols]; return z.reindex(base.index,method='ffill')

def build_features(perp,spot):
    x=add_session_vwap(perp.copy()); s=spot[['delta_quote','taker_ratio']].reindex(x.index).ffill(); x['spot_delta']=s.delta_quote; x['spot_taker_ratio']=s.taker_ratio
    x['rv20']=x.close.pct_change().rolling(48,min_periods=48).std()*math.sqrt(48*365); x['rv_pct']=x.rv20.rolling(48*30,min_periods=48*7).rank(pct=True)
    x['rel_vol']=x.volume/x.volume.rolling(48,min_periods=20).mean(); x['delta_4h']=x.delta_quote.rolling(8,min_periods=4).sum(); x['spot_delta_4h']=x.spot_delta.rolling(8,min_periods=4).sum()
    h1=add_ind(agg_ohlcv(perp,'1h')); h4=add_ind(agg_ohlcv(perp,'4h')); d1=add_ind(agg_ohlcv(perp,'1D'))
    h4['roll_high20']=h4.high.rolling(20,min_periods=10).max().shift(1); h4['roll_low20']=h4.low.rolling(20,min_periods=10).min().shift(1)
    d1['roll_high20']=d1.high.rolling(20,min_periods=10).max().shift(1); d1['roll_low20']=d1.low.rolling(20,min_periods=10).min().shift(1); d1['pdh']=d1.high.shift(1); d1['pdl']=d1.low.shift(1); d1['pdc']=d1.close.shift(1)
    x=add_avwap_fib(x,h1)
    x=x.join(ffill_map(x,h1,['close','ema20','ma50','ma200','atr14','rsi14','macd_hist','kdj_k','kdj_d','swing_low_12','swing_high_12','swing_low_24','swing_high_24','vol_ma20','delta_4'],'h1_'))
    x=x.join(ffill_map(x,h4,['close','ema20','ma50','ma200','atr14','roll_high20','roll_low20'],'h4_'))
    x=x.join(ffill_map(x,d1,['close','ema20','ma50','ma200','roll_high20','roll_low20','pdh','pdl','pdc'],'d1_'))
    x['regime4']=0; bull=(x.h4_close>x.h4_ema20)&(x.h4_ema20>x.h4_ma50)&(x.h4_ema20.diff(8)>0); bear=(x.h4_close<x.h4_ema20)&(x.h4_ema20<x.h4_ma50)&(x.h4_ema20.diff(8)<0); x.loc[bull,'regime4']=1; x.loc[bear,'regime4']=-1
    x['regime1']=0; b1=(x.h1_ema20>x.h1_ma50)&(x.h1_ema20.diff(2)>0); s1=(x.h1_ema20<x.h1_ma50)&(x.h1_ema20.diff(2)<0); x.loc[b1,'regime1']=1; x.loc[s1,'regime1']=-1
    x['day_bias']=0; x.loc[x.d1_close>x.d1_ema20,'day_bias']=1; x.loc[x.d1_close<x.d1_ema20,'day_bias']=-1
    return x.dropna(subset=['h1_atr14','h4_ema20','h1_ema20'])

def nearest_ref(row,direction,v3=True,fib=True,value=True):
    atrv=float(row.h1_atr14); refs=[('h1ema20',row.h1_ema20,'h1'),('h1ma50',row.h1_ma50,'h1'),('h4ema20',row.h4_ema20,'h4'),('swing',row.h1_swing_low_12 if direction==1 else row.h1_swing_high_12,'h1')]
    if v3:
        refs.append(('pdl' if direction==1 else 'pdh',row.d1_pdl if direction==1 else row.d1_pdh,'day'))
        if value: refs += [('svwap',row.session_vwap,'value'),('avwap',row.avwap,'value')]
        if fib and isinstance(row.fib_levels,list) and row.fib_dir==direction:
            refs += [(f'fib{RATIOS[j]}',v,'fib') for j,v in enumerate(row.fib_levels)]
    refs=[r for r in refs if pd.notna(r[1]) and np.isfinite(r[1])]; c=float(row.close); refs=[r for r in refs if abs(float(r[1])-c)<=2.0*atrv]
    if not refs: return None,[]
    refs.sort(key=lambda r:abs(float(r[1])-c)); center=float(refs[0][1]); cluster=[r for r in refs if abs(float(r[1])-center)<=0.25*atrv]
    return center,cluster

def candle_trigger(bar,center,direction,atrv):
    rng=max(float(bar.high-bar.low),1e-12); body=abs(float(bar.close-bar.open)); lower=float(min(bar.open,bar.close)-bar.low); upper=float(bar.high-max(bar.open,bar.close))
    ok=(bar.low<=center+0.20*atrv and bar.close>=center and bar.close>bar.open and lower/rng>=0.20) if direction==1 else (bar.high>=center-0.20*atrv and bar.close<=center and bar.close<bar.open and upper/rng>=0.20)
    return bool(ok),float(min(1.0,body/rng)+(lower/rng if direction==1 else upper/rng))

def score_v3(prev,bar,nxt,direction,cluster,fib_enabled=True,orderflow=True,value=True):
    earned=0.0; avail=12; earned += 5 if prev.regime4==direction else (2 if prev.regime4==0 else 0); earned += 4 if prev.regime1==direction else (1 if prev.regime1==0 else 0); earned += 3 if prev.day_bias in (0,direction) else 0
    avail+=22; structural=(prev.regime1==direction)+(prev.regime4==direction); earned += 8 if structural==2 else (5 if structural==1 else 2); earned += min(5,len(cluster)*1.7); families=len(set(r[2] for r in cluster)); earned += min(3,max(0,families-1)*1.5)
    if value:
        nearv=min(abs(prev.close-prev.session_vwap),abs(prev.close-prev.avwap) if pd.notna(prev.avwap) else 99*prev.h1_atr14); earned += 3 if nearv<=0.35*prev.h1_atr14 else (1.5 if nearv<=0.75*prev.h1_atr14 else 0)
    else: avail-=3
    pdref=prev.d1_pdl if direction==1 else prev.d1_pdh; earned += 2 if pd.notna(pdref) and abs(prev.close-pdref)<=0.5*prev.h1_atr14 else 0
    if fib_enabled: earned += 1 if any(r[2]=='fib' for r in cluster) else 0
    else: avail-=1
    if orderflow:
        avail+=12; rv=float(prev.rel_vol) if pd.notna(prev.rel_vol) else 0; earned += 5 if rv>=1.2 else (4 if rv>=1.0 else (2 if rv>=0.8 else 0)); oq=(prev.delta_4h*direction>0); tq=((prev.taker_ratio-0.5)*direction>0); earned += 5 if oq and tq else (3 if oq or tq else 0); earned += 2 if (prev.spot_delta_4h*direction>0 and prev.delta_4h*direction>0) else 0
    avail+=7; rv_pct=float(prev.rv_pct) if pd.notna(prev.rv_pct) else .5; earned += 3 if 0.15<=rv_pct<=0.85 else (1.5 if rv_pct<0.95 else 0); dist=abs(prev.close-prev.session_vwap)/prev.h1_atr14; earned += 2 if dist<=1 else (1 if dist<=1.5 else 0); earned += 2 if len(cluster)>=2 else (1 if len(cluster)==1 else 0)
    avail+=3; mom=0
    if direction==1: mom += 1 if 35<=prev.h1_rsi14<=65 else 0; mom += .5 if prev.h1_macd_hist>=0 else 0; mom += .5 if prev.h1_kdj_k>=prev.h1_kdj_d else 0
    else: mom += 1 if 35<=prev.h1_rsi14<=65 else 0; mom += .5 if prev.h1_macd_hist<=0 else 0; mom += .5 if prev.h1_kdj_k<=prev.h1_kdj_d else 0
    earned+=min(2,mom); earned+=1 if ((prev.h4_close-prev.h4_ema20)*direction>0 and prev.regime4==direction) else 0
    pre=70*earned/avail
    re=0; ra=10; trig,qual=candle_trigger(bar,float(cluster[0][1]) if cluster else float(prev.close),direction,float(prev.h1_atr14)); re += 6 if trig else 0; re += min(2,max(0,qual)); hold=(nxt.low>=bar.low-0.05*prev.h1_atr14 and nxt.close>=bar.close*0.997) if direction==1 else (nxt.high<=bar.high+0.05*prev.h1_atr14 and nxt.close<=bar.close*1.003); re += 2 if hold else 0
    pe=0; rvol=bar.volume/(prev.volume if prev.volume>0 else np.nan); pe += 4 if pd.notna(rvol) and rvol>=1.1 else (2 if pd.notna(rvol) and rvol>=0.8 else 0); pe += 3 if bar.delta_quote*direction>0 and (bar.taker_ratio-0.5)*direction>0 else 0; ra+=9; re += 9*pe/7
    ra+=5; re+=3; nonch=abs(float(nxt.close-prev.h1_ema20))/prev.h1_atr14<=1.75; re+=2 if nonch else 0
    reaction=30*re/ra; return float(pre+reaction),float(pre),float(reaction)

def targets(prev,entry,direction):
    arr=[prev.h1_swing_high_12,prev.h4_roll_high20,prev.d1_roll_high20] if direction==1 else [prev.h1_swing_low_12,prev.h4_roll_low20,prev.d1_roll_low20]
    return [float(v) if pd.notna(v) and ((v>entry) if direction==1 else (v<entry)) else np.nan for v in arr]

def simulate_path(df,start_i,entry,sl,tps,direction):
    risk=abs(entry-sl); end=min(len(df)-1,start_i+HORIZON_BARS); sub=df.iloc[start_i:end+1]; tp2=tps[1]; outcome='TIME'; exitp=float(sub.iloc[-1].close); hit_bar=None; tp_hits=[False]*3; first_plus1=None
    for k,(_,b) in enumerate(sub.iterrows()):
        fav=((b.high-entry)/risk) if direction==1 else ((entry-b.low)/risk)
        for j,tp in enumerate(tps):
            if pd.notna(tp) and ((b.high>=tp) if direction==1 else (b.low<=tp)): tp_hits[j]=True
        if ((b.low<=sl) if direction==1 else (b.high>=sl)): outcome='SL'; exitp=sl; hit_bar=k; break
        if pd.notna(tp2) and ((b.high>=tp2) if direction==1 else (b.low<=tp2)): outcome='TP2'; exitp=tp2; hit_bar=k; break
        if first_plus1 is None and fav>=1: first_plus1=k
    r=((exitp-entry)*direction)/risk; mfe_full=((sub.high.max()-entry)/risk) if direction==1 else ((entry-sub.low.min())/risk); mae_full=((entry-sub.low.min())/risk) if direction==1 else ((sub.high.max()-entry)/risk)
    early=sub.iloc[:min(8,len(sub))]; lo=float(early.low.min()); hi=float(early.high.max()); den=max(hi-lo,1e-12); eff=1-(entry-lo)/den if direction==1 else 1-(hi-entry)/den; eff=float(np.clip(eff,0,1)); false_start=bool(outcome=='SL' and hit_bar is not None and hit_bar<=6 and (first_plus1 is None or first_plus1>hit_bar))
    return {'R':float(r),'outcome':outcome,'mfe':float(mfe_full),'mae':float(mae_full),'entry_eff':eff,'false_start':false_start,'tp1_hit':tp_hits[0],'tp2_hit':tp_hits[1],'tp3_hit':tp_hits[2],'bars':(hit_bar+1 if hit_bar is not None else len(sub))}

def generate_trades(df,variant,threshold=78,atr_buffer=0.0):
    v3=variant!='baseline'; fib=variant!='v3_nofib'; orderflow=variant!='v3_no_orderflow'; value=variant!='v3_no_value'; trades=[]; rejected=[]; i=2; last_exit=-1
    while i<len(df)-HORIZON_BARS-2:
        if i<=last_exit: i+=1; continue
        prev=df.iloc[i-1]; bar=df.iloc[i]; nxt=df.iloc[i+1]; direction=int(prev.regime4)
        if direction==0: i+=1; continue
        center,cluster=nearest_ref(prev,direction,v3,fib,value)
        if center is None: i+=1; continue
        atrv=float(prev.h1_atr14); half=(.20 if not v3 else .16)*atrv
        if not (bar.low<=center+half and bar.high>=center-half): i+=1; continue
        trig,_=candle_trigger(bar,center,direction,atrv)
        if not trig: i+=1; continue
        hold=(nxt.low>=bar.low-.05*atrv and nxt.close>=center) if direction==1 else (nxt.high<=bar.high+.05*atrv and nxt.close<=center)
        if not hold: i+=1; continue
        entry=float(nxt.close)
        if abs(entry-prev.h1_ema20)>1.75*atrv: i+=1; continue
        vol_ref=float(prev.volume) if prev.volume>0 else np.nan
        if pd.isna(vol_ref) or float(bar.volume)<.55*vol_ref: i+=1; continue
        struct=float(min(prev.h1_swing_low_12,bar.low)) if direction==1 else float(max(prev.h1_swing_high_12,bar.high)); sl=struct-direction*atr_buffer*atrv; risk=abs(entry-sl)
        if risk<=0 or risk>2.5*atrv: i+=1; continue
        tps=targets(prev,entry,direction)
        if pd.isna(tps[1]): i+=1; continue
        rr2=((tps[1]-entry)*direction)/risk
        if rr2<3: i+=1; continue
        score=100.; pre=70.; react=30.
        if v3:
            score,pre,react=score_v3(prev,bar,nxt,direction,cluster,fib,orderflow,value)
            if score<threshold:
                sim=simulate_path(df,i+2,entry,sl,tps,direction); rejected.append({'ts':str(df.index[i+1]),'direction':direction,'score':score,'would_tp2':sim['tp2_hit'],'R':sim['R']}); i+=1; continue
        sim=simulate_path(df,i+2,entry,sl,tps,direction); reg='trend' if prev.regime1==direction else 'transition'; tr={'ts':df.index[i+1],'asset':'','variant':variant,'direction':'LONG' if direction==1 else 'SHORT','regime':reg,'entry':entry,'sl':sl,'tp1':tps[0],'tp2':tps[1],'tp3':tps[2],'rr2':rr2,'score':score,'pre':pre,'reaction':react}; tr.update(sim); trades.append(tr); last_exit=i+2+sim['bars']-1; i=last_exit+1
    return pd.DataFrame(trades),pd.DataFrame(rejected)

def metrics(tr):
    if tr is None or len(tr)==0: return {'n':0,'expectancy':None,'pf':None,'win_rate':None,'maxdd_R':None,'false_start_rate':None,'mfe':None,'mae':None,'entry_eff':None,'tp1_rate':None,'tp2_rate':None,'tp3_rate':None,'stop_rate':None,'time_rate':None}
    r=tr.R.astype(float).values; eq=np.cumsum(r); peaks=np.maximum.accumulate(np.r_[0,eq]); dd=np.r_[0,eq]-peaks; gp=r[r>0].sum(); gl=-r[r<0].sum(); pf=float(gp/gl) if gl>0 else (float('inf') if gp>0 else None)
    return {'n':int(len(tr)),'expectancy':float(np.mean(r)),'pf':pf,'win_rate':float(np.mean(r>0)),'maxdd_R':float(dd.min()),'false_start_rate':float(tr.false_start.mean()),'mfe':float(tr.mfe.mean()),'mae':float(tr.mae.mean()),'entry_eff':float(tr.entry_eff.mean()),'tp1_rate':float(tr.tp1_hit.mean()),'tp2_rate':float(tr.tp2_hit.mean()),'tp3_rate':float(tr.tp3_hit.mean()),'stop_rate':float((tr.outcome=='SL').mean()),'time_rate':float((tr.outcome=='TIME').mean())}

def subset(tr,period):
    if len(tr)==0:return tr
    a=pd.Timestamp(period[0],tz='UTC'); b=pd.Timestamp(period[1],tz='UTC'); return tr[(tr.ts>=a)&(tr.ts<=b)]

def bootstrap_ci(r,n=1500):
    r=np.asarray(r,dtype=float)
    if len(r)<5:return [None,None]
    vals=np.array([RNG.choice(r,size=len(r),replace=True).mean() for _ in range(n)]); return [float(np.quantile(vals,.025)),float(np.quantile(vals,.975))]

def bootstrap_diff(a,b,n=1500):
    a=np.asarray(a,dtype=float); b=np.asarray(b,dtype=float)
    if len(a)<5 or len(b)<5:return {'mean':None,'ci95':[None,None]}
    vals=np.array([RNG.choice(a,len(a),replace=True).mean()-RNG.choice(b,len(b),replace=True).mean() for _ in range(n)]); return {'mean':float(a.mean()-b.mean()),'ci95':[float(np.quantile(vals,.025)),float(np.quantile(vals,.975))]}

def select_config(feature_map):
    grid=[]
    for th in [70,74,78,82,85]:
        for buf in [0,.10,.15,.20]:
            alltr=[]
            for asset,df in feature_map.items():
                tr,_=generate_trades(df,'v3_fib',th,buf); tr['asset']=asset; alltr.append(tr)
            tr=pd.concat(alltr,ignore_index=True); mt=metrics(subset(tr,TRAIN)); mv=metrics(subset(tr,VAL))
            if mt['n']>=30 and mt['expectancy'] is not None:
                rank=mt['expectancy']+.10*max(0,(mt['pf'] or 0)-1)+.003*(mt['maxdd_R'] or 0); grid.append((rank,th,buf,mt,mv))
    grid.sort(key=lambda x:x[0],reverse=True); top=grid[:5]
    positive=[x for x in top if x[4]['n']>=20 and (x[4]['expectancy'] if x[4]['expectancy'] is not None else -99)>0]
    pool=positive or top; chosen=max(pool,key=lambda x:((x[4]['expectancy'] if x[4]['expectancy'] is not None else -99),(x[4]['pf'] or 0),x[4]['maxdd_R'] or -99))
    return chosen[1],chosen[2],chosen[3],chosen[4],[{'threshold':x[1],'atr_buffer':x[2],'train':x[3],'val':x[4]} for x in top]

def main():
    features={}; coverage={}
    for asset in ASSETS:
        print('Loading',asset,flush=True); perp=load_market(asset,'futures'); spot=load_market(asset,'spot'); f=build_features(perp,spot); features[asset]=f
        coverage[asset]={'bars30m':int(len(f)),'start':str(f.index.min()),'end':str(f.index.max()),'spot_perp_overlap_pct':float(f.spot_delta.notna().mean()*100),'historical_OI':'N/A >30d official endpoint','depth':'N/A historical','liquidation_heatmap':'N/A historical','onchain':'N/A historical'}
    th,buf,train_sel,val_sel,topgrid=select_config(features); print('Selected',th,buf,flush=True)
    variants=['baseline','v3_nofib','v3_fib','v3_no_orderflow','v3_no_value']; alltr={}; rej={}
    for var in variants:
        trs=[]; rjs=[]
        for asset,df in features.items():
            tr,rj=generate_trades(df,var,th if var!='baseline' else 0,buf if var!='baseline' else 0); tr['asset']=asset; rj['asset']=asset; trs.append(tr); rjs.append(rj)
        alltr[var]=pd.concat(trs,ignore_index=True); rej[var]=pd.concat(rjs,ignore_index=True)
    atr_ab={}
    for ab in [0,.10,.15,.20]:
        trs=[]
        for asset,df in features.items():
            tr,_=generate_trades(df,'v3_fib',th,ab); tr['asset']=asset; trs.append(tr)
        atr_ab[str(ab)]=metrics(subset(pd.concat(trs,ignore_index=True),OOS))
    result={'study':'MASTER TRADING DAILY ENTRY V3 SHADOW BACKTEST','baseline_proxy_notice':'Current MASTER is manual/discretionary; historical baseline is a deterministic canonical-rule proxy, not reconstructed historical MASTER decisions.','data_source':'Binance public monthly kline archives, 30m spot + USD-M futures; higher frames resampled from closed bars only.','periods':{'train':TRAIN,'validation':VAL,'oos':OOS},'execution_frame':'30m completed trigger; 1H setup; 4H regime; 1D context','coverage':coverage,'selected_config':{'score_threshold':th,'atr_buffer_x_1hATR':buf,'train':train_sel,'validation':val_sel,'top_train_candidates':topgrid},'variants':{},'atr_buffer_ablation_oos':atr_ab,'fib_rules':{'retracement':RATIOS,'time':'OFF','anchor':'confirmed completed 1H pivot pair','score_cap':1}}
    for var,tr in alltr.items():
        result['variants'][var]={}
        for name,per in [('train',TRAIN),('validation',VAL),('oos',OOS)]:
            z=subset(tr,per); d={'combined':metrics(z),'assets':{},'direction':{},'regime':{}}
            if len(z):
                d['combined']['expectancy_ci95']=bootstrap_ci(z.R.values)
                for a in ASSETS:d['assets'][a]=metrics(z[z.asset==a])
                for q in ['LONG','SHORT']:d['direction'][q]=metrics(z[z.direction==q])
                for q in ['trend','transition']:d['regime'][q]=metrics(z[z.regime==q])
            result['variants'][var][name]=d
        rj=rej[var]
        if len(rj):
            z=rj[(pd.to_datetime(rj.ts,utc=True)>=pd.Timestamp(OOS[0],tz='UTC'))&(pd.to_datetime(rj.ts,utc=True)<=pd.Timestamp(OOS[1],tz='UTC'))]; result['variants'][var]['oos']['rejected_trigger_candidates']=int(len(z)); result['variants'][var]['oos']['missed_tp2_rate']=float(z.would_tp2.mean()) if len(z) else None
    bo=subset(alltr['baseline'],OOS); vo=subset(alltr['v3_fib'],OOS); no=subset(alltr['v3_nofib'],OOS); result['oos_expectancy_diff_v3_vs_baseline']=bootstrap_diff(vo.R.values,bo.R.values); result['oos_expectancy_diff_fib_vs_nofib']=bootstrap_diff(vo.R.values,no.R.values)
    mb=metrics(bo); mv=metrics(vo); mn=metrics(no); asset_ok=True
    for a in ASSETS:
        bb=metrics(bo[bo.asset==a]); vv=metrics(vo[vo.asset==a]);
        if vv['n']<10 or vv['expectancy'] is None or bb['expectancy'] is None or vv['expectancy']<bb['expectancy']-.05: asset_ok=False
    dd_ok=(abs(mv['maxdd_R'])<=abs(mb['maxdd_R'])*1.15) if mb['maxdd_R'] and mv['maxdd_R'] else False; fs_ok=(mv['false_start_rate']<=mb['false_start_rate']*1.05+.01) if mb['false_start_rate'] is not None else True; primary=(mv['n']>=50 and mv['expectancy'] is not None and mb['expectancy'] is not None and mv['expectancy']>mb['expectancy'] and (mv['pf'] or 0)>(mb['pf'] or 0)); fib_ok=(mv['expectancy']>=mn['expectancy']-.01 and (mv['pf'] or 0)>=(mn['pf'] or 0)-.03); ci=result['oos_expectancy_diff_v3_vs_baseline']['ci95']; ci_not_bad=(ci[0] is not None and ci[0]>-.05)
    if primary and dd_ok and fs_ok and asset_ok and fib_ok and ci_not_bad: verdict='V3 PASS — WORK MIGRATION CANDIDATE'
    elif mv['expectancy'] is not None and mb['expectancy'] is not None and mv['expectancy']>mb['expectancy'] and (mv['pf'] or 0)>=(mb['pf'] or 0)*.98: verdict='V3 PARTIAL — MORE VALIDATION REQUIRED'
    else: verdict='V3 FAIL — KEEP CURRENT BASELINE'
    result['decision']={'verdict':verdict,'checks':{'primary_exp_pf':primary,'maxdd_ok':dd_ok,'false_start_ok':fs_ok,'asset_stability':asset_ok,'fib_ablation_ok':fib_ok,'bootstrap_not_materially_negative':ci_not_bad},'production_change':False,'ui_change':False}
    result['validated_engine_spec']={'status':'VALIDATED_FOR_WORK_MIGRATION_REVIEW' if verdict.startswith('V3 PASS') else 'CANDIDATE_ONLY','score_threshold':th,'atr_buffer_x_1hATR':buf,'fib_enabled':bool(fib_ok),'fib_score_cap':1,'fibonacci_time':'OFF','frames':'1D context -> 4H regime -> 1H setup -> 30m trigger','hard_gate':['CurrentFresh','TradeFrameLocked','StructuralEntryOrRetest','Completed30mTrigger','Participation','NonChasing','StructuralSL','TP1TP2TP3','RR>=3','NoSevereRiskVeto']}
    for var,tr in alltr.items(): tr.to_csv(OUT/f'trades_{var}.csv',index=False)
    def clean(obj):
        if isinstance(obj,dict): return {k:clean(v) for k,v in obj.items()}
        if isinstance(obj,list): return [clean(v) for v in obj]
        if isinstance(obj,np.floating): obj=float(obj)
        if isinstance(obj,np.integer): return int(obj)
        if isinstance(obj,float) and (math.isnan(obj) or math.isinf(obj)): return None
        return obj
    result=clean(result); (OUT/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8'); (OUT/'validated_engine_spec.json').write_text(json.dumps(result['validated_engine_spec'],ensure_ascii=False,indent=2),encoding='utf-8')
    def fmt(m):
        if not m or not m.get('n'): return 'N/A'
        return f"n={m['n']}, Exp={m['expectancy']:.3f}R, PF={m['pf']:.2f}, MaxDD={m['maxdd_R']:.1f}R, FalseStart={m['false_start_rate']*100:.1f}%"
    lines=['# MASTER TRADING V3 SHADOW BACKTEST REPORT','',f'**최종판정: {verdict}**','','## 방법','- Binance 30m Spot + USD-M Futures 공개 월별 원천데이터','- 2023 Train / 2024 Validation / 2025 OOS','- 4H Regime → 1H Setup → 30m 완성봉 Trigger; 1D Context','- Current MASTER는 수동/재량 시스템이므로 Baseline은 최신 Canonical 명시 규칙의 deterministic proxy이며 과거 채팅 의사결정을 재구성하지 않음.','- OI/Depth/가격별 청산맵/온체인 등 장기 point-in-time 복원 불가능 축은 N/A; 0으로 대체하지 않음.','','## OOS 핵심 비교',f'- CURRENT BASELINE proxy: {fmt(mb)}',f'- V3 CORE - FIB: {fmt(mn)}',f'- V3 CORE + FIB: {fmt(mv)}',f'- 선택 Score threshold: {th}; ATR buffer: {buf} × 1H ATR','','## 판정 체크']
    for k,v in result['decision']['checks'].items(): lines.append(f'- {k}: {"PASS" if v else "FAIL"}')
    lines += ['','## 데이터 한계','- 역사적 OI는 Binance 공식 API가 최근 약 1개월만 제공하므로 장기 OOS에 사후 생성하지 않음.','- Depth 및 가격별 Liquidation Heatmap의 신뢰 가능한 장기 point-in-time archive가 없어 N/A.','- Volume Profile은 30m OHLCV만으로 가격대별 실제 체결분포를 정확히 복원할 수 없으므로 이 1차 검증에서는 N/A.','- 따라서 본 결과는 V3의 가격구조/위치/ATR/VWAP/AVWAP/Fib/Volume/CVD-Taker proxy 축 중심의 Shadow 검증이며, Work 승격 전 실시간 Shadow에서 미복원 축을 추가 검증해야 함.']
    (OUT/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(verdict); print('Baseline',fmt(mb)); print('V3-Fib',fmt(mn)); print('V3+Fib',fmt(mv))

if __name__=='__main__': main()
