from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

START=pd.Timestamp('2017-09-01T00:00:00Z')
END=pd.Timestamp('2026-09-01T00:00:00Z')
OUT=Path('btc_trend_v30/output/restart_phase1_v1'); OUT.mkdir(parents=True,exist_ok=True)
COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_buy_base','taker_buy_quote','ignore']
BASE='https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/{interval}/BTCUSDT-{interval}-{ym}.zip'
EPS=1e-12


def months():
    cur=pd.Timestamp(START.year,START.month,1,tz='UTC'); last=pd.Timestamp(END.year,END.month,1,tz='UTC')
    while cur<last:
        yield cur.strftime('%Y-%m'); cur=cur+pd.offsets.MonthBegin(1)


def load_interval(interval):
    frames=[]; fail=[]; s=requests.Session(); s.headers.update({'User-Agent':'master-btc-trend-v30-restart/1.0'})
    for ym in months():
        try:
            r=s.get(BASE.format(interval=interval,ym=ym),timeout=45)
            if r.status_code!=200: fail.append(f'{interval}:{ym}:HTTP{r.status_code}'); continue
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                ns=[n for n in z.namelist() if n.endswith('.csv')]
                if not ns: fail.append(f'{interval}:{ym}:no_csv'); continue
                frames.append(pd.read_csv(z.open(ns[0]),header=None,names=COLS))
        except Exception as e: fail.append(f'{interval}:{ym}:{type(e).__name__}')
    if not frames: raise RuntimeError(f'No Binance data {interval}')
    x=pd.concat(frames,ignore_index=True)
    v=pd.to_numeric(x.open_time,errors='coerce').astype(float); v=np.where(v>1e14,v/1000,v)
    x['time']=pd.to_datetime(v,unit='ms',utc=True)
    for c in ['open','high','low','close','volume','quote_volume','taker_buy_quote']: x[c]=pd.to_numeric(x[c],errors='coerce')
    x=x[['time','open','high','low','close','volume','quote_volume','taker_buy_quote']].dropna().drop_duplicates('time').sort_values('time')
    x=x[(x.time>=START)&(x.time<END)].set_index('time'); x['net_taker_quote']=2*x.taker_buy_quote-x.quote_volume
    return x,fail


def rsi(s,n=14):
    d=s.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0)
    au=up.ewm(alpha=1/n,adjust=False,min_periods=n).mean(); ad=dn.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    return (100-100/(1+au/ad.replace(0,np.nan))).fillna(50)


def atr(x,n=14):
    pc=x.close.shift(1); tr=pd.concat([(x.high-x.low).abs(),(x.high-pc).abs(),(x.low-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean()


def kdj(x,n=9):
    lo=x.low.rolling(n).min(); hi=x.high.rolling(n).max(); rsv=100*(x.close-lo)/(hi-lo).replace(0,np.nan)
    k=rsv.ewm(alpha=1/3,adjust=False).mean().fillna(50); d=k.ewm(alpha=1/3,adjust=False).mean().fillna(50); return k,d,3*k-2*d


def enrich(x,tf):
    x=x.copy(); x['ema20']=x.close.ewm(span=20,adjust=False).mean(); x['ema60']=x.close.ewm(span=60,adjust=False).mean()
    x['sma50']=x.close.rolling(50).mean(); x['sma200']=x.close.rolling(200).mean(); x['atr14']=atr(x); x['rsi14']=rsi(x); x['k'],x['d'],x['j']=kdj(x)
    m=x.volume.rolling(40).mean(); sd=x.volume.rolling(40).std(ddof=0); x['vol_z']=((x.volume-m)/sd.replace(0,np.nan)).fillna(0)
    x['range']=(x.high-x.low).replace(0,np.nan); x['body']=x.close-x.open; x['body_ratio']=x.body.abs()/x['range']; x['close_loc']=(x.close-x.low)/x['range']
    x['uw']=(x.high-np.maximum(x.open,x.close))/x['range']; x['lw']=(np.minimum(x.open,x.close)-x.low)/x['range']
    x['taker_imb_3']=x.net_taker_quote.rolling(3).sum()/x.quote_volume.rolling(3).sum().replace(0,np.nan); x['taker_imb_6']=x.net_taker_quote.rolling(6).sum()/x.quote_volume.rolling(6).sum().replace(0,np.nan)
    for n in [3,5,10,20,30,60]: x[f'ret{n}']=x.close.pct_change(n)
    for n in [10,20,60,90,180]:
        x[f'high{n}_prev']=x.high.shift(1).rolling(n if tf=='1D' else max(12,int(n*1.5))).max(); x[f'low{n}_prev']=x.low.shift(1).rolling(n if tf=='1D' else max(12,int(n*1.5))).min()
    x['ema20_slope5']=x.ema20/x.ema20.shift(5)-1; x['sma50_slope10']=x.sma50/x.sma50.shift(10)-1; x['rsi_delta5']=x.rsi14-x.rsi14.shift(5); x['j_delta3']=x.j-x.j.shift(3)
    x['rsi_min20']=x.rsi14.shift(1).rolling(20).min(); x['rsi_max20']=x.rsi14.shift(1).rolling(20).max()
    x['candle_bull']=(35*x.body_ratio.clip(0,1)*(x.body>0)+25*x.close_loc.clip(0,1)+15*x.lw.clip(0,1)+15*((x.vol_z.clip(-1,3)+1)/4).clip(0,1)+10*(x.close>x.high.shift(1))).clip(0,100)
    x['candle_bear']=(35*x.body_ratio.clip(0,1)*(x.body<0)+25*(1-x.close_loc.clip(0,1))+15*x.uw.clip(0,1)+15*((x.vol_z.clip(-1,3)+1)/4).clip(0,1)+10*(x.close<x.low.shift(1))).clip(0,100)
    h=5 if tf=='1D' else 12; rr=x.close/x.close.shift(h)-1; pr=x.close.shift(h)/x.close.shift(2*h)-1
    rf=x.net_taker_quote.rolling(h).sum()/x.quote_volume.rolling(h).sum().replace(0,np.nan); pf=x.net_taker_quote.shift(h).rolling(h).sum()/x.quote_volume.shift(h).rolling(h).sum().replace(0,np.nan)
    x['down_exhaust']=(45*((-pr).clip(0,.20)/.20).clip(0,1)+30*((rr-pr).clip(0,.12)/.12).clip(0,1)+25*((pf-rf).clip(-.20,.20)+.20)/.40).clip(0,100)
    x['up_exhaust']=(45*(pr.clip(0,.20)/.20).clip(0,1)+30*((pr-rr).clip(0,.12)/.12).clip(0,1)+25*((rf-pf).clip(-.20,.20)+.20)/.40).clip(0,100)
    x['sell_absorb']=(50*((-x.taker_imb_3).clip(0,.25)/.25).clip(0,1)+30*x.close_loc.clip(0,1)+20*((x.ret3+.06).clip(0,.06)/.06).clip(0,1)).clip(0,100)
    x['buy_exhaust']=(50*(x.taker_imb_3.clip(0,.25)/.25).clip(0,1)+30*(1-x.close_loc.clip(0,1))+20*((.06-x.ret3).clip(0,.06)/.06).clip(0,1)).clip(0,100)
    return x


def h4_daily(h):
    y=h.copy(); y['day']=y.index.floor('D'); cols=['close','ema20','sma50','rsi14','j','vol_z','close_loc','taker_imb_3','taker_imb_6','ret3','ema20_slope5','rsi_delta5','j_delta3','candle_bull','candle_bear','down_exhaust','up_exhaust','sell_absorb','buy_exhaust']
    g=y.groupby('day')[cols].last(); g.columns=['h4_'+c for c in cols]; return g


def chart_state(d,h4):
    x=d.copy().join(h4_daily(h4),how='left'); dd=1-x.close/x.high180_prev; rr=x.close/x.low180_prev-1
    x['down_context']=np.maximum(((dd-.08)/.32*100).clip(0,100),((-x.ret60-.04)/.32*100).clip(0,100)); x['up_context']=np.maximum(((rr-.10)/.65*100).clip(0,100),((x.ret60-.06)/.45*100).clip(0,100))
    x['near_low']=((.22-np.maximum(0,x.close/x.low90_prev-1))/.22*100).clip(0,100); x['near_high']=((.22-np.maximum(0,1-x.close/x.high90_prev))/.22*100).clip(0,100)
    fl=((x.low<x.low20_prev)&(x.close>x.low20_prev)); fh=((x.high>x.high20_prev)&(x.close<x.high20_prev))
    x['structure_l']=(30*fl+25*(x.close>x.high10_prev)+15*(x.close>x.ema20)+15*(x.ret3>0)+15*((x.h4_close>x.h4_ema20)&(x.h4_ret3>0))).astype(float).clip(0,100)
    x['structure_s']=(30*fh+25*(x.close<x.low10_prev)+15*(x.close<x.ema20)+15*(x.ret3<0)+15*((x.h4_close<x.h4_ema20)&(x.h4_ret3<0))).astype(float).clip(0,100)
    x['wave_l']=(.60*x.down_exhaust+.40*x.h4_down_exhaust).clip(0,100); x['wave_s']=(.60*x.up_exhaust+.40*x.h4_up_exhaust).clip(0,100)
    x['candle_l']=(.45*x.candle_bull+.55*x.h4_candle_bull).clip(0,100); x['candle_s']=(.45*x.candle_bear+.55*x.h4_candle_bear).clip(0,100)
    comp=(abs(x.ema20-x.sma50)/x.close).clip(0,.08); cs=1-comp/.08
    x['ma_l']=(25*(x.ema20_slope5>0)+20*(x.close>x.ema20)+15*(x.h4_ema20_slope5>0)+15*(x.h4_close>x.h4_ema20)+10*(x.sma50_slope10>-.01)+15*cs).astype(float).clip(0,100)
    x['ma_s']=(25*(x.ema20_slope5<0)+20*(x.close<x.ema20)+15*(x.h4_ema20_slope5<0)+15*(x.h4_close<x.h4_ema20)+10*(x.sma50_slope10<.01)+15*cs).astype(float).clip(0,100)
    dl=((x.close<=x.low20_prev*1.05)&(x.rsi14>x.rsi_min20+5)); ds=((x.close>=x.high20_prev*.95)&(x.rsi14<x.rsi_max20-5))
    x['mom_l']=(35*dl+20*(x.rsi_delta5>0)+15*(x.rsi14>40)+15*(x.j_delta3>0)+15*((x.h4_rsi_delta5>0)&(x.h4_j_delta3>0))).astype(float).clip(0,100)
    x['mom_s']=(35*ds+20*(x.rsi_delta5<0)+15*(x.rsi14<60)+15*(x.j_delta3<0)+15*((x.h4_rsi_delta5<0)&(x.h4_j_delta3<0))).astype(float).clip(0,100)
    x['flow_l']=(.35*x.sell_absorb+.20*x.h4_sell_absorb+20*((x.vol_z>0)&(x.close_loc>.55))+15*(x.taker_imb_3>x.taker_imb_6)+10*(x.h4_taker_imb_3>x.h4_taker_imb_6)).astype(float).clip(0,100)
    x['flow_s']=(.35*x.buy_exhaust+.20*x.h4_buy_exhaust+20*((x.vol_z>0)&(x.close_loc<.45))+15*(x.taker_imb_3<x.taker_imb_6)+10*(x.h4_taker_imb_3<x.h4_taker_imb_6)).astype(float).clip(0,100)
    x['base_l']=(.18*x.down_context+.15*x.near_low+.18*x.structure_l+.18*x.wave_l+.09*x.candle_l+.08*x.ma_l+.07*x.mom_l+.07*x.flow_l).clip(0,100)
    x['base_s']=(.18*x.up_context+.15*x.near_high+.18*x.structure_s+.18*x.wave_s+.09*x.candle_s+.08*x.ma_s+.07*x.mom_s+.07*x.flow_s).clip(0,100)
    x['confirm_l']=(.35*x.structure_l+.25*x.ma_l+.15*x.mom_l+.15*x.flow_l+10*((x.ret5>0)&(x.h4_ret3>0))).astype(float).clip(0,100)
    x['confirm_s']=(.35*x.structure_s+.25*x.ma_s+.15*x.mom_s+.15*x.flow_s+10*((x.ret5<0)&(x.h4_ret3<0))).astype(float).clip(0,100)
    return x


def first_passage(d,t,side,h=180):
    if t not in d.index: return np.nan
    p=float(d.loc[t].close); fut=d[(d.index>t)&(d.index<=t+pd.Timedelta(days=h))]
    for _,r in fut.iterrows():
        good=(r.high>=p*1.30) if side=='LONG' else (r.low<=p*.75); bad=(r.low<=p*.85) if side=='LONG' else (r.high>=p*1.15)
        if good and bad: return np.nan
        if good:return 1.;
        if bad:return 0.
    return np.nan


def outcomes(d,side): return pd.Series([first_passage(d,t,side) for t in d.index],index=d.index)


def truth_events(d):
    rows=[]
    for i in range(10,len(d)-181):
        t=d.index[i]
        if d.low.iloc[i]<=d.low.iloc[i-10:i+11].min()*(1+1e-10) and first_passage(d,t,'LONG')==1:
            peak=d.high.iloc[i+1:i+181].max(); rows.append({'time':t,'side':'LONG','price':float(d.low.iloc[i]),'strength':float(peak/d.low.iloc[i]-1)})
        if d.high.iloc[i]>=d.high.iloc[i-10:i+11].max()*(1-1e-10) and first_passage(d,t,'SHORT')==1:
            trough=d.low.iloc[i+1:i+181].min(); rows.append({'time':t,'side':'SHORT','price':float(d.high.iloc[i]),'strength':float(1-trough/d.high.iloc[i])})
    z=pd.DataFrame(rows).sort_values('time'); keep=[]
    for side in ['LONG','SHORT']:
        g=z[z.side==side]; grp=[]
        for _,r in g.iterrows():
            if not grp or (r.time-grp[-1]['time']).days>45: grp.append(r.to_dict())
            elif r.strength>grp[-1]['strength']: grp[-1]=r.to_dict()
        keep+=grp
    return pd.DataFrame(keep).sort_values('time').reset_index(drop=True)


def fractal_prior(x,out,side):
    fs=['down_context','near_low','structure_l','wave_l','candle_l','ma_l','mom_l','flow_l'] if side=='LONG' else ['up_context','near_high','structure_s','wave_s','candle_s','ma_s','mom_s','flow_s']
    a=x[fs].astype(float).fillna(50).values/100; oo=out.reindex(x.index); vals=np.full(len(x),np.nan)
    for i,t in enumerate(x.index):
        hist=np.where((x.index<=t-pd.Timedelta(days=181))&oo.notna().values)[0]
        if len(hist)<180:continue
        dist=np.sqrt(np.mean((a[hist]-a[i])**2,axis=1)); order=np.argsort(dist); chosen=[]
        for k in order:
            j=hist[k]
            if all(abs((x.index[j]-x.index[c]).days)>30 for c in chosen):chosen.append(j)
            if len(chosen)>=24:break
        if len(chosen)<10:continue
        ch=np.array(chosen); dd=np.sqrt(np.mean((a[ch]-a[i])**2,axis=1)); w=1/(.05+dd); vals[i]=100*np.average(oo.values[ch],weights=w)
    return vals


def pct(v,a):
    a=np.asarray(a,float); a=a[np.isfinite(a)]
    if not np.isfinite(v) or len(a)<30:return np.nan
    return 100*np.searchsorted(np.sort(a),v,side='right')/len(a)


def mode_score(frame,side,mode,train_fr):
    b=frame['base_l' if side=='LONG' else 'base_s'].astype(float); fr=frame['fr_l' if side=='LONG' else 'fr_s']; st=frame['structure_l' if side=='LONG' else 'structure_s']; wa=frame['wave_l' if side=='LONG' else 'wave_s']; out=[]
    train_fr=np.asarray(train_fr,float); train_fr=train_fr[np.isfinite(train_fr)]
    for bb,ff,ss,ww in zip(b,fr,st,wa):
        if mode=='NO_FRACTAL' or pd.isna(ff):z=bb
        elif mode=='SOFT_ADD':z=bb+np.clip((ff-50)*.16,-8,8)
        elif mode=='RANK_BOOST':rp=pct(ff,train_fr); z=bb if pd.isna(rp) else bb+np.clip((rp-50)*.12,-6,6)
        elif mode=='INTERACTION':
            agree=(ss+ww)/2; z=bb+np.clip((ff-50)*.16,-8,8) if agree>=60 else (bb-np.clip((40-ff)*.12,0,5) if ff<40 else bb)
        out.append(float(np.clip(z,0,100)))
    return np.array(out)


def select(frame,score,side,th):
    ctx=frame['down_context' if side=='LONG' else 'up_context']; loc=frame['near_low' if side=='LONG' else 'near_high']; conf=frame['confirm_l' if side=='LONG' else 'confirm_s']; rows=[]
    for (t,r),sc,cc,ll,co in zip(frame.iterrows(),score,ctx,loc,conf):
        if not np.isfinite(sc) or sc<th or cc<15 or ll<10:continue
        z={'time':t,'price':float(r.close),'score':float(sc),'confirmation':float(co)}
        if not rows or (t-rows[-1]['time']).days>28:rows.append(z)
        elif sc>rows[-1]['score']:rows[-1]=z
    return rows


def path_metrics(d,sigs,side):
    u=[]; tf=[]; fl=[]
    for s in sigs:
        t=s['time']; p=s['price']; fut=d[(d.index>t)&(d.index<=t+pd.Timedelta(days=180))]
        if fut.empty:continue
        if side=='LONG':mfe=fut.high.max()/p-1; adv=max(0,1-fut.low.min()/p)
        else:mfe=1-fut.low.min()/p; adv=max(0,fut.high.max()/p-1)
        u.append(100*(min(mfe,.8)-.65*min(adv,.4))); o=first_passage(d,t,side)
        if np.isfinite(o):tf.append(o)
        if o==0:fl.append(min(adv,.2)*100)
    return {'avg_path_utility':round(float(np.mean(u)),2) if u else None,'target_first_pct':round(100*float(np.mean(tf)),2) if tf else None,'false_start_loss_pct':round(float(np.mean(fl)),2) if fl else 0.,'paths':len(u)}


def evaluate(d,sigs,truth,side):
    ts=truth[truth.side==side]; mt=set(); ms=set(); lag=[]; pe=[]; cap=[]
    for i,s in enumerate(sigs):
        t=s['time']; p=s['price']; g=ts[(ts.time>=t-pd.Timedelta(days=14))&(ts.time<=t+pd.Timedelta(days=14))].copy()
        if g.empty:continue
        g['pe']=(p/g.price-1).abs()*100; g=g[g.pe<=18]
        if g.empty:continue
        g['dt']=(g.time-t).abs(); r=g.sort_values(['dt','pe','strength'],ascending=[True,True,False]).iloc[0]; key=pd.Timestamp(r.time)
        if key in mt:continue
        mt.add(key);ms.add(i);lag.append((t-key).days);pe.append(float(r.pe)); full=float(r.strength)
        target=float(r.price)*(1+full) if side=='LONG' else float(r.price)*(1-full); c=max(0,target/p-1)/full if side=='LONG' else max(0,1-target/p)/full;cap.append(float(np.clip(c,0,1)))
    covered=set()
    for _,r in ts.iterrows():
        if any(-10<=(s['time']-r.time).days<=30 for s in sigs):covered.add(pd.Timestamp(r.time))
    pm=path_metrics(d,sigs,side)
    return {'signals':len(sigs),'matched':len(ms),'truth_events':len(ts),'precision_pct':round(100*len(ms)/len(sigs),2) if sigs else None,'recall_pct':round(100*len(mt)/len(ts),2) if len(ts) else None,'missed_trend_rate_pct':round(100*(1-len(covered)/len(ts)),2) if len(ts) else None,'move_capture_ratio_pct':round(100*np.mean(cap),2) if cap else None,'median_origin_lag_days':round(float(np.median(lag)),2) if lag else None,'median_localization_error_pct':round(float(np.median(pe)),2) if pe else None,**pm}


def objective(e):
    cap=e.get('move_capture_ratio_pct') or 0; miss=e.get('missed_trend_rate_pct'); miss=100 if miss is None else miss; u=e.get('avg_path_utility') or 0; p=e.get('precision_pct') or 0
    return .35*cap+.35*(100-miss)+.20*np.clip(u,0,100)+.10*p


def oos(d,x,truth,side):
    modes=['NO_FRACTAL','SOFT_ADD','RANK_BOOST','INTERACTION']; rows={m:[] for m in modes}
    frcol='fr_l' if side=='LONG' else 'fr_s'
    for y in range(2020,2027):
        tr=x[x.index.year<y].copy(); te=x[x.index.year==y].copy(); tt=truth[truth.time.dt.year<y]; ty=truth[truth.time.dt.year==y]
        if len(tr)<600 or len(te)<100 or len(tt[tt.side==side])<3:continue
        for m in modes:
            trs=mode_score(tr,side,m,tr[frcol].dropna().values); tes=mode_score(te,side,m,tr[frcol].dropna().values); best=None
            for q in [72,78,84,90]:
                th=np.nanpercentile(trs,q); e=evaluate(d,select(tr,trs,side,th),tt,side); z=(objective(e),-abs(q-78),q)
                if best is None or z>best:best=z
            q=best[2];th=np.nanpercentile(trs,q);e=evaluate(d,select(te,tes,side,th),ty,side);e.update({'year':y,'mode':m,'train_quantile':q,'threshold':round(float(th),3)});rows[m].append(e)
    return rows


def aggregate(rows):
    if not rows:return{}
    sig=sum(r['signals'] for r in rows);mat=sum(r['matched'] for r in rows);tru=sum(r['truth_events'] for r in rows)
    def wa(k,w):
        z=[(r[k],r[w]) for r in rows if r.get(k) is not None and (r.get(w) or 0)>0];return round(sum(a*b for a,b in z)/sum(b for _,b in z),2) if z else None
    return {'signals':sig,'matched':mat,'truth_events':tru,'precision_pct':round(100*mat/sig,2) if sig else None,'recall_pct':round(100*mat/tru,2) if (truq:=tru) else None,'missed_trend_rate_pct':wa('missed_trend_rate_pct','truth_events'),'move_capture_ratio_pct':wa('move_capture_ratio_pct','matched'),'avg_path_utility':wa('avg_path_utility','paths'),'false_start_loss_pct':wa('false_start_loss_pct','signals'),'target_first_pct':wa('target_first_pct','signals'),'years':len(rows)}


def main():
    d0,fd=load_interval('1d');h0,f4=load_interval('4h');d=enrich(d0,'1D');h=enrich(h0,'4H');x=chart_state(d,h).dropna(subset=['sma200','base_l','base_s']).copy();truth=truth_events(d);truth['time']=pd.to_datetime(truth.time,utc=True)
    x['fr_l']=fractal_prior(x,outcomes(d,'LONG'),'LONG');x['fr_s']=fractal_prior(x,outcomes(d,'SHORT'),'SHORT')
    result={};best={}
    for side in ['LONG','SHORT']:
        r=oos(d,x,truth,side);a={m:aggregate(v) for m,v in r.items()}
        for m in a:a[m]['utility_objective']=round(objective(a[m]),2)
        b=max(a,key=lambda m:a[m].get('utility_objective') or -999);best[side]=b;result[side]={'aggregate':a,'years':r,'best_mode':b}
    old={'LONG_precision':10.34,'LONG_recall':20.0,'SHORT_precision':20.59,'SHORT_recall':41.18}
    summary={'engine':'MASTER_BTC_TREND_V3_RESTART_PHASE1_V1','status':'RESEARCH_ONLY_NOT_LIVE','data_failures':fd+f4,'truth_events':{'LONG':int((truth.side=='LONG').sum()),'SHORT':int((truth.side=='SHORT').sum())},'old_phase14_reference':old,'fractal_mode_comparison':result,'best_modes':best,'anti_leakage':['PIT chart features only','fractal analog outcomes fully knowable <= query-181D','walk-forward transforms use prior years only'],'master_btc_trend_v26_modified':False,'production_modified':False,'integration':'FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL'}
    l=result['LONG']['aggregate'][best['LONG']];s=result['SHORT']['aggregate'][best['SHORT']];summary['phase1_candidate_gate']=bool((l.get('avg_path_utility') or -1)>0 and (s.get('avg_path_utility') or -1)>0 and (l.get('missed_trend_rate_pct') or 100)<80 and (s.get('missed_trend_rate_pct') or 100)<80);summary['stage']='CANDIDATE_ARCHITECTURE_PROMISING' if summary['phase1_candidate_gate'] else 'RESEARCH_FAIL_REDESIGN'
    truth.to_csv(OUT/'truth_major_origins.csv',index=False);x.tail(900).to_csv(OUT/'recent_state.csv');(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'engine':summary['engine'],'truth':summary['truth_events'],'best_modes':best,'LONG':l,'SHORT':s,'stage':summary['stage'],'data_failures':summary['data_failures'],'master_modified':False},ensure_ascii=False,default=str))

if __name__=='__main__':main()
