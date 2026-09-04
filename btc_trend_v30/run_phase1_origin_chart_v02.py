from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

from run_phase1_origin_chart import (
    load_interval, add_features, build_truth_events
)

OUT = Path('btc_trend_v30/output/phase1_v02')
OUT.mkdir(parents=True, exist_ok=True)


def enrich_context(d: pd.DataFrame, h4: pd.DataFrame):
    d=d.copy(); h4=h4.copy()
    for x in (d,h4):
        x['ret30']=x.close.pct_change(30 if x is d else 42)
        x['ret60']=x.close.pct_change(60 if x is d else 84)
        look180=180 if x is d else 270
        look90=90 if x is d else 135
        x['high180_prev']=x.high.shift(1).rolling(look180).max()
        x['low180_prev']=x.low.shift(1).rolling(look180).min()
        x['high90_prev']=x.high.shift(1).rolling(look90).max()
        x['low90_prev']=x.low.shift(1).rolling(look90).min()
        x['drawdown180']=1-x.close/x.high180_prev
        x['rally180']=x.close/x.low180_prev-1
        x['rsi20_min_prev']=x.rsi14.shift(1).rolling(20).min()
        x['rsi20_max_prev']=x.rsi14.shift(1).rolling(20).max()
        x['high6_prev']=x.high.shift(1).rolling(6).max()
        x['low6_prev']=x.low.shift(1).rolling(6).min()
    return d,h4


def latest_before(df: pd.DataFrame, ts: pd.Timestamp):
    s=df[df.index<=ts]
    return None if s.empty else s.iloc[-1]


def clip100(x): return max(0.0,min(100.0,float(x)))


def stage_scores(d: pd.DataFrame,h4: pd.DataFrame)->pd.DataFrame:
    rows=[]
    for t,r in d.iterrows():
        if pd.isna(r.sma200) or pd.isna(r.high180_prev) or pd.isna(r.low180_prev):
            continue
        r4=latest_before(h4,t+pd.Timedelta(hours=23,minutes=59))
        if r4 is None or pd.isna(r4.sma200): continue

        # A reversal origin must have an opposing prior trend. This prevents continuation days
        # from being misclassified as fresh origins.
        down_ctx=max(
            clip100((r.drawdown180-0.10)/0.35*100),
            clip100((-r.ret60-0.05)/0.35*100),
        )
        up_ctx=max(
            clip100((r.rally180-0.12)/0.70*100),
            clip100((r.ret60-0.08)/0.45*100),
        )

        near_low=clip100((0.20-max(0.0,r.close/r.low90_prev-1))/0.20*100)
        near_high=clip100((0.20-max(0.0,1-r.close/r.high90_prev))/0.20*100)

        wave_l=0.6*float(r.down_exhaust)+0.4*float(r4.down_exhaust)
        wave_s=0.6*float(r.up_exhaust)+0.4*float(r4.up_exhaust)

        # Divergence/recovery proxies use only prior/current information.
        mom_l=0
        mom_l+=35 if r.rsi14>=r.rsi20_min_prev+8 and r.close<=r.low20_prev*1.08 else (18 if r.rsi_delta5>0 else 0)
        mom_l+=30 if r.j_delta3>10 else (15 if r.j_delta3>0 else 0)
        mom_l+=35 if r4.rsi_delta5>0 and r4.j_delta3>0 else (18 if r4.rsi_delta5>0 else 0)
        mom_s=0
        mom_s+=35 if r.rsi14<=r.rsi20_max_prev-8 and r.close>=r.high20_prev*0.92 else (18 if r.rsi_delta5<0 else 0)
        mom_s+=30 if r.j_delta3<-10 else (15 if r.j_delta3<0 else 0)
        mom_s+=35 if r4.rsi_delta5<0 and r4.j_delta3<0 else (18 if r4.rsi_delta5<0 else 0)

        # Absorption / rejection: flow intensity relative to price close location.
        absorb_l=0
        absorb_l+=35 if r.taker_imb_3<0 and r.close_loc>=0.60 else (18 if r.close_loc>=0.60 else 0)
        absorb_l+=25 if r4.taker_imb_3<0 and r4.close_loc>=0.60 else (12 if r4.close_loc>=0.60 else 0)
        absorb_l+=20 if r.vol_z>=1 else (10 if r.vol_z>=0 else 0)
        absorb_l+=20 if r4.vol_z>=1 else (10 if r4.vol_z>=0 else 0)
        absorb_s=0
        absorb_s+=35 if r.taker_imb_3>0 and r.close_loc<=0.40 else (18 if r.close_loc<=0.40 else 0)
        absorb_s+=25 if r4.taker_imb_3>0 and r4.close_loc<=0.40 else (12 if r4.close_loc<=0.40 else 0)
        absorb_s+=20 if r.vol_z>=1 else (10 if r.vol_z>=0 else 0)
        absorb_s+=20 if r4.vol_z>=1 else (10 if r4.vol_z>=0 else 0)

        candle_l=0.45*float(r.candle_bull)+0.55*float(r4.candle_bull)
        candle_s=0.45*float(r.candle_bear)+0.55*float(r4.candle_bear)

        # Candidate score deliberately emphasizes location + exhaustion BEFORE full MA confirmation.
        cand_l=(0.22*down_ctx+0.23*near_low+0.20*wave_l+0.13*mom_l+0.12*absorb_l+0.10*candle_l)
        cand_s=(0.22*up_ctx+0.23*near_high+0.20*wave_s+0.13*mom_s+0.12*absorb_s+0.10*candle_s)

        # Confirmation score is separate: structure/MA/momentum/flow flip after the candidate.
        structure_l=0
        structure_l+=30 if r.close>r.high6_prev else (15 if r.close>r.ema20 else 0)
        structure_l+=25 if r4.close>r4.high6_prev else (12 if r4.close>r4.ema20 else 0)
        structure_l+=20 if r.close>r.open and r.close_loc>0.60 else 0
        structure_l+=15 if r4.close>r4.open and r4.close_loc>0.60 else 0
        structure_l+=10 if r.ret3>0 else 0
        structure_s=0
        structure_s+=30 if r.close<r.low6_prev else (15 if r.close<r.ema20 else 0)
        structure_s+=25 if r4.close<r4.low6_prev else (12 if r4.close<r4.ema20 else 0)
        structure_s+=20 if r.close<r.open and r.close_loc<0.40 else 0
        structure_s+=15 if r4.close<r4.open and r4.close_loc<0.40 else 0
        structure_s+=10 if r.ret3<0 else 0

        ma_l=35*float(r.close>r.ema20)+20*float(r.ema20_slope5>0)+15*float(r4.close>r4.ema20)+15*float(r4.ema20_slope5>0)+15*float(r.close>r.sma50)
        ma_s=35*float(r.close<r.ema20)+20*float(r.ema20_slope5<0)+15*float(r4.close<r4.ema20)+15*float(r4.ema20_slope5<0)+15*float(r.close<r.sma50)
        flow_l=40*float(r.taker_imb_3>r.taker_imb_6)+30*float(r4.taker_imb_3>r4.taker_imb_6)+30*float(r.vol_z>0 and r.close_loc>0.55)
        flow_s=40*float(r.taker_imb_3<r.taker_imb_6)+30*float(r4.taker_imb_3<r4.taker_imb_6)+30*float(r.vol_z>0 and r.close_loc<0.45)
        conf_l=0.45*structure_l+0.25*ma_l+0.15*mom_l+0.15*flow_l
        conf_s=0.45*structure_s+0.25*ma_s+0.15*mom_s+0.15*flow_s

        rows.append({
            'time':t,'close':float(r.close),
            'long_candidate':round(cand_l,3),'short_candidate':round(cand_s,3),
            'long_confirm':round(conf_l,3),'short_confirm':round(conf_s,3),
            'prior_down_context':round(down_ctx,2),'prior_up_context':round(up_ctx,2),
            'near_low':round(near_low,2),'near_high':round(near_high,2),
            'wave_l':round(wave_l,2),'wave_s':round(wave_s,2),
            'momentum_l':round(mom_l,2),'momentum_s':round(mom_s,2),
            'absorb_l':round(absorb_l,2),'absorb_s':round(absorb_s,2),
            'candle_l':round(candle_l,2),'candle_s':round(candle_s,2),
        })
    return pd.DataFrame(rows).set_index('time')


def build_two_stage_signals(s:pd.DataFrame,side:str,cand_th:float,conf_th:float)->list[dict]:
    cc='long_candidate' if side=='LOW' else 'short_candidate'
    cf='long_confirm' if side=='LOW' else 'short_confirm'
    signals=[]; last_confirm=None
    for t,r in s.iterrows():
        if r[cf]<conf_th: continue
        hist=s[(s.index>=t-pd.Timedelta(days=21))&(s.index<=t)]
        if hist.empty: continue
        best_t=hist[cc].idxmax(); best=hist.loc[best_t]
        if best[cc]<cand_th: continue
        # Candidate must have real prior opposite-trend context; not merely indicator noise.
        ctx=best.prior_down_context if side=='LOW' else best.prior_up_context
        loc=best.near_low if side=='LOW' else best.near_high
        if ctx<35 or loc<35: continue
        quality=0.60*best[cc]+0.40*r[cf]
        sig={'origin_time':best_t,'confirm_time':t,'origin_price':float(best.close),'confirm_price':float(r.close),
             'candidate':float(best[cc]),'confirmation':float(r[cf]),'quality':float(quality)}
        if last_confirm is None or (t-last_confirm).days>28:
            signals.append(sig); last_confirm=t
        elif quality>signals[-1]['quality']:
            signals[-1]=sig; last_confirm=t
    return signals


def evaluate(signals:list[dict],truth:pd.DataFrame,side:str)->dict:
    ts=truth[truth.side==side].copy(); matched=set(); good=0; lags=[]; perr=[]
    for sig in signals:
        ot=pd.Timestamp(sig['origin_time']); cp=sig['origin_price']
        cand=ts[(ts.time>=ot-pd.Timedelta(days=10))&(ts.time<=ot+pd.Timedelta(days=10))].copy()
        if cand.empty: continue
        cand['price_err']=(cp/cand.price-1).abs()*100
        cand=cand[cand.price_err<=15]
        if cand.empty: continue
        cand['dist']=(cand.time-ot).abs()
        tr=cand.sort_values(['dist','price_err']).iloc[0]; key=pd.Timestamp(tr.time)
        if key in matched: continue
        matched.add(key); good+=1
        lags.append((pd.Timestamp(sig['confirm_time'])-key).days)
        perr.append(float(tr.price_err))
    return {
        'signals':len(signals),'matched':good,'truth_events':len(ts),
        'precision_pct':round(100*good/len(signals),2) if signals else None,
        'recall_pct':round(100*good/len(ts),2) if len(ts) else None,
        'median_confirmation_lag_days':round(float(np.median(lags)),2) if lags else None,
        'median_origin_price_error_pct':round(float(np.median(perr)),2) if perr else None,
    }


def choose_threshold(train:pd.DataFrame,truth:pd.DataFrame,side:str):
    opts=[]
    for ct in [45,50,55,60,65,70]:
        for ft in [45,50,55,60,65,70]:
            sig=build_two_stage_signals(train,side,ct,ft); ev=evaluate(sig,truth,side)
            p=ev['precision_pct'] or 0; r=ev['recall_pct'] or 0
            f05=1.25*p*r/(0.25*p+r) if p>0 and r>0 else 0
            # Prefer smaller, more useful origin sets when objective ties.
            opts.append((f05,p,r,-len(sig),ct,ft))
    best=max(opts)
    return best[4],best[5]


def oos_by_year(s:pd.DataFrame,truth:pd.DataFrame,side:str):
    rows=[]
    for y in range(2020,2027):
        train=s[s.index.year<y]; test=s[s.index.year==y]
        tr=truth[truth.time.dt.year<y]; te=truth[truth.time.dt.year==y]
        if len(train)<500 or len(test)<100 or len(tr[tr.side==side])<3: continue
        ct,ft=choose_threshold(train,tr,side)
        sig=build_two_stage_signals(test,side,ct,ft); ev=evaluate(sig,te,side)
        ev.update({'year':y,'candidate_threshold':ct,'confirm_threshold':ft})
        rows.append(ev)
    return rows


def aggregate(rows):
    sig=sum(r['signals'] for r in rows); m=sum(r['matched'] for r in rows); tr=sum(r['truth_events'] for r in rows)
    return {'signals':sig,'matched':m,'truth_events':tr,'precision_pct':round(100*m/sig,2) if sig else None,
            'recall_pct':round(100*m/tr,2) if tr else None,'years':len(rows)}


def bucket_forward(s:pd.DataFrame,side:str):
    col='long_candidate' if side=='LOW' else 'short_candidate'; out=[]
    # A coarse event-outcome check; not a probability calibration.
    for lo,hi in [(0,39),(40,54),(55,69),(70,100)]:
        x=s[(s[col]>=lo)&(s[col]<=hi)]
        hits=0; evaln=0
        for t,r in x.iterrows():
            fut=D[(D.index>t)&(D.index<=t+pd.Timedelta(days=180))]
            if fut.empty: continue
            p=float(r.close); result=None
            for _,q in fut.iterrows():
                if side=='LOW':
                    a=q.high>=p*1.30; b=q.low<=p*0.85
                else:
                    a=q.low<=p*0.75; b=q.high>=p*1.15
                if a and b: result='AMBIG'; break
                if a: result='HIT'; break
                if b: result='ADVERSE'; break
            if result in ('HIT','ADVERSE'):
                evaln+=1; hits+=result=='HIT'
        out.append({'side':side,'bucket':f'{lo}-{hi}','n':len(x),'evaluable':evaln,
                    'desired_first_pct':round(100*hits/evaln,2) if evaln else None})
    return out


if __name__=='__main__':
    d0,fd=load_interval('1d'); h0,f4=load_interval('4h')
    D,H4=enrich_context(add_features(d0,'1D'),add_features(h0,'4H'))
    S=stage_scores(D,H4); T=build_truth_events(D); T['time']=pd.to_datetime(T.time,utc=True)
    ol=oos_by_year(S,T,'LOW'); os=oos_by_year(S,T,'HIGH')
    al,ash=aggregate(ol),aggregate(os)
    buckets=bucket_forward(S,'LOW')+bucket_forward(S,'HIGH')
    gates={
        'long_precision_ge_30':(al['precision_pct'] or 0)>=30,
        'short_precision_ge_30':(ash['precision_pct'] or 0)>=30,
        'long_recall_ge_25':(al['recall_pct'] or 0)>=25,
        'short_recall_ge_25':(ash['recall_pct'] or 0)>=25,
        'two_stage_origin_design':True,
        'prior_opposing_trend_required':True,
        'future_data_features_forbidden':True,
    }
    stage='PROMISING_CONTINUE_RESEARCH' if all(gates.values()) else 'RESEARCH_FAIL_OR_REDESIGN'
    summary={
        'engine':'MASTER_BTC_TREND_V3_PHASE1_ORIGIN_CHART_V0_2',
        'status':'RESEARCH_ONLY_NOT_LIVE',
        'design_change':'Split trend-origin candidate (location/exhaustion) from post-origin confirmation. Require prior opposing trend.',
        'data':{'start':str(D.index.min()),'end':str(D.index.max()),'daily_rows':len(D),'h4_rows':len(H4),'failures':fd+f4},
        'truth_counts':T.side.value_counts().to_dict(),
        'oos':{'LONG_ORIGIN':al,'SHORT_ORIGIN':ash,'long_years':ol,'short_years':os},
        'candidate_bucket_forward_check':buckets,
        'latest':{'time':str(S.index[-1]),**S.iloc[-1].to_dict()},
        'gates':gates,'stage':stage,
        'note':'30% precision gate is an interim Phase1 research gate for sparse exact origin episodes, not a live accuracy promise. Final probabilities require separate calibration.',
    }
    S.reset_index().to_csv(OUT/'origin_two_stage_daily.csv',index=False)
    T.to_csv(OUT/'truth_events.csv',index=False)
    (OUT/'phase1_v02_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2,default=str))
