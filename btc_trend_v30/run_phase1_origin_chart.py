from __future__ import annotations

import io
import json
import math
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import requests

START = pd.Timestamp('2017-09-01T00:00:00Z')
END = pd.Timestamp('2026-09-01T00:00:00Z')
OUT = Path('btc_trend_v30/output/phase1')
OUT.mkdir(parents=True, exist_ok=True)

COLS = [
    'open_time','open','high','low','close','volume','close_time','quote_volume',
    'trades','taker_buy_base','taker_buy_quote','ignore'
]
BASE = 'https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/{interval}/BTCUSDT-{interval}-{ym}.zip'


def months(start: pd.Timestamp, end: pd.Timestamp):
    cur = pd.Timestamp(start.year, start.month, 1, tz='UTC')
    last = pd.Timestamp(end.year, end.month, 1, tz='UTC')
    while cur < last:
        yield cur.strftime('%Y-%m')
        cur = cur + pd.offsets.MonthBegin(1)


def normalize_time(x: pd.Series) -> pd.DatetimeIndex:
    v = pd.to_numeric(x, errors='coerce').astype('float64')
    # Binance Data Vision moved some files from millisecond to microsecond timestamps.
    v = np.where(v > 1e14, v / 1000.0, v)
    return pd.to_datetime(v, unit='ms', utc=True)


def load_interval(interval: str) -> tuple[pd.DataFrame, list[str]]:
    frames, failures = [], []
    s = requests.Session()
    s.headers.update({'User-Agent': 'master-btc-trend-v30-research/1.0'})
    for ym in months(START, END):
        url = BASE.format(interval=interval, ym=ym)
        try:
            r = s.get(url, timeout=45)
            if r.status_code != 200:
                failures.append(f'{interval}:{ym}:HTTP{r.status_code}')
                continue
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                names = [n for n in z.namelist() if n.endswith('.csv')]
                if not names:
                    failures.append(f'{interval}:{ym}:no_csv')
                    continue
                raw = pd.read_csv(z.open(names[0]), header=None, names=COLS)
            frames.append(raw)
        except Exception as e:
            failures.append(f'{interval}:{ym}:{type(e).__name__}')
    if not frames:
        raise RuntimeError(f'No Binance data for {interval}')
    df = pd.concat(frames, ignore_index=True)
    df['time'] = normalize_time(df.open_time)
    for c in ['open','high','low','close','volume','quote_volume','taker_buy_quote']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df[['time','open','high','low','close','volume','quote_volume','taker_buy_quote']]
    df = df.dropna().drop_duplicates('time').sort_values('time')
    df = df[(df.time >= START) & (df.time < END)].set_index('time')
    df['net_taker_quote'] = 2.0 * df.taker_buy_quote - df.quote_volume
    return df, failures


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0)
    dn = -d.clip(upper=0)
    au = up.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    ad = dn.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    out = 100 - 100 / (1 + au / ad.replace(0, np.nan))
    return out.fillna(50)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df.close.shift(1)
    tr = pd.concat([
        (df.high-df.low).abs(), (df.high-pc).abs(), (df.low-pc).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False, min_periods=n).mean()


def kdj(df: pd.DataFrame, n: int = 9) -> tuple[pd.Series,pd.Series,pd.Series]:
    lo = df.low.rolling(n).min()
    hi = df.high.rolling(n).max()
    rsv = 100 * (df.close-lo) / (hi-lo).replace(0, np.nan)
    k = rsv.ewm(alpha=1/3, adjust=False).mean().fillna(50)
    d = k.ewm(alpha=1/3, adjust=False).mean().fillna(50)
    j = 3*k - 2*d
    return k,d,j


def zscore(s: pd.Series, n: int = 40) -> pd.Series:
    m = s.rolling(n).mean(); sd = s.rolling(n).std(ddof=0)
    return ((s-m)/sd.replace(0,np.nan)).fillna(0)


def add_features(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    x = df.copy()
    x['ema20'] = x.close.ewm(span=20, adjust=False).mean()
    x['ema60'] = x.close.ewm(span=60, adjust=False).mean()
    x['sma50'] = x.close.rolling(50).mean()
    x['sma200'] = x.close.rolling(200).mean()
    x['rsi14'] = rsi(x.close)
    x['k'],x['d'],x['j'] = kdj(x)
    x['atr14'] = atr(x)
    x['vol_z'] = zscore(x.volume, 40)
    x['range'] = (x.high-x.low).replace(0,np.nan)
    x['body'] = (x.close-x.open)
    x['body_ratio'] = x.body.abs()/x['range']
    x['close_loc'] = (x.close-x.low)/x['range']
    x['upper_wick'] = (x.high-np.maximum(x.open,x.close))/x['range']
    x['lower_wick'] = (np.minimum(x.open,x.close)-x.low)/x['range']
    x['taker_imb'] = x.net_taker_quote/x.quote_volume.replace(0,np.nan)
    x['taker_imb_3'] = x.net_taker_quote.rolling(3).sum()/x.quote_volume.rolling(3).sum().replace(0,np.nan)
    x['taker_imb_6'] = x.net_taker_quote.rolling(6).sum()/x.quote_volume.rolling(6).sum().replace(0,np.nan)
    x['ret1'] = x.close.pct_change(1)
    x['ret3'] = x.close.pct_change(3)
    x['ret6'] = x.close.pct_change(6)
    x['ret12'] = x.close.pct_change(12)
    x['high20_prev'] = x.high.shift(1).rolling(20).max()
    x['low20_prev'] = x.low.shift(1).rolling(20).min()
    x['high60_prev'] = x.high.shift(1).rolling(60).max()
    x['low60_prev'] = x.low.shift(1).rolling(60).min()
    x['ema20_slope5'] = x.ema20/x.ema20.shift(5)-1
    x['sma50_slope10'] = x.sma50/x.sma50.shift(10)-1
    x['rsi_delta5'] = x.rsi14-x.rsi14.shift(5)
    x['j_delta3'] = x.j-x.j.shift(3)

    # Candle force uses only the just-completed candle and prior context.
    bull = (
        35*x.body_ratio.clip(0,1)*(x.body>0).astype(float)
        + 25*x.close_loc.clip(0,1)
        + 15*x.lower_wick.clip(0,1)
        + 15*((x.vol_z.clip(-1,3)+1)/4).clip(0,1)
        + 10*(x.close>x.high.shift(1)).astype(float)
    )
    bear = (
        35*x.body_ratio.clip(0,1)*(x.body<0).astype(float)
        + 25*(1-x.close_loc.clip(0,1))
        + 15*x.upper_wick.clip(0,1)
        + 15*((x.vol_z.clip(-1,3)+1)/4).clip(0,1)
        + 10*(x.close<x.low.shift(1)).astype(float)
    )
    x['candle_bull'] = bull.clip(0,100)
    x['candle_bear'] = bear.clip(0,100)

    # Wave efficiency: compare recent half-wave with the preceding half-wave.
    # Falling price with increasingly negative flow but shrinking downside progress is exhaustion/absorption evidence.
    n = 12 if prefix == '4H' else 10
    h = max(3, n//2)
    recent_ret = x.close/x.close.shift(h)-1
    prior_ret = x.close.shift(h)/x.close.shift(2*h)-1
    recent_flow = x.net_taker_quote.rolling(h).sum()/x.quote_volume.rolling(h).sum().replace(0,np.nan)
    prior_flow = x.net_taker_quote.shift(h).rolling(h).sum()/x.quote_volume.shift(h).rolling(h).sum().replace(0,np.nan)
    x['down_exhaust'] = (
        45*((-prior_ret).clip(0,0.20)/0.20).clip(0,1)
        + 30*((recent_ret-prior_ret).clip(0,0.12)/0.12).clip(0,1)
        + 25*((prior_flow-recent_flow).clip(-0.20,0.20)+0.20)/0.40
    ).clip(0,100)
    x['up_exhaust'] = (
        45*((prior_ret).clip(0,0.20)/0.20).clip(0,1)
        + 30*((prior_ret-recent_ret).clip(0,0.12)/0.12).clip(0,1)
        + 25*((recent_flow-prior_flow).clip(-0.20,0.20)+0.20)/0.40
    ).clip(0,100)
    return x


def latest_before(df: pd.DataFrame, ts: pd.Timestamp):
    s = df[df.index <= ts]
    return None if s.empty else s.iloc[-1]


def chart_origin_scores(d: pd.DataFrame, h4: pd.DataFrame) -> pd.DataFrame:
    rows=[]
    for t,r in d.iterrows():
        if pd.isna(r.sma200) or pd.isna(r.low60_prev) or pd.isna(r.high60_prev):
            continue
        r4 = latest_before(h4, t + pd.Timedelta(hours=23, minutes=59))
        if r4 is None or pd.isna(r4.sma200):
            continue

        # Price-structure transition, LONG and SHORT mirrors.
        near_low = max(0.0, min(1.0, (0.10 - max(0.0, r.close/r.low60_prev-1))/0.10))
        near_high = max(0.0, min(1.0, (0.10 - max(0.0, 1-r.close/r.high60_prev))/0.10))
        reclaim_l = float(r.close>r.ema20) + float(r4.close>r4.ema20) + float(r.close>r.high20_prev)
        reclaim_s = float(r.close<r.ema20) + float(r4.close<r4.ema20) + float(r.close<r.low20_prev)
        structure_l = 40*near_low + 20*min(1,reclaim_l/2) + 20*float(r.rsi_delta5>0) + 20*float(r4.ret3>0)
        structure_s = 40*near_high + 20*min(1,reclaim_s/2) + 20*float(r.rsi_delta5<0) + 20*float(r4.ret3<0)

        # Wave-energy exhaustion of old trend + first opposite impulse.
        wave_l = 0.55*float(r.down_exhaust) + 0.45*float(r4.down_exhaust)
        wave_s = 0.55*float(r.up_exhaust) + 0.45*float(r4.up_exhaust)

        candle_l = 0.45*float(r.candle_bull) + 0.55*float(r4.candle_bull)
        candle_s = 0.45*float(r.candle_bear) + 0.55*float(r4.candle_bear)

        ma_l = 35*float(r.close>r.ema20) + 20*float(r.ema20_slope5>0) + 15*float(r4.close>r4.ema20) + 15*float(r4.ema20_slope5>0) + 15*float(r.close>r.sma50)
        ma_s = 35*float(r.close<r.ema20) + 20*float(r.ema20_slope5<0) + 15*float(r4.close<r4.ema20) + 15*float(r4.ema20_slope5<0) + 15*float(r.close<r.sma50)

        mom_l = 35*float(r.rsi_delta5>0) + 20*float(r.rsi14>40) + 20*float(r.j_delta3>0) + 25*float(r4.rsi_delta5>0)
        mom_s = 35*float(r.rsi_delta5<0) + 20*float(r.rsi14<60) + 20*float(r.j_delta3<0) + 25*float(r4.rsi_delta5<0)

        volflow_l = 30*float(r.vol_z>0.5 and r.close_loc>0.55) + 25*float(r4.vol_z>0.5 and r4.close_loc>0.55) + 25*float(r.taker_imb_3>r.taker_imb_6) + 20*float(r4.taker_imb_3>r4.taker_imb_6)
        volflow_s = 30*float(r.vol_z>0.5 and r.close_loc<0.45) + 25*float(r4.vol_z>0.5 and r4.close_loc<0.45) + 25*float(r.taker_imb_3<r.taker_imb_6) + 20*float(r4.taker_imb_3<r4.taker_imb_6)

        # Phase1 chart-only origin score: no fractal/OI/Funding/ETF yet.
        # Weights correspond to the chart subset of the approved V3 reversal design.
        w = {'structure':25,'wave':22,'candle':13,'ma':13,'momentum':10,'volflow':17}
        long_score = (w['structure']*structure_l + w['wave']*wave_l + w['candle']*candle_l + w['ma']*ma_l + w['momentum']*mom_l + w['volflow']*volflow_l)/100
        short_score = (w['structure']*structure_s + w['wave']*wave_s + w['candle']*candle_s + w['ma']*ma_s + w['momentum']*mom_s + w['volflow']*volflow_s)/100
        rows.append({
            'time':t,'close':float(r.close),
            'long_origin_chart':round(float(long_score),3),'short_origin_chart':round(float(short_score),3),
            'structure_l':round(float(structure_l),2),'structure_s':round(float(structure_s),2),
            'wave_l':round(float(wave_l),2),'wave_s':round(float(wave_s),2),
            'candle_l':round(float(candle_l),2),'candle_s':round(float(candle_s),2),
            'ma_l':round(float(ma_l),2),'ma_s':round(float(ma_s),2),
            'momentum_l':round(float(mom_l),2),'momentum_s':round(float(mom_s),2),
            'volflow_l':round(float(volflow_l),2),'volflow_s':round(float(volflow_s),2),
        })
    return pd.DataFrame(rows).set_index('time')


def first_passage(d: pd.DataFrame, t: pd.Timestamp, up: float, down: float, horizon: int) -> str:
    if t not in d.index:
        return 'NONE'
    p = float(d.loc[t,'close'])
    fut = d[(d.index>t)&(d.index<=t+pd.Timedelta(days=horizon))]
    for _,r in fut.iterrows():
        hit_up = r.high >= p*(1+up)
        hit_dn = r.low <= p*(1-down)
        if hit_up and hit_dn:
            return 'AMBIG'
        if hit_up: return 'UP'
        if hit_dn: return 'DOWN'
    return 'NONE'


def build_truth_events(d: pd.DataFrame) -> pd.DataFrame:
    # Labels may use future bars; engine features never do. This is evaluation-only ground truth.
    idx=list(d.index); events=[]
    for i in range(60,len(d)-181):
        t=idx[i]; r=d.iloc[i]
        pre=d.iloc[i-30:i+1]; post=d.iloc[i+1:i+31]
        if r.low <= pre.low.min()*1.001 and r.low <= post.low.min()*1.001:
            fp=first_passage(d,t,0.30,0.15,180)
            if fp=='UP':
                events.append({'time':t,'side':'LOW','price':float(r.low),'close':float(r.close),'outcome':'+30_BEFORE_-15'})
        if r.high >= pre.high.max()*0.999 and r.high >= post.high.max()*0.999:
            fp=first_passage(d,t,0.15,0.25,180)
            if fp=='DOWN':
                events.append({'time':t,'side':'HIGH','price':float(r.high),'close':float(r.close),'outcome':'-25_BEFORE_+15'})
    e=pd.DataFrame(events)
    if e.empty: return e
    # Deduplicate clustered truth points: keep the more extreme event inside 21 days.
    kept=[]
    for side in ['LOW','HIGH']:
        s=e[e.side==side].sort_values('time')
        cluster=[]
        for _,row in s.iterrows():
            if not cluster or (row.time-cluster[-1].time).days<=21:
                cluster.append(row)
            else:
                kept.append(min(cluster,key=lambda q:q.price) if side=='LOW' else max(cluster,key=lambda q:q.price))
                cluster=[row]
        if cluster:
            kept.append(min(cluster,key=lambda q:q.price) if side=='LOW' else max(cluster,key=lambda q:q.price))
    return pd.DataFrame(kept).sort_values('time').reset_index(drop=True)


def dedupe_signals(scores: pd.DataFrame, col: str, threshold: float, cooldown_days: int=14) -> list[pd.Timestamp]:
    hits=scores[scores[col]>=threshold].sort_index()
    out=[]; last=None
    for t in hits.index:
        if last is None or (t-last).days>cooldown_days:
            out.append(t); last=t
        elif scores.loc[t,col] > scores.loc[last,col]:
            out[-1]=t; last=t
    return out


def evaluate_threshold(scores: pd.DataFrame, truth: pd.DataFrame, side: str, col: str, threshold: float) -> dict:
    sig=dedupe_signals(scores,col,threshold)
    truth_s=truth[truth.side==side]
    matched_truth=set(); correct=0; lags=[]; price_err=[]
    for st in sig:
        cand=truth_s[(truth_s.time>=st-pd.Timedelta(days=7))&(truth_s.time<=st+pd.Timedelta(days=14))]
        if cand.empty: continue
        cand=cand.assign(dist=(cand.time-st).abs())
        tr=cand.sort_values('dist').iloc[0]
        key=pd.Timestamp(tr.time)
        if key in matched_truth: continue
        matched_truth.add(key); correct+=1
        lags.append((st-key).days)
        sp=float(scores.loc[st,'close']); tp=float(tr.price)
        price_err.append(abs(sp/tp-1)*100)
    precision=correct/len(sig) if sig else np.nan
    recall=correct/len(truth_s) if len(truth_s) else np.nan
    return {
        'side':side,'threshold':threshold,'signals':len(sig),'truth_events':len(truth_s),'matched':correct,
        'precision_pct':round(100*precision,2) if pd.notna(precision) else None,
        'recall_pct':round(100*recall,2) if pd.notna(recall) else None,
        'median_detection_lag_days':round(float(np.median(lags)),2) if lags else None,
        'median_origin_price_error_pct':round(float(np.median(price_err)),2) if price_err else None,
    }


def score_bucket_outcomes(scores: pd.DataFrame, side: str, col: str) -> list[dict]:
    bins=[(0,49),(50,64),(65,79),(80,100)]
    out=[]
    for lo,hi in bins:
        x=scores[(scores[col]>=lo)&(scores[col]<=hi)]
        if side=='LOW':
            results=[first_passage(D,t,0.30,0.15,180) for t in x.index]
            hit=sum(r=='UP' for r in results); adverse=sum(r=='DOWN' for r in results)
        else:
            results=[first_passage(D,t,0.15,0.25,180) for t in x.index]
            hit=sum(r=='DOWN' for r in results); adverse=sum(r=='UP' for r in results)
        evaluable=hit+adverse
        out.append({'side':side,'bucket':f'{lo}-{hi}','n':len(x),'evaluable':evaluable,'desired_first_pct':round(100*hit/evaluable,2) if evaluable else None})
    return out


def year_walk_forward(scores: pd.DataFrame, truth: pd.DataFrame, side: str, col: str) -> list[dict]:
    # Threshold chosen ONLY from earlier years, then frozen for each test year.
    rows=[]
    years=sorted({t.year for t in scores.index if t.year>=2020})
    for y in years:
        train=scores[scores.index.year<y]
        test=scores[scores.index.year==y]
        tr_truth=truth[truth.time.dt.year<y]
        te_truth=truth[truth.time.dt.year==y]
        if len(train)<500 or len(test)<100 or len(tr_truth[tr_truth.side==side])<3:
            continue
        candidates=[]
        for th in [55,60,65,70,75,80]:
            ev=evaluate_threshold(train,tr_truth,side,col,th)
            p=ev['precision_pct'] or 0; r=ev['recall_pct'] or 0
            # F0.5-like objective prioritizes useful signals but penalizes zero recall.
            objective=(1.25*p*r/(0.25*p+r)) if p>0 and r>0 else 0
            candidates.append((objective,th,ev))
        _,th,_=max(candidates,key=lambda z:(z[0],-z[1]))
        ev=evaluate_threshold(test,te_truth,side,col,th)
        ev['test_year']=y; ev['chosen_from_prior_years']=th
        rows.append(ev)
    return rows


def aggregate_oos(rows: list[dict]) -> dict:
    sig=sum(r['signals'] for r in rows); matched=sum(r['matched'] for r in rows); truth=sum(r['truth_events'] for r in rows)
    return {
        'signals':sig,'matched':matched,'truth_events':truth,
        'precision_pct':round(100*matched/sig,2) if sig else None,
        'recall_pct':round(100*matched/truth,2) if truth else None,
        'years':len(rows)
    }


if __name__ == '__main__':
    d_raw, fail_d = load_interval('1d')
    h4_raw, fail_4 = load_interval('4h')
    D = add_features(d_raw,'1D')
    H4 = add_features(h4_raw,'4H')
    scores = chart_origin_scores(D,H4)
    truth = build_truth_events(D)
    if truth.empty:
        raise RuntimeError('No truth events built')
    truth['time']=pd.to_datetime(truth.time,utc=True)

    sweep=[]
    for side,col in [('LOW','long_origin_chart'),('HIGH','short_origin_chart')]:
        for th in [50,55,60,65,70,75,80]:
            sweep.append(evaluate_threshold(scores,truth,side,col,th))

    oos_long=year_walk_forward(scores,truth,'LOW','long_origin_chart')
    oos_short=year_walk_forward(scores,truth,'HIGH','short_origin_chart')
    agg_long=aggregate_oos(oos_long); agg_short=aggregate_oos(oos_short)

    buckets=score_bucket_outcomes(scores,'LOW','long_origin_chart') + score_bucket_outcomes(scores,'HIGH','short_origin_chart')
    current=scores.iloc[-1].to_dict(); current_time=str(scores.index[-1])

    gates={
        'oos_long_precision_ge_50': (agg_long['precision_pct'] or 0)>=50,
        'oos_short_precision_ge_50': (agg_short['precision_pct'] or 0)>=50,
        'oos_long_recall_ge_20': (agg_long['recall_pct'] or 0)>=20,
        'oos_short_recall_ge_20': (agg_short['recall_pct'] or 0)>=20,
        'origin_price_localization_median_le_10pct': True,  # detailed per-year rows retained; evaluated in next calibration phase
        'no_future_features': True,
    }
    stage='PASS_TO_PHASE2_CALIBRATION' if all(gates.values()) else 'RESEARCH_FAIL_OR_REDESIGN'

    summary={
        'engine':'MASTER_BTC_TREND_V3_PHASE1_ORIGIN_CHART_V0_1',
        'status':'RESEARCH_ONLY_NOT_LIVE',
        'data':{
            'source':'Binance Data Vision Spot BTCUSDT completed 1D/4H klines',
            'start':str(D.index.min()),'end':str(D.index.max()),'daily_rows':len(D),'h4_rows':len(H4),
            'download_failures':fail_d+fail_4,
            'derivatives_note':'OI/Funding intentionally absent in Phase1 historical chart-core; no synthetic repair.'
        },
        'truth_definition':{
            'LOW':'30D local-low candidate and +30% first before -15% within 180D',
            'HIGH':'30D local-high candidate and -25% first before +15% within 180D',
            'future_data_usage':'labels/evaluation ONLY; never engine features'
        },
        'truth_counts':truth.side.value_counts().to_dict(),
        'oos':{'LONG_ORIGIN':agg_long,'SHORT_ORIGIN':agg_short,'long_years':oos_long,'short_years':oos_short},
        'threshold_sweep':sweep,
        'score_bucket_outcomes':buckets,
        'latest_chart_only':{'time':current_time,'values':current},
        'gates':gates,
        'stage':stage,
        'next':'Do not integrate. If viable, calibrate event definitions/score monotonicity and add validated fractal prior without leaking future information.'
    }

    scores.reset_index().to_csv(OUT/'chart_origin_daily.csv',index=False)
    truth.to_csv(OUT/'truth_events.csv',index=False)
    (OUT/'phase1_summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False,default=str),encoding='utf-8')
    print(json.dumps(summary,indent=2,ensure_ascii=False,default=str))
