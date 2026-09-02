from __future__ import annotations

import io, json, math, os, zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

START = pd.Timestamp('2023-09-01T00:00:00Z')
END = pd.Timestamp('2026-09-01T00:00:00Z')
OUTDIR = Path('btc_backtest/output')
OUTDIR.mkdir(parents=True, exist_ok=True)
BASE = 'https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-{ym}.zip'
COLS = ['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_buy_base','taker_buy_quote','ignore']


def months(start, end):
    cur = pd.Timestamp(start.year, start.month, 1, tz='UTC')
    last = pd.Timestamp(end.year, end.month, 1, tz='UTC')
    while cur < last:
        yield cur.strftime('%Y-%m')
        cur = cur + pd.offsets.MonthBegin(1)


def load_data():
    frames, failures = [], []
    s = requests.Session(); s.headers.update({'User-Agent':'master-btc-trend-backtest/1.0'})
    for ym in months(START, END):
        try:
            r = s.get(BASE.format(ym=ym), timeout=45)
            if r.status_code != 200:
                failures.append(f'{ym}:HTTP{r.status_code}'); continue
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                names=[n for n in z.namelist() if n.endswith('.csv')]
                if not names: failures.append(f'{ym}:no_csv'); continue
                raw=pd.read_csv(z.open(names[0]),header=None,names=COLS)
            frames.append(raw)
        except Exception as e:
            failures.append(f'{ym}:{type(e).__name__}')
    if not frames: raise RuntimeError('No Binance monthly data downloaded')
    df=pd.concat(frames,ignore_index=True)
    ts=pd.to_numeric(df.open_time,errors='coerce'); ts=np.where(ts>1e14,ts/1000.0,ts)
    df['time']=pd.to_datetime(ts,unit='ms',utc=True)
    for c in ['open','high','low','close','volume','quote_volume','taker_buy_quote']:
        df[c]=pd.to_numeric(df[c],errors='coerce')
    df=df[['time','open','high','low','close','volume','quote_volume','taker_buy_quote']].dropna().drop_duplicates('time').sort_values('time')
    return df[(df.time>=START)&(df.time<END)].set_index('time'),failures


def rsi(s,n=14):
    d=s.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0)
    au=up.ewm(alpha=1/n,adjust=False,min_periods=n).mean(); ad=dn.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    return (100-100/(1+au/ad.replace(0,np.nan))).fillna(50)


def atr(df,n=14):
    pc=df.close.shift(1)
    tr=pd.concat([(df.high-df.low).abs(),(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean()


def enrich_hourly(h):
    h=h.copy()
    for n in [1,3,6,12,24]: h[f'ret{n}']=h.close.pct_change(n)
    h['rsi14']=rsi(h.close); h['ema20']=h.close.ewm(span=20,adjust=False).mean(); h['atr14']=atr(h)
    vm=h.volume.rolling(20).mean(); vs=h.volume.rolling(20).std(ddof=0); h['vol_z']=(h.volume-vm)/vs.replace(0,np.nan)
    h['range']=(h.high-h.low).replace(0,np.nan); h['close_loc']=(h.close-h.low)/h['range']; h['lower_wick_ratio']=(np.minimum(h.open,h.close)-h.low)/h['range']
    h['net_taker_quote']=2*h.taker_buy_quote-h.quote_volume; h['taker_imb1']=h.net_taker_quote/h.quote_volume.replace(0,np.nan)
    for n in [3,6,12]: h[f'taker_imb{n}']=h.net_taker_quote.rolling(n).sum()/h.quote_volume.rolling(n).sum().replace(0,np.nan)
    return h


def resample_ohlcv(h,rule):
    x=pd.DataFrame({'open':h.open.resample(rule,label='right',closed='right').first(),'high':h.high.resample(rule,label='right',closed='right').max(),'low':h.low.resample(rule,label='right',closed='right').min(),'close':h.close.resample(rule,label='right',closed='right').last(),'volume':h.volume.resample(rule,label='right',closed='right').sum(),'quote_volume':h.quote_volume.resample(rule,label='right',closed='right').sum(),'net_taker_quote':h.net_taker_quote.resample(rule,label='right',closed='right').sum()}).dropna()
    x['rsi14']=rsi(x.close); x['ema20']=x.close.ewm(span=20,adjust=False).mean(); x['sma50']=x.close.rolling(50).mean(); x['sma200']=x.close.rolling(200).mean(); x['atr14']=atr(x); x['taker_imb']=x.net_taker_quote/x.quote_volume.replace(0,np.nan)
    return x


def cluster_levels(items,tol=0.02):
    items=[(n,float(v)) for n,v in items if pd.notna(v) and v>0]; items.sort(key=lambda z:z[1],reverse=True); clusters=[]
    for name,level in items:
        hit=False
        for c in clusters:
            if abs(level-c['center'])/c['center']<=tol:
                c['levels'].append(level); c['names'].append(name); c['center']=float(np.mean(c['levels'])); hit=True; break
        if not hit: clusters.append({'center':level,'levels':[level],'names':[name]})
    return sorted(clusters,key=lambda c:c['center'],reverse=True)


def latest_row_before(df,ts):
    sub=df[df.index<=ts]; return None if sub.empty else sub.iloc[-1]


def build_plans(h,d,w,h4):
    plans=[]; last_reg=None; d2=d.copy(); d2['high20_prev']=d2.high.shift(1).rolling(20).max(); d2['low20']=d2.low.rolling(20).min(); d2['low60']=d2.low.rolling(60).min(); d2['ema20_slope5']=d2.ema20/d2.ema20.shift(5)-1
    for i,(t,row) in enumerate(d2.iterrows()):
        if i<210 or pd.isna(row.high20_prev) or pd.isna(row.sma200): continue
        if not (row.close>=row.high20_prev*0.995 and row.close>row.ema20 and row.ema20_slope5>0): continue
        if last_reg is not None and (t-last_reg).days<14: continue
        wr=latest_row_before(w,t-pd.Timedelta(seconds=1)); r4=latest_row_before(h4,t)
        if wr is None or r4 is None: continue
        cand=[('D1_EMA20',row.ema20),('D1_SMA50',row.sma50),('D1_SMA200',row.sma200),('D1_LOW20',row.low20),('D1_LOW60',row.low60),('W1_EMA20',wr.ema20),('4H_EMA20',r4.ema20),('4H_SMA50',r4.sma50)]
        cand=[(n,v) for n,v in cand if pd.notna(v) and v<row.close*0.995 and v>row.close*0.55]
        cl=cluster_levels(cand,0.0225)
        if len(cl)<3: continue
        cl=cl[:3]; atrd=float(row.atr14); zones=[]
        for j,c in enumerate(cl,1):
            center=c['center']; half=min(max(0.006*center,0.25*atrd),0.015*center); zones.append({'n':j,'center':center,'low':center-half,'high':center+half,'confluence':len(c['names']),'sources':c['names']})
        sl=zones[2]['low']-max(0.5*atrd,0.01*zones[2]['center'])
        if sl<=0: continue
        plans.append({'id':f'P{len(plans)+1:03d}','reg':t,'start':t+pd.Timedelta(hours=1),'expiry':t+pd.Timedelta(days=30),'anchor_high':max(float(row.high),float(row.high20_prev)),'sl':sl,'zones':zones}); last_reg=t
    return plans


def zone_quality(plan,zone,touch_row,drow,wrow,r4):
    s=0
    s+=25 if drow.close>drow.ema20 and drow.ema20>drow.sma50 else (19 if drow.close>drow.ema20 else (12 if drow.close>drow.sma50 else 5))
    s+=15 if wrow is not None and wrow.close>wrow.ema20 else (7 if wrow is not None else 0)
    s+=15 if r4 is not None and r4.close>r4.ema20 and r4.ema20>r4.sma50 else (10 if r4 is not None and r4.close>r4.ema20 else (5 if r4 is not None else 0))
    s+=10 if touch_row.close>=zone['center'] and touch_row.close>touch_row.open else (5 if touch_row.close>=zone['low'] else 0)
    s+=min(15,zone['confluence']*5)
    s+=10 if touch_row.vol_z>=1.5 and touch_row.close_loc>=0.60 else (6 if touch_row.vol_z>=0.8 and touch_row.close_loc>=0.55 else (3 if touch_row.close_loc>=0.60 else 0))
    dd=1-touch_row.close/plan['anchor_high']; th={1:0.03,2:0.06,3:0.10}[zone['n']]; s+=10 if dd>=th else (6 if dd>=th*0.6 else 2)
    return int(round(min(100,s)))


def fear_flags(h,pos):
    r=h.iloc[pos]
    return {'ret12':bool(r.ret12<=-0.03),'rsi':bool(r.rsi14<=35),'volume':bool(r.vol_z>=1.5),'taker':bool(r.taker_imb6<=-0.08),'range':bool((r.high-r.low)/r.close>=max(0.018,1.5*(r.atr14/r.close if pd.notna(r.atr14) else 0)))}


def exhaust_score(h,pos,zone):
    if pos<12:return 0
    r=h.iloc[pos]; prev=h.iloc[pos-12:pos]; prev_low=float(prev.low.min()); cl=float(r.close_loc if pd.notna(r.close_loc) else 0.5); raw=0
    raw+=30 if r.low<prev_low and r.close>prev_low else (24 if r.low<=prev_low*1.002 and cl>=0.65 else (18 if r.low<=prev_low*1.003 and r.close>=zone['center'] else 0))
    raw+=20 if r.vol_z>=2 and cl>=0.60 else (14 if r.vol_z>=1 and cl>=0.55 else (5 if r.vol_z>=1 else 0))
    p3=h.iloc[pos-3:pos]; prev_imb=float(p3.net_taker_quote.sum()/p3.quote_volume.sum()) if p3.quote_volume.sum()!=0 else 0; cur=float(r.taker_imb3) if pd.notna(r.taker_imb3) else 0
    raw+=15 if cur<=-0.06 and r.close>=prev_low and cur<prev_imb else (10 if cur<=-0.04 and r.close>=zone['center'] else 0)
    prev3=float(h.close.iloc[pos-3]/h.close.iloc[pos-6]-1) if pos>=6 else 0; cur3=float(r.ret3) if pd.notna(r.ret3) else 0
    raw+=10 if prev3<-0.01 and cur3>prev3+0.008 else (6 if cur3>-0.003 and float(r.ret6 if pd.notna(r.ret6) else 0)<-0.01 else 0)
    raw+=10 if r.lower_wick_ratio>=0.45 and r.close>=zone['center'] else (6 if r.lower_wick_ratio>=0.30 else (8 if r.close>zone['high'] else 0))
    return int(min(100,round(raw/85*100)))


def early_score(h,pos,zone,exhaust):
    if pos<12:return 0
    r=h.iloc[pos]; prev12=h.iloc[pos-12:pos]; prev_low=float(prev12.low.min()); raw=0
    raw+=25 if r.low<prev_low and r.close>prev_low else (22 if r.low<=zone['high'] and r.close>zone['high'] else (15 if r.low<=zone['center'] and r.close>=zone['center'] else 0))
    low_idx=prev12.low.idxmin(); pr=float(h.loc[low_idx,'rsi14']); raw+=15 if r.low<=prev_low*1.002 and r.rsi14>=pr+5 else (10 if r.rsi14>35 and h.rsi14.iloc[pos-1]<=35 else (8 if r.rsi14>=h.rsi14.iloc[pos-1]+4 else 0))
    raw+=15 if r.close>r.open and r.close_loc>=0.65 else (10 if r.close_loc>=0.60 else (6 if r.close>r.open else 0))
    raw+=15 if r.ret1>0 and h.ret3.iloc[pos-1]<0 else (10 if r.ret3>h.ret3.iloc[pos-3]+0.01 else 0)
    raw+=10 if pos>=2 and (h.close.iloc[pos-1:pos+1]>=zone['center']).all() else (8 if r.close>zone['high'] else (5 if r.close>zone['center'] else 0))
    raw+=10 if exhaust>=70 else (6 if exhaust>=60 else 0)
    cur=float(r.taker_imb3) if pd.notna(r.taker_imb3) else 0; prev=float(h.taker_imb3.iloc[pos-3]) if pos>=3 and pd.notna(h.taker_imb3.iloc[pos-3]) else 0; raw+=10 if cur>=prev+0.05 else (5 if cur>=prev+0.02 else 0)
    return int(min(100,round(raw)))


def reaction_score(h,pos,zone,drow,exhaust):
    r=h.iloc[pos]; raw=0
    raw+=30 if r.low<=zone['high'] and r.close>zone['high'] else (22 if r.close>=zone['center'] else (14 if pos>=2 and r.low>=h.low.iloc[pos-2:pos].min() else 0))
    raw+=15 if pos>=2 and (h.close.iloc[pos-1:pos+1]>=zone['center']).all() else (10 if r.close>zone['high'] else 0)
    raw+=15 if exhaust>=70 else (10 if exhaust>=60 else 0)
    raw+=10 if r.rsi14>=h.rsi14.iloc[pos-3]+6 else (6 if r.rsi14>=40 else 0)
    raw+=10 if r.close>=r.ema20 else 0
    cur=float(r.taker_imb3) if pd.notna(r.taker_imb3) else 0; prev=float(h.taker_imb3.iloc[pos-3]) if pos>=3 and pd.notna(h.taker_imb3.iloc[pos-3]) else 0; raw+=10 if cur>=prev+0.05 else (5 if cur>=prev+0.02 else 0)
    raw+=10 if drow.close>=drow.ema20 else (5 if drow.close>=drow.sma50 else 0)
    return int(min(100,round(raw)))


def rr(entry,sl,target):
    risk=entry-sl; reward=target-entry; return reward/risk if risk>0 and reward>0 else -999

def safety_proxy(entry,sl):
    dist=(entry-sl)/entry if entry>sl>0 else 1
    return 90 if dist<=0.12 else (85 if dist<=0.15 else (80 if dist<=0.18 else 70))
def nonchase(r): return pd.isna(r.ret24) or r.ret24<0.05

TH={'strict_v25':{'zone':82,'exhaust':70,'early':75,'rr':4.0},'moderate':{'zone':80,'exhaust':65,'early':70,'rr':3.5},'loose':{'zone':78,'exhaust':60,'early':65,'rr':3.0}}


def evaluate(h,d,w,h4,plans):
    events=[]; htimes=h.index
    for p in plans:
        win=h[(h.index>=p['start'])&(h.index<=p['expiry'])]
        if win.empty: continue
        drow=d.loc[d.index<=p['reg']].iloc[-1]; wrow=latest_row_before(w,p['reg']); r4reg=latest_row_before(h4,p['reg'])
        for zone in p['zones']:
            hits=win[(win.low<=zone['high'])&(win.high>=zone['low'])]
            if hits.empty:continue
            tt=hits.index[0]; tp=htimes.get_loc(tt); tr=h.iloc[tp]; zq=zone_quality(p,zone,tr,drow,wrow,r4reg)
            rec={'plan':p['id'],'zone':zone['n'],'reg':p['reg'],'touch':tt,'zone_low':zone['low'],'zone_high':zone['high'],'zone_mid':zone['center'],'zone_quality':zq,'sl':p['sl'],'target':p['anchor_high'],'touch_price':float(tr.close)}
            end=min(len(h)-1,tp+24); early={k:None for k in TH}; fast=None; h4c=None; maxex=maxer=maxre=0; fear_any=False
            for pos in range(tp,end+1):
                r=h.iloc[pos]; fear=sum(fear_flags(h,pos).values())>=2; fear_any|=fear; ex=exhaust_score(h,pos,zone); er=early_score(h,pos,zone,ex); re=reaction_score(h,pos,zone,drow,ex); maxex=max(maxex,ex); maxer=max(maxer,er); maxre=max(maxre,re); sp=safety_proxy(float(r.close),p['sl']); nc=nonchase(r)
                indep=[ex>=70,bool(r.vol_z>=1 and r.close_loc>=0.55),bool(r.taker_imb6<=-0.05 and r.close>=zone['center']),bool(r.rsi14<=38 and er>=70),bool(r.ret3>h.ret3.iloc[pos-3]+0.008 if pos>=3 and pd.notna(h.ret3.iloc[pos-3]) else False),bool(r.lower_wick_ratio>=0.30 or r.close>zone['high'])]
                for name,th in TH.items():
                    if early[name] is None and zq>=th['zone'] and fear and ex>=th['exhaust'] and er>=th['early'] and sp>=85 and nc and rr(float(r.close),p['sl'],p['anchor_high'])>=th['rr'] and sum(indep)>=3:
                        early[name]=(h.index[pos],float(r.close))
                persist=(pos>=1 and r.close>=zone['center'] and h.close.iloc[pos-1]>=zone['center']) or (r.close>zone['high'] and pos>=2 and h.low.iloc[pos-2:pos+1].min()>=zone['low']*0.997)
                if fast is None and zq>=80 and r.close>zone['high'] and persist and re>=65 and sp>=80 and nc and rr(float(r.close),p['sl'],p['anchor_high'])>=3: fast=(h.index[pos],float(r.close))
                if h4c is None and pos>=3 and h.index[pos].hour in [3,7,11,15,19,23]:
                    b=h.iloc[pos-3:pos+1]; o=float(b.open.iloc[0]); lo=float(b.low.min()); c=float(b.close.iloc[-1]); defense=(c>zone['high'] and c>o) or (lo<=zone['high'] and c>=zone['center'] and lo>float(h.low.iloc[tp:pos+1].min())*0.999)
                    if zq>=80 and defense and re>=65 and sp>=80 and nc and rr(c,p['sl'],p['anchor_high'])>=3:h4c=(h.index[pos],c)
            confirm=min([x for x in [fast,h4c] if x is not None],key=lambda z:z[0],default=None)
            rec.update({'fear':fear_any,'max_exhaust':maxex,'max_early':maxer,'max_reaction':maxre,'fast_time':fast[0] if fast else pd.NaT,'fast_price':fast[1] if fast else np.nan,'h4_time':h4c[0] if h4c else pd.NaT,'h4_price':h4c[1] if h4c else np.nan,'confirm_time':confirm[0] if confirm else pd.NaT,'confirm_price':confirm[1] if confirm else np.nan})
            for name,v in early.items(): rec[f'{name}_time']=v[0] if v else pd.NaT; rec[f'{name}_price']=v[1] if v else np.nan
            horizon=h[(h.index>=tt)&(h.index<=min(p['expiry'],tt+pd.Timedelta(days=30)))]
            for method in ['confirm']+list(TH):
                et=rec['confirm_time'] if method=='confirm' else rec[f'{method}_time']; ep=rec['confirm_price'] if method=='confirm' else rec[f'{method}_price']
                if pd.isna(et) or pd.isna(ep):continue
                fut=horizon[horizon.index>=et]
                if fut.empty:continue
                for n in [4,24,72]: rec[f'{method}_ret{n}h']=float(fut.close.iloc[min(n,len(fut)-1)]/ep-1)
                rec[f'{method}_mfe']=float(fut.high.max()/ep-1); rec[f'{method}_mae']=float(fut.low.min()/ep-1); status='none'; ht=pd.NaT
                for xt,br in fut.iterrows():
                    if br.low<=p['sl']:status='SL';ht=xt;break
                    if br.high>=p['anchor_high']:status='TP1';ht=xt;break
                rec[f'{method}_first_hit']=status; rec[f'{method}_hit_time']=ht
                if status=='TP1':
                    trough=float(fut.loc[:ht].low.min()); den=p['anchor_high']-trough; rec[f'{method}_move_capture']=(p['anchor_high']-ep)/den if den>0 else np.nan
            se=early['strict_v25']
            if se and confirm and se[0]<confirm[0]: rec['strict_entry_improve_pct']=(confirm[1]-se[1])/confirm[1]; rec['strict_to_confirm_hours']=(confirm[0]-se[0]).total_seconds()/3600; rec['strict_confirm_within24']=rec['strict_to_confirm_hours']<=24; rec['strict_confirm_within48']=rec['strict_to_confirm_hours']<=48
            events.append(rec)
    return pd.DataFrame(events)


def pct(x):return None if pd.isna(x) else round(float(x)*100,2)
def summarize(events,start=None,end=None):
    x=events.copy()
    if start:x=x[x.touch>=pd.Timestamp(start,tz='UTC')]
    if end:x=x[x.touch<pd.Timestamp(end,tz='UTC')]
    out={'events':int(len(x)),'fear_events':int(x.fear.sum()) if len(x) else 0}
    for m in ['confirm']+list(TH):
        col='confirm_time' if m=='confirm' else f'{m}_time'; sig=x[x[col].notna()]; out[m]={'signals':int(len(sig))}
        if len(sig):
            for k in ['ret4h','ret24h','ret72h','mfe','mae','move_capture']:
                c=f'{m}_{k}'; out[m][f'median_{k}_pct']=pct(sig[c].median()) if c in sig else None
            fh=f'{m}_first_hit'; out[m]['sl_first_rate_pct']=round(100*(sig[fh]=='SL').mean(),1); out[m]['tp1_first_rate_pct']=round(100*(sig[fh]=='TP1').mean(),1)
    pair=x[x.strict_v25_time.notna()&x.confirm_time.notna()&(x.strict_v25_time<x.confirm_time)]; out['strict_vs_confirm_pairs']=int(len(pair))
    if len(pair): out['median_entry_improvement_pct']=round(100*pair.strict_entry_improve_pct.median(),2); out['median_lead_hours']=round(pair.strict_to_confirm_hours.median(),1); out['confirm_within24_pct']=round(100*pair.strict_confirm_within24.mean(),1); out['confirm_within48_pct']=round(100*pair.strict_confirm_within48.mean(),1)
    return out


def main():
    h,fail=load_data(); h=enrich_hourly(h); h4=resample_ohlcv(h,'4h'); d=resample_ohlcv(h,'1D'); w=resample_ohlcv(h,'W-SUN'); plans=build_plans(h,d,w,h4); events=evaluate(h,d,w,h4,plans)
    result={'schema':'MASTER_BTC_TREND_V2.5_BACKTEST_V1','generated_utc':datetime.now(timezone.utc).isoformat(),'data':{'source':'Binance Vision USD-M Futures BTCUSDT 1h monthly klines','start':str(h.index.min()),'end':str(h.index.max()),'rows':int(len(h)),'download_failures':fail},'methodology':{'type':'Stage-1 quantitative proxy; no-lookahead plan registration; OI/Funding excluded','taker_proxy':'kline taker-buy quote vs total quote; not wallet CVD','v24_proxy':'first valid FAST 1H or 4H confirmation, Zone>=80, Reaction>=65, RR>=3','v25_strict':'Zone>=82 + Fear + Exhaustion>=70 + Early>=75 + RR>=4 + >=3 independent reversal groups','same_bar':'SL-first conservative','limitations':['No historical OI/Funding axis in Stage-1','No ETF/macro severe-risk veto in signal simulation','Zone/score rules are deterministic proxies of visual MASTER rules','Fees/funding/slippage excluded']},'plans':int(len(plans)),'full':summarize(events),'in_sample_2023_09_to_2025_02':summarize(events,'2023-09-01','2025-03-01'),'out_of_sample_2025_03_to_2026_09':summarize(events,'2025-03-01','2026-09-01')}
    strict=result['full']['strict_v25']; conf=result['full']['confirm']; pairs=result['full']['strict_vs_confirm_pairs']; decision=[]
    if strict.get('signals',0)<10: decision.append('STRICT EARLY sample <10: do not optimize thresholds yet; keep EARLY small/experimental.')
    else:
        decision.append('Strict EARLY false-risk is not materially worse than confirm-only by SL-first metric.' if strict.get('sl_first_rate_pct',100)<=conf.get('sl_first_rate_pct',100)+5 else 'Strict EARLY has materially higher SL-first risk; reduce early allocation or strengthen gate.')
        if pairs and result['full'].get('median_entry_improvement_pct',0)>0.5: decision.append('Strict EARLY provides meaningful earlier/lower entry versus confirmation on paired cases.')
    result['preliminary_decision']=decision
    (OUTDIR/'v25_backtest_summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    if len(events):events.to_csv(OUTDIR/'v25_backtest_events.csv',index=False)
    f=result['full']; ins=result['in_sample_2023_09_to_2025_02']; oos=result['out_of_sample_2025_03_to_2026_09']; md=['# MASTER BTC TREND V2.5 Stage-1 Backtest','',f"- Data: {result['data']['source']}",f"- Range: {result['data']['start']} ~ {result['data']['end']}",f"- 1H rows: {result['data']['rows']:,}",f"- Registered plans: {result['plans']}",f"- Zone-touch events: {f['events']}",'','## Full sample','','| Metric | Confirm-only proxy | V2.5 strict EARLY | Moderate | Loose |','|---|---:|---:|---:|---:|']
    for key,label in [('signals','Signals'),('sl_first_rate_pct','SL-first %'),('tp1_first_rate_pct','TP1-first %'),('median_ret24h_pct','Median +24h %'),('median_ret72h_pct','Median +72h %'),('median_mfe_pct','Median MFE %'),('median_mae_pct','Median MAE %')]:
        vals=[f.get(m,{}).get(key,'N/A') for m in ['confirm','strict_v25','moderate','loose']]; md.append(f"| {label} | {vals[0]} | {vals[1]} | {vals[2]} | {vals[3]} |")
    md+=['',f"- Strict EARLY vs later-confirm paired cases: **{f.get('strict_vs_confirm_pairs',0)}**",f"- Median entry improvement: **{f.get('median_entry_improvement_pct','N/A')}%**",f"- Median lead time: **{f.get('median_lead_hours','N/A')}h**",f"- Confirm within 24h after strict EARLY: **{f.get('confirm_within24_pct','N/A')}%**",'','## Split check','',f"- In-sample events/signals(strict): {ins['events']} / {ins['strict_v25']['signals']}",f"- Out-of-sample events/signals(strict): {oos['events']} / {oos['strict_v25']['signals']}",'','## Preliminary decision']+[f'- {x}' for x in decision]+['','## Important limitations']+[f"- {x}" for x in result['methodology']['limitations']]
    (OUTDIR/'v25_backtest_report.md').write_text('\n'.join(md),encoding='utf-8')
    print(json.dumps({'rows':len(h),'plans':len(plans),'events':len(events),'summary':result['full'],'failures':fail},ensure_ascii=False,default=str,indent=2))

if __name__=='__main__':main()
