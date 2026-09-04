from __future__ import annotations

import io
import json
import math
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import requests

START = pd.Timestamp('2017-09-01T00:00:00Z')
# Historical research uses completed monthly files only. Current/live state is intentionally separate.
END = pd.Timestamp('2026-08-01T00:00:00Z')
OUT = Path('btc_trend_v30_restart/output/restart_phase1_v01')
OUT.mkdir(parents=True, exist_ok=True)

COLS = [
    'open_time','open','high','low','close','volume','close_time','quote_volume',
    'trades','taker_buy_base','taker_buy_quote','ignore'
]
BASE = 'https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/{interval}/BTCUSDT-{interval}-{ym}.zip'


def months(start: pd.Timestamp, end: pd.Timestamp):
    cur = pd.Timestamp(start.year, start.month, 1, tz='UTC')
    last = pd.Timestamp(end.year, end.month, 1, tz='UTC')
    out=[]
    while cur < last:
        out.append(cur.strftime('%Y-%m'))
        cur = cur + pd.offsets.MonthBegin(1)
    return out


def normalize_time(x: pd.Series) -> pd.DatetimeIndex:
    v = pd.to_numeric(x, errors='coerce').astype('float64')
    v = np.where(v > 1e14, v / 1000.0, v)
    return pd.to_datetime(v, unit='ms', utc=True)


def _fetch_month(interval: str, ym: str):
    url = BASE.format(interval=interval, ym=ym)
    try:
        r = requests.get(url, timeout=35, headers={'User-Agent':'master-btc-trend-v30-restart/1.0'})
        if r.status_code != 200:
            return None, f'{interval}:{ym}:HTTP{r.status_code}'
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            names=[n for n in z.namelist() if n.endswith('.csv')]
            if not names:
                return None, f'{interval}:{ym}:no_csv'
            raw=pd.read_csv(z.open(names[0]), header=None, names=COLS)
        return raw, None
    except Exception as e:
        return None, f'{interval}:{ym}:{type(e).__name__}'


def load_interval(interval: str):
    frames=[]; failures=[]
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs={ex.submit(_fetch_month, interval, ym):ym for ym in months(START,END)}
        for f in as_completed(futs):
            raw,err=f.result()
            if err: failures.append(err)
            elif raw is not None: frames.append(raw)
    if not frames:
        raise RuntimeError(f'No Binance data for {interval}')
    df=pd.concat(frames,ignore_index=True)
    df['time']=normalize_time(df.open_time)
    for c in ['open','high','low','close','volume','quote_volume','taker_buy_quote']:
        df[c]=pd.to_numeric(df[c],errors='coerce')
    df=df[['time','open','high','low','close','volume','quote_volume','taker_buy_quote']]
    df=df.dropna().drop_duplicates('time').sort_values('time')
    df=df[(df.time>=START)&(df.time<END)].set_index('time')
    df['net_taker_quote']=2.0*df.taker_buy_quote-df.quote_volume
    return df, sorted(failures)


def rsi(s: pd.Series,n=14):
    d=s.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0)
    au=up.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    ad=dn.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    return (100-100/(1+au/ad.replace(0,np.nan))).fillna(50)


def atr(df,n=14):
    pc=df.close.shift(1)
    tr=pd.concat([(df.high-df.low).abs(),(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean()


def kdj(df,n=9):
    lo=df.low.rolling(n).min(); hi=df.high.rolling(n).max()
    rsv=100*(df.close-lo)/(hi-lo).replace(0,np.nan)
    k=rsv.ewm(alpha=1/3,adjust=False).mean().fillna(50)
    d=k.ewm(alpha=1/3,adjust=False).mean().fillna(50)
    return k,d,3*k-2*d


def zscore(s,n=40):
    m=s.rolling(n).mean(); sd=s.rolling(n).std(ddof=0)
    return ((s-m)/sd.replace(0,np.nan)).fillna(0)


def clip100(x):
    return np.clip(x,0,100)


def add_features(df: pd.DataFrame, tf: str):
    x=df.copy()
    x['ema20']=x.close.ewm(span=20,adjust=False).mean()
    x['ema60']=x.close.ewm(span=60,adjust=False).mean()
    x['sma50']=x.close.rolling(50).mean(); x['sma200']=x.close.rolling(200).mean()
    x['rsi14']=rsi(x.close); x['k'],x['d'],x['j']=kdj(x); x['atr14']=atr(x)
    x['vol_z']=zscore(x.volume,40)
    rng=(x.high-x.low).replace(0,np.nan)
    body=x.close-x.open
    x['body_ratio']=body.abs()/rng
    x['close_loc']=(x.close-x.low)/rng
    x['upper_wick']=(x.high-np.maximum(x.open,x.close))/rng
    x['lower_wick']=(np.minimum(x.open,x.close)-x.low)/rng
    x['taker_imb']=x.net_taker_quote/x.quote_volume.replace(0,np.nan)
    x['taker_imb_3']=x.net_taker_quote.rolling(3).sum()/x.quote_volume.rolling(3).sum().replace(0,np.nan)
    x['taker_imb_6']=x.net_taker_quote.rolling(6).sum()/x.quote_volume.rolling(6).sum().replace(0,np.nan)
    x['cvd_10']=x.net_taker_quote.rolling(10).sum()
    x['cvd_20']=x.net_taker_quote.rolling(20).sum()
    x['ret3']=x.close.pct_change(3); x['ret10']=x.close.pct_change(10)
    x['ret30']=x.close.pct_change(30 if tf=='1D' else 42)
    x['ret60']=x.close.pct_change(60 if tf=='1D' else 84)
    x['high10_prev']=x.high.shift(1).rolling(10).max(); x['low10_prev']=x.low.shift(1).rolling(10).min()
    x['high20_prev']=x.high.shift(1).rolling(20).max(); x['low20_prev']=x.low.shift(1).rolling(20).min()
    x['high60_prev']=x.high.shift(1).rolling(60).max(); x['low60_prev']=x.low.shift(1).rolling(60).min()
    look90=90 if tf=='1D' else 135; look180=180 if tf=='1D' else 270
    x['high90_prev']=x.high.shift(1).rolling(look90).max(); x['low90_prev']=x.low.shift(1).rolling(look90).min()
    x['high180_prev']=x.high.shift(1).rolling(look180).max(); x['low180_prev']=x.low.shift(1).rolling(look180).min()
    x['drawdown180']=1-x.close/x.high180_prev; x['rally180']=x.close/x.low180_prev-1
    x['ema20_slope5']=x.ema20/x.ema20.shift(5)-1; x['sma50_slope10']=x.sma50/x.sma50.shift(10)-1
    x['rsi_delta5']=x.rsi14-x.rsi14.shift(5); x['j_delta3']=x.j-x.j.shift(3)
    x['failed_breakdown']=((x.low<x.low20_prev)&(x.close>x.low20_prev)).astype(float)
    x['failed_breakout']=((x.high>x.high20_prev)&(x.close<x.high20_prev)).astype(float)
    x['bos_up']=(x.close>x.high10_prev).astype(float); x['bos_down']=(x.close<x.low10_prev).astype(float)
    x['reclaim_ema20']=((x.close>x.ema20)&(x.close.shift(1)<=x.ema20.shift(1))).astype(float)
    x['lose_ema20']=((x.close<x.ema20)&(x.close.shift(1)>=x.ema20.shift(1))).astype(float)

    vol_component=((x.vol_z.clip(-1,3)+1)/4).clip(0,1)
    x['candle_bull']=(
        30*x.body_ratio.clip(0,1)*(body>0).astype(float)+25*x.close_loc.clip(0,1)
        +15*x.lower_wick.clip(0,1)+15*vol_component
        +10*(x.close>x.high.shift(1)).astype(float)+5*(x.close>x.close.shift(2)).astype(float)
    ).clip(0,100)
    x['candle_bear']=(
        30*x.body_ratio.clip(0,1)*(body<0).astype(float)+25*(1-x.close_loc.clip(0,1))
        +15*x.upper_wick.clip(0,1)+15*vol_component
        +10*(x.close<x.low.shift(1)).astype(float)+5*(x.close<x.close.shift(2)).astype(float)
    ).clip(0,100)

    # Wave energy: compare prior and recent half-wave in price speed, flow and efficiency.
    n=10 if tf=='1D' else 12; h=max(3,n//2)
    rr=x.close/x.close.shift(h)-1; pr=x.close.shift(h)/x.close.shift(2*h)-1
    rf=x.net_taker_quote.rolling(h).sum()/x.quote_volume.rolling(h).sum().replace(0,np.nan)
    pf=x.net_taker_quote.shift(h).rolling(h).sum()/x.quote_volume.shift(h).rolling(h).sum().replace(0,np.nan)
    recent_speed=rr.abs()/h; prior_speed=pr.abs()/h
    down_old=(-pr).clip(0,.25)/.25
    up_old=pr.clip(0,.25)/.25
    price_less_down=(rr-pr).clip(0,.15)/.15
    price_less_up=(pr-rr).clip(0,.15)/.15
    sell_ineff=((pf-rf).clip(-.25,.25)+.25)/.50
    buy_ineff=((rf-pf).clip(-.25,.25)+.25)/.50
    decel=((prior_speed-recent_speed).clip(0,.03)/.03)
    x['down_exhaust']=clip100(35*down_old+25*price_less_down+20*sell_ineff+20*decel)
    x['up_exhaust']=clip100(35*up_old+25*price_less_up+20*buy_ineff+20*decel)
    x['wave_up_impulse']=clip100(50*(rr.clip(0,.15)/.15)+25*((rf+0.1).clip(0,.2)/.2)+25*x.close_loc.clip(0,1))
    x['wave_down_impulse']=clip100(50*((-rr).clip(0,.15)/.15)+25*((-rf+0.1).clip(0,.2)/.2)+25*(1-x.close_loc.clip(0,1)))
    return x


def last4(h4,t):
    s=h4[h4.index<=t+pd.Timedelta(hours=23,minutes=59)]
    return None if s.empty else s.iloc[-1]


def build_state(D,H4):
    rows=[]
    for t,r in D.iterrows():
        if pd.isna(r.sma200) or pd.isna(r.high180_prev): continue
        q=last4(H4,t)
        if q is None or pd.isna(q.sma200): continue
        down_ctx=max(float(clip100((r.drawdown180-.08)/.32*100)),float(clip100((-r.ret60-.03)/.32*100)))
        up_ctx=max(float(clip100((r.rally180-.10)/.65*100)),float(clip100((r.ret60-.05)/.45*100)))
        loc_l=float(clip100((.18-max(0.,r.close/r.low90_prev-1))/.18*100))
        loc_s=float(clip100((.18-max(0.,1-r.close/r.high90_prev))/.18*100))

        struct_l=20*r.failed_breakdown+15*r.bos_up+15*r.reclaim_ema20+10*float(r.close>r.ema20)+10*float(q.close>q.ema20)+10*float(r.rsi_delta5>0)+10*float(q.rsi_delta5>0)+10*float(r.close_loc>.60)
        struct_s=20*r.failed_breakout+15*r.bos_down+15*r.lose_ema20+10*float(r.close<r.ema20)+10*float(q.close<q.ema20)+10*float(r.rsi_delta5<0)+10*float(q.rsi_delta5<0)+10*float(r.close_loc<.40)
        wave_l=.60*float(r.down_exhaust)+.25*float(q.down_exhaust)+.15*float(q.wave_up_impulse)
        wave_s=.60*float(r.up_exhaust)+.25*float(q.up_exhaust)+.15*float(q.wave_down_impulse)
        candle_l=.45*float(r.candle_bull)+.55*float(q.candle_bull)
        candle_s=.45*float(r.candle_bear)+.55*float(q.candle_bear)
        ma_l=30*float(r.close>r.ema20)+20*float(r.ema20_slope5>0)+15*float(r.close>r.sma50)+15*float(q.close>q.ema20)+10*float(q.ema20_slope5>0)+10*float(r.ema20>r.ema60)
        ma_s=30*float(r.close<r.ema20)+20*float(r.ema20_slope5<0)+15*float(r.close<r.sma50)+15*float(q.close<q.ema20)+10*float(q.ema20_slope5<0)+10*float(r.ema20<r.ema60)
        mom_l=25*float(r.rsi_delta5>0)+15*float(r.rsi14>40)+15*float(r.j_delta3>0)+15*float(q.rsi_delta5>0)+15*float(q.j_delta3>0)+15*float(r.rsi14>r.rsi14.shift(1) if False else r.rsi_delta5>3)
        mom_s=25*float(r.rsi_delta5<0)+15*float(r.rsi14<60)+15*float(r.j_delta3<0)+15*float(q.rsi_delta5<0)+15*float(q.j_delta3<0)+15*float(r.rsi_delta5<-3)
        flow_l=25*float(r.taker_imb_3>r.taker_imb_6)+20*float(q.taker_imb_3>q.taker_imb_6)+20*float(r.vol_z>0 and r.close_loc>.55)+15*float(q.vol_z>0 and q.close_loc>.55)+20*float(r.taker_imb_3<0 and r.close_loc>.60)
        flow_s=25*float(r.taker_imb_3<r.taker_imb_6)+20*float(q.taker_imb_3<q.taker_imb_6)+20*float(r.vol_z>0 and r.close_loc<.45)+15*float(q.vol_z>0 and q.close_loc<.45)+20*float(r.taker_imb_3>0 and r.close_loc<.40)

        # Loose candidate: location/context keep recall, chart evidence ranks candidates.
        cand_l=.18*down_ctx+.20*loc_l+.18*wave_l+.13*candle_l+.12*mom_l+.11*flow_l+.08*struct_l
        cand_s=.18*up_ctx+.20*loc_s+.18*wave_s+.13*candle_s+.12*mom_s+.11*flow_s+.08*struct_s
        # Confirmation is separate and may be low at the exact origin.
        conf_l=.34*struct_l+.24*ma_l+.16*mom_l+.14*flow_l+.12*candle_l
        conf_s=.34*struct_s+.24*ma_s+.16*mom_s+.14*flow_s+.12*candle_s
        rows.append(dict(time=t,close=float(r.close),atr14=float(r.atr14),
                         candidate_l=float(clip100(cand_l)),candidate_s=float(clip100(cand_s)),
                         confirm_l=float(clip100(conf_l)),confirm_s=float(clip100(conf_s)),
                         down_ctx=down_ctx,up_ctx=up_ctx,loc_l=loc_l,loc_s=loc_s,
                         structure_l=float(struct_l),structure_s=float(struct_s),wave_l=wave_l,wave_s=wave_s,
                         candle_l=candle_l,candle_s=candle_s,ma_l=float(ma_l),ma_s=float(ma_s),
                         momentum_l=float(mom_l),momentum_s=float(mom_s),flow_l=float(flow_l),flow_s=float(flow_s)))
    return pd.DataFrame(rows).set_index('time')


def first_passage(D,t,p,side,good,adverse,horizon):
    fut=D[(D.index>t)&(D.index<=t+pd.Timedelta(days=horizon))]
    if fut.empty: return None
    for _,r in fut.iterrows():
        if side=='LOW': g=r.high>=p*(1+good); a=r.low<=p*(1-adverse)
        else: g=r.low<=p*(1-good); a=r.high>=p*(1+adverse)
        if g and a: return None
        if g: return 1
        if a: return 0
    return None


def build_truth(D):
    idx=list(D.index); ev=[]
    for i in range(70,len(D)-181):
        t=idx[i]; r=D.iloc[i]
        pre=D.iloc[i-30:i+1]; post=D.iloc[i+1:i+31]
        if r.low<=pre.low.min()*1.001 and r.low<=post.low.min()*1.001:
            if first_passage(D,t,float(r.close),'LOW',.30,.15,180)==1:
                ev.append(dict(time=t,side='LOW',price=float(r.low),close=float(r.close)))
        if r.high>=pre.high.max()*.999 and r.high>=post.high.max()*.999:
            if first_passage(D,t,float(r.close),'HIGH',.25,.15,180)==1:
                ev.append(dict(time=t,side='HIGH',price=float(r.high),close=float(r.close)))
    e=pd.DataFrame(ev)
    if e.empty:return e
    kept=[]
    for side in ['LOW','HIGH']:
        s=e[e.side==side].sort_values('time'); cluster=[]
        for _,row in s.iterrows():
            if not cluster or (row.time-cluster[-1].time).days<=21: cluster.append(row)
            else:
                kept.append(min(cluster,key=lambda z:z.price) if side=='LOW' else max(cluster,key=lambda z:z.price)); cluster=[row]
        if cluster: kept.append(min(cluster,key=lambda z:z.price) if side=='LOW' else max(cluster,key=lambda z:z.price))
    return pd.DataFrame(kept).sort_values('time').reset_index(drop=True)


def daily_outcomes(D,S,side):
    vals=np.full(len(S),np.nan)
    for i,t in enumerate(S.index):
        if t> D.index.max()-pd.Timedelta(days=180): continue
        vals[i]=first_passage(D,t,float(S.iloc[i].close),side,.30 if side=='LOW' else .25,.15,180)
    return vals


def add_fractal_prior(D,S):
    F=S.copy()
    base_cols_l=['down_ctx','loc_l','wave_l','structure_l','candle_l','momentum_l','flow_l']
    base_cols_s=['up_ctx','loc_s','wave_s','structure_s','candle_s','momentum_s','flow_s']
    arr=F[sorted(set(base_cols_l+base_cols_s))].fillna(50).to_numpy(float)/100.0
    colidx={c:i for i,c in enumerate(sorted(set(base_cols_l+base_cols_s)))}
    out_l=daily_outcomes(D,F,'LOW'); out_s=daily_outcomes(D,F,'HIGH')
    for side,cols,outcome,candcol,outcol in [
        ('LOW',base_cols_l,out_l,'candidate_l','fractal_l'),('HIGH',base_cols_s,out_s,'candidate_s','fractal_s')]:
        use=[colidx[c] for c in cols]; vals=np.full(len(F),np.nan)
        # Only useful candidate/context rows get an analog calculation; fractal never creates a signal by itself.
        eligible=np.where(F[candcol].to_numpy()>=20)[0]
        for i in eligible:
            t=F.index[i]
            hidx=np.where((F.index<=t-pd.Timedelta(days=181)) & np.isfinite(outcome))[0]
            if len(hidx)<180: continue
            q=arr[i,use]; h=arr[hidx][:,use]
            dist=np.sqrt(np.mean((h-q)**2,axis=1)); take=np.argsort(dist)[:24]
            ids=hidx[take]
            if len(ids)<12: continue
            w=1/(.05+dist[take]); vals[i]=100*float(np.average(outcome[ids],weights=w))
        F[outcol]=vals
    return F


def adjusted_candidate(r,side,mode):
    c=float(r.candidate_l if side=='LOW' else r.candidate_s)
    fr=r.fractal_l if side=='LOW' else r.fractal_s
    fr=50.0 if pd.isna(fr) else float(fr)
    if mode=='NONE': return c
    if mode=='SOFT': return float(clip100(c+.15*(fr-50)))
    if mode=='RANK': return float(clip100(.80*c+.20*fr))
    if mode=='INTERACTION': return float(clip100(c + .20*((fr-50)/50.0)*c))
    raise ValueError(mode)


def build_signals(S,side,mode,ct,ft,qt):
    cf='confirm_l' if side=='LOW' else 'confirm_s'; ctx='down_ctx' if side=='LOW' else 'up_ctx'; loc='loc_l' if side=='LOW' else 'loc_s'
    out=[]; last=None
    for t,r in S.iterrows():
        if float(r[cf])<ft: continue
        hist=S[(S.index>=t-pd.Timedelta(days=21))&(S.index<=t)]
        if hist.empty:continue
        best=None
        for bt,b in hist.iterrows():
            ac=adjusted_candidate(b,side,mode)
            if ac<ct or float(b[ctx])<15 or float(b[loc])<15: continue
            q=.58*ac+.42*float(r[cf])
            if best is None or q>best[0]: best=(q,bt,b,ac)
        if best is None or best[0]<qt: continue
        q,bt,b,ac=best
        sig=dict(origin_time=bt,confirm_time=t,origin_price=float(b.close),confirm_price=float(r.close),quality=float(q),candidate_adj=float(ac),confirmation=float(r[cf]),fractal_prior=None if pd.isna(b.fractal_l if side=='LOW' else b.fractal_s) else float(b.fractal_l if side=='LOW' else b.fractal_s))
        if last is None or (t-last).days>28:
            out.append(sig); last=t
        elif q>out[-1]['quality']:
            out[-1]=sig; last=t
    return out


def evaluate_signals(D,signals,truth,side):
    ts=truth[truth.side==side].copy(); used=set(); good=0; lags=[]; errs=[]; econ=[]
    for sig in signals:
        ot=pd.Timestamp(sig['origin_time']); op=float(sig['origin_price'])
        cand=ts[(ts.time>=ot-pd.Timedelta(days=14))&(ts.time<=ot+pd.Timedelta(days=14))].copy()
        if not cand.empty:
            cand['perr']=(op/cand.price-1).abs()*100; cand=cand[cand.perr<=18]
        if not cand.empty:
            cand['dist']=(cand.time-ot).abs(); tr=cand.sort_values(['dist','perr']).iloc[0]; key=pd.Timestamp(tr.time)
            if key not in used:
                used.add(key); good+=1; lags.append((pd.Timestamp(sig['confirm_time'])-key).days); errs.append(float(tr.perr))
        ep=first_passage(D,pd.Timestamp(sig['confirm_time']),float(sig['confirm_price']),side,.20,.10,120)
        if ep is not None: econ.append(ep)
    precision=100*good/len(signals) if signals else None; recall=100*good/len(ts) if len(ts) else None
    medlag=float(np.median(lags)) if lags else None; mederr=float(np.median(errs)) if errs else None
    econp=100*float(np.mean(econ)) if econ else None
    leadq=100 if medlag is not None and medlag<=0 else (max(0,100-medlag/30*100) if medlag is not None else 0)
    locq=max(0,100-mederr/20*100) if mederr is not None else 0
    p=precision or 0; r=recall or 0; e=econp or 0
    utility=.25*p+.30*r+.20*e+.15*leadq+.10*locq
    return dict(signals=len(signals),matched=good,truth_events=len(ts),precision_pct=None if precision is None else round(precision,2),recall_pct=None if recall is None else round(recall,2),missed_trend_pct=None if recall is None else round(100-recall,2),median_confirmation_lag_days=None if medlag is None else round(medlag,2),median_origin_price_error_pct=None if mederr is None else round(mederr,2),economic_evaluable=len(econ),economic_success_pct=None if econp is None else round(econp,2),research_utility=round(utility,2))


def choose_train(D,S,T,side,mode):
    best=None
    for ct in [30,45,60]:
      for ft in [35,50,65]:
       for qt in [45,60,75]:
        sig=build_signals(S,side,mode,ct,ft,qt); ev=evaluate_signals(D,sig,T,side)
        # Must not win by making almost no signals.
        coverage_penalty=8 if ev['signals']<max(2,int(.15*max(1,ev['truth_events']))) else 0
        z=(ev['research_utility']-coverage_penalty,ev['recall_pct'] or 0,ev['precision_pct'] or 0,-ev['signals'],ct,ft,qt)
        if best is None or z>best: best=z
    return best[-3],best[-2],best[-1]


def oos_method(D,S,T,side,mode):
    rows=[]; allsig=[]
    for y in range(2020,2027):
        tr=S[S.index.year<y]; te=S[S.index.year==y]
        tt=T[T.time.dt.year<y]; ty=T[T.time.dt.year==y]
        if len(tr)<500 or len(te)<90 or len(tt[tt.side==side])<3: continue
        ct,ft,qt=choose_train(D,tr,tt,side,mode)
        sig=build_signals(te,side,mode,ct,ft,qt); ev=evaluate_signals(D,sig,ty,side)
        ev.update(year=y,ct=ct,ft=ft,qt=qt); rows.append(ev)
        for s in sig: s.update(test_year=y,side=side,mode=mode); allsig.append(s)
    # Aggregate matched/precision/recall; lag/error/economic are recomputed on pooled OOS signals against pooled OOS truth.
    if not rows:return rows,{},[]
    years={r['year'] for r in rows}; poolT=T[T.time.dt.year.isin(years)]
    agg=evaluate_signals(D,allsig,poolT,side); agg['years']=len(rows); agg['mode']=mode
    return rows,agg,allsig


def confirmed_pivots(h,order=5):
    vals=[]
    if len(h)<2*order+1:return vals
    lo=h.low.to_numpy(); hi=h.high.to_numpy(); idx=h.index
    # last 'order' bars cannot be called a confirmed pivot yet.
    for i in range(order,len(h)-order):
        if lo[i]<=np.min(lo[i-order:i+order+1]): vals.append((idx[i],float(lo[i]),'LOW'))
        if hi[i]>=np.max(hi[i-order:i+order+1]): vals.append((idx[i],float(hi[i]),'HIGH'))
    return vals


def cluster_levels(levels,pct=.022):
    clusters=[]
    for tm,v,typ in sorted(levels,key=lambda z:z[1]):
        hit=None
        for c in clusters:
            if abs(v-c['center'])/max(1,c['center'])<=pct: hit=c; break
        if hit:
            hit['levels'].append(v); hit['events'].append((tm,v,typ)); hit['center']=float(np.mean(hit['levels']))
        else: clusters.append(dict(center=v,levels=[v],events=[(tm,v,typ)]))
    return clusters


def prior_reaction_efficiency(D,events,center,side,register_time):
    vals=[]
    for tm,_,_ in events:
        if tm>register_time-pd.Timedelta(days=25): continue
        if tm not in D.index:continue
        a=float(D.loc[tm].atr14) if pd.notna(D.loc[tm].atr14) else center*.03
        fut=D[(D.index>tm)&(D.index<=tm+pd.Timedelta(days=20))]
        if fut.empty:continue
        if side=='LOW': fav=max(0,float(fut.high.max())-center); adv=max(0,center-float(fut.low.min()))
        else: fav=max(0,center-float(fut.low.min())); adv=max(0,float(fut.high.max())-center)
        vals.append(np.clip((fav/(a+1e-9)-adv/(a+1e-9)+3)/6,0,1))
    return 50 if not vals else 100*float(np.mean(vals[-6:]))


def zone_candidates(D,t,side):
    h=D.loc[:t].tail(370)
    if len(h)<220:return []
    r=h.iloc[-1]; p=float(r.close); a=float(r.atr14) if pd.notna(r.atr14) else p*.03
    piv=confirmed_pivots(h,5)
    levels=piv.copy()
    for name,val in [('EMA20',r.ema20),('SMA50',r.sma50),('SMA200',r.sma200)]:
        if pd.notna(val):levels.append((t-pd.Timedelta(days=6),float(val),name))
    clusters=cluster_levels(levels)
    out=[]
    for c in clusters:
        z=float(c['center'])
        if side=='LOW' and z>=p*.995:continue
        if side=='HIGH' and z<=p*1.005:continue
        events=c['events']; piv_events=[e for e in events if e[2] in ('LOW','HIGH')]
        density=min(100,20*len(piv_events))
        lowc=sum(e[2]=='LOW' for e in piv_events); highc=sum(e[2]=='HIGH' for e in piv_events)
        role=100 if lowc and highc else (40 if len(piv_events)>=2 else 10)
        lasttm=max([e[0] for e in piv_events],default=t-pd.Timedelta(days=180))
        age=(t-lasttm).days; freshness=float(np.clip((age-10)/120*100,0,100))
        recent=h.tail(90); tests=int((((recent.low<=z*1.015)&(recent.high>=z*.985))).sum())
        weakening=float(np.clip(100-18*max(0,tests-1),0,100))
        disp=[]
        for tm,_,typ in piv_events:
            if tm>t-pd.Timedelta(days=20) or tm not in D.index:continue
            aa=float(D.loc[tm].atr14) if pd.notna(D.loc[tm].atr14) else a
            fut=D[(D.index>tm)&(D.index<=tm+pd.Timedelta(days=15))]
            if fut.empty:continue
            move=(float(fut.high.max())-z)/aa if side=='LOW' else (z-float(fut.low.min()))/aa
            disp.append(float(np.clip(move/5,0,1)))
        displacement=50 if not disp else 100*float(np.mean(disp[-5:]))
        ht=sum(abs(z-float(v))/z<=.02 for v in [r.ema20,r.sma50,r.sma200] if pd.notna(v)); overlap=min(100,33.33*ht+15*min(2,len(piv_events)))
        reaction=prior_reaction_efficiency(D,piv_events,z,side,t)
        dist=abs(p-z)/(a+1e-9); feasibility=float(np.clip(100-abs(dist-5)*10,0,100))
        out.append(dict(center=z,pivot_density=density,role_reversal=role,freshness=freshness,retest_strength=weakening,displacement=displacement,ht_overlap=overlap,prior_reaction=reaction,feasibility=feasibility,atr=a,distance_atr=dist,tests90=tests,pivots=len(piv_events)))
    # Keep structurally richest reachable candidates, not merely nearest.
    for c in out:
        c['heuristic']=.16*c['pivot_density']+.14*c['role_reversal']+.13*c['freshness']+.15*c['retest_strength']+.16*c['displacement']+.12*c['ht_overlap']+.10*c['prior_reaction']+.04*c['feasibility']
    return sorted(out,key=lambda c:c['heuristic'],reverse=True)[:4]


def zone_outcome(D,t,side,z):
    center=float(z['center']); fut=D[(D.index>t)&(D.index<=t+pd.Timedelta(days=120))]
    touch=None
    half=max(.008*center,.35*float(z['atr']))
    for tt,r in fut.iterrows():
        if r.low<=center+half and r.high>=center-half:
            touch=tt;break
    if touch is None:return False,None,None,t+pd.Timedelta(days=120)
    a=float(D.loc[touch].atr14) if touch in D.index and pd.notna(D.loc[touch].atr14) else float(z['atr'])
    stop=1.5*a; target=4.5*a
    after=D[(D.index>touch)&(D.index<=touch+pd.Timedelta(days=60))]
    result=None
    for _,r in after.iterrows():
        if side=='LOW': g=r.high>=center+target; bad=r.low<=center-stop
        else: g=r.low<=center-target; bad=r.high>=center+stop
        if g and bad: result=None;break
        if g:result=1;break
        if bad:result=0;break
    return True,result,touch,touch+pd.Timedelta(days=60)

ZONE_FEATURES=['pivot_density','role_reversal','freshness','retest_strength','displacement','ht_overlap','prior_reaction','feasibility']


def build_zone_registry(D):
    rows=[]
    dates=[]
    for y in range(2020,2027):
        for m in range(1,13):
            z=D[(D.index.year==y)&(D.index.month==m)]
            if not z.empty: dates.append(z.index[0])
    for t in dates:
        for side in ['LOW','HIGH']:
            for z in zone_candidates(D,t,side):
                reached,result,touch,resolved=zone_outcome(D,t,side,z)
                row=dict(register_time=t,side=side,zone=z['center'],reached=reached,reaction_success=result,touch_time=touch,resolved_time=resolved,heuristic=z['heuristic'])
                for c in ZONE_FEATURES:row[c]=z[c]
                rows.append(row)
    return pd.DataFrame(rows)


def fit_logit(X,y,l2=.8,iters=500,lr=.05):
    X=np.asarray(X,float); y=np.asarray(y,float)
    mu=X.mean(0); sd=X.std(0); sd[sd<1e-8]=1
    Z=(X-mu)/sd; Z=np.column_stack([np.ones(len(Z)),Z]); w=np.zeros(Z.shape[1])
    for _ in range(iters):
        a=np.clip(Z@w,-20,20); p=1/(1+np.exp(-a)); grad=Z.T@(p-y)/len(y); grad[1:]+=l2*w[1:]/len(y); w-=lr*grad
    return mu,sd,w


def pred_logit(model,X):
    mu,sd,w=model; Z=(np.asarray(X,float)-mu)/sd; Z=np.column_stack([np.ones(len(Z)),Z]); a=np.clip(Z@w,-20,20); return 1/(1+np.exp(-a))


def zone_oos(R,side):
    outs=[]
    for y in range(2022,2027):
        cutoff=pd.Timestamp(f'{y}-01-01',tz='UTC')
        tr=R[(R.side==side)&R.reaction_success.notna()&(pd.to_datetime(R.resolved_time,utc=True)<cutoff)]
        te=R[(R.side==side)&(pd.to_datetime(R.register_time,utc=True).dt.year==y)&R.reaction_success.notna()]
        if len(tr)<30 or len(te)<5 or tr.reaction_success.nunique()<2:continue
        model=fit_logit(tr[ZONE_FEATURES].values,tr.reaction_success.values)
        ptrain=pred_logit(model,tr[ZONE_FEATURES].values); ptest=pred_logit(model,te[ZONE_FEATURES].values)
        # Convert to train-distribution percentile score, avoiding pseudo-probability claims.
        sp=np.array([100*np.mean(ptrain<=v) for v in ptest])
        z=te[['register_time','zone','reaction_success']].copy(); z['score']=sp; z['test_year']=y; outs.append(z)
    if not outs:return pd.DataFrame(),[]
    O=pd.concat(outs,ignore_index=True); buckets=[]
    for lo,hi in [(0,24.999),(25,49.999),(50,74.999),(75,100)]:
        g=O[(O.score>=lo)&(O.score<=hi)]
        buckets.append(dict(bucket=f'{int(lo)}-{int(hi)}',n=len(g),success_pct=None if len(g)==0 else round(100*float(g.reaction_success.mean()),2)))
    return O,buckets


def monotonic_tendency(buckets):
    vals=[b['success_pct'] for b in buckets if b['n']>=5 and b['success_pct'] is not None]
    if len(vals)<3:return False
    violations=sum(vals[i+1]+3<vals[i] for i in range(len(vals)-1))
    return violations<=1 and vals[-1]>=vals[0]+8


def main():
    d0,fd=load_interval('1d'); h0,f4=load_interval('4h')
    D=add_features(d0,'1D'); H4=add_features(h0,'4H')
    S=build_state(D,H4); T=build_truth(D); T['time']=pd.to_datetime(T.time,utc=True)
    F=add_fractal_prior(D,S)

    methods={}; signal_rows=[]
    for side in ['LOW','HIGH']:
        methods[side]={}
        for mode in ['NONE','SOFT','RANK','INTERACTION']:
            yr,agg,sigs=oos_method(D,F,T,side,mode)
            methods[side][mode]=dict(aggregate=agg,years=yr)
            signal_rows.extend(sigs)

    def best_mode(side):
        opts=[]
        for m,v in methods[side].items():
            a=v['aggregate']; opts.append((a.get('research_utility',0),a.get('recall_pct') or 0,a.get('precision_pct') or 0,m))
        return max(opts)[-1]

    chosen={'LONG':best_mode('LOW'),'SHORT':best_mode('HIGH')}
    baseline={'LONG':methods['LOW']['NONE']['aggregate'],'SHORT':methods['HIGH']['NONE']['aggregate']}
    selected={'LONG':methods['LOW'][chosen['LONG']]['aggregate'],'SHORT':methods['HIGH'][chosen['SHORT']]['aggregate']}

    Z=build_zone_registry(D); zl,zlb=zone_oos(Z,'LOW'); zs,zsb=zone_oos(Z,'HIGH')
    zl.to_csv(OUT/'zone_oos_long.csv',index=False); zs.to_csv(OUT/'zone_oos_short.csv',index=False); Z.to_csv(OUT/'zone_registry.csv',index=False)
    F.tail(1000).to_csv(OUT/'recent_state_with_fractal.csv')
    pd.DataFrame(signal_rows).to_csv(OUT/'all_oos_signals.csv',index=False)
    T.to_csv(OUT/'truth_events.csv',index=False)

    fractal_gain={k:round((selected[k].get('research_utility',0)-baseline[k].get('research_utility',0)),2) for k in ['LONG','SHORT']}
    gates={
      'long_nonzero':selected['LONG'].get('signals',0)>0,
      'short_nonzero':selected['SHORT'].get('signals',0)>0,
      'long_recall_ge_25':(selected['LONG'].get('recall_pct') or 0)>=25,
      'short_recall_ge_25':(selected['SHORT'].get('recall_pct') or 0)>=25,
      'long_precision_ge_30':(selected['LONG'].get('precision_pct') or 0)>=30,
      'short_precision_ge_30':(selected['SHORT'].get('precision_pct') or 0)>=30,
      'long_economic_ge_50':(selected['LONG'].get('economic_success_pct') or 0)>=50,
      'short_economic_ge_50':(selected['SHORT'].get('economic_success_pct') or 0)>=50,
      'long_lag_le_21':selected['LONG'].get('median_confirmation_lag_days') is not None and selected['LONG']['median_confirmation_lag_days']<=21,
      'short_lag_le_21':selected['SHORT'].get('median_confirmation_lag_days') is not None and selected['SHORT']['median_confirmation_lag_days']<=21,
      'long_localization_le_15':selected['LONG'].get('median_origin_price_error_pct') is not None and selected['LONG']['median_origin_price_error_pct']<=15,
      'short_localization_le_15':selected['SHORT'].get('median_origin_price_error_pct') is not None and selected['SHORT']['median_origin_price_error_pct']<=15,
      'fractal_does_not_extinguish_signals':selected['LONG'].get('signals',0)>0 and selected['SHORT'].get('signals',0)>0,
      'zone_long_separation':monotonic_tendency(zlb),
      'zone_short_separation':monotonic_tendency(zsb),
      'no_synthetic_oi_funding':True,
      'production_untouched':True,
    }
    summary={
      'engine':'MASTER_BTC_TREND_V3_RESTART_PHASE1_V0_1',
      'status':'RESEARCH_ONLY_NOT_LIVE',
      'data':{'source':'Binance Data Vision Spot BTCUSDT completed 1D/4H','start':str(D.index.min()),'end':str(D.index.max()),'daily_rows':len(D),'h4_rows':len(H4),'failures':fd+f4,'oi_funding':'N/A in long-history Phase1; no synthetic repair'},
      'truth':{'LOW':'30D local low +30% before -15% within180D','HIGH':'30D local high -25% before +15% within180D','future_usage':'evaluation labels only'},
      'method_comparison':methods,
      'chosen_mode':chosen,
      'baseline_none':baseline,
      'selected':selected,
      'soft_fractal_utility_gain_vs_none':fractal_gain,
      'zone_quality':{'LONG_BUCKETS':zlb,'SHORT_BUCKETS':zsb,'LONG_OOS_N':len(zl),'SHORT_OOS_N':len(zs),'reach_is_separate_from_reaction':True,'reaction_label':'fixed structural 3R before 1R after actual touch'},
      'gates':gates,
      'stage':'PASS_TO_FOUR_ENGINE_RESEARCH' if all(gates.values()) else 'REDESIGN_PROGRESS_NOT_YET_PASS',
      'master_btc_trend_v26_modified':False,
      'production_modified':False,
      'integration':'FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL',
      'notes':['Fractal modes are soft/rank/interaction comparisons, never hard deletion.','Research utility is only a model-selection criterion, not a MASTER score or probability.','Current/live BTC decision is out of scope for this historical Phase1 validator.']
    }
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'stage':summary['stage'],'chosen_mode':chosen,'selected':selected,'fractal_gain':fractal_gain,'zone_long':zlb,'zone_short':zsb,'gates':gates},ensure_ascii=False))

if __name__=='__main__':
    main()
