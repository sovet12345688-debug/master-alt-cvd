from __future__ import annotations
import json, math, sys
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
import validation_protocol_v1_step2_r12_daily_scoring as s2
from r13 import r13_historical_diagnostic_replay as base
from r14.r14_engine import R14Engine, R14ContractError

OUT=HERE/'output/full_replay'; OUT.mkdir(parents=True,exist_ok=True)
OOS_START=base.OOS_START; OOS_END=base.OOS_END; END_EXCL=base.END_EXCL

def as_bool(s):
    if s.dtype==bool: return s
    return s.astype(str).str.lower().eq('true')

def signal_windows(g):
    g=g.sort_values('date').copy(); rows=list(g.itertuples(index=False)); out=[]
    expiry=pd.Timestamp(g.date.max())+pd.Timedelta(days=2)
    for j,s in enumerate(rows):
        known=pd.Timestamp(s.date)+pd.Timedelta(days=1)
        nxt=(pd.Timestamp(rows[j+1].date)+pd.Timedelta(days=1)) if j+1<len(rows) else expiry
        if known<min(nxt,expiry): out.append((s,known,min(nxt,expiry)))
    return out,expiry

def resolve_fixed_end(h1,entry_time,direction,stop,target):
    q=h1[(h1.time>=entry_time)&(h1.time<END_EXCL)]
    for _,r in q.iterrows():
        tp=float(r.high)>=target if direction=='long' else float(r.low)<=target
        sl=float(r.low)<=stop if direction=='long' else float(r.high)>=stop
        if tp and sl: return 'AMBIGUOUS_SAME_BAR',None,pd.Timestamp(r.time),None
        if tp: return 'TP_3R',3.0,pd.Timestamp(r.time),float(target)
        if sl: return 'SL_1R',-1.0,pd.Timestamp(r.time),float(stop)
    return 'CENSORED_UNRESOLVED',None,pd.NaT,None

def runner_resolve(h1,scored,entry_time,direction,entry,stop):
    q=h1[(h1.time>=entry_time)&(h1.time<END_EXCL)].copy()
    stop_time=pd.NaT
    for _,r in q.iterrows():
        hit=float(r.low)<=stop if direction=='long' else float(r.high)>=stop
        if hit: stop_time=pd.Timestamp(r.time); break
    d=scored.copy(); d['date']=pd.to_datetime(d['date'],utc=True); d['known']=d['date']+pd.Timedelta(days=1)
    d=d[(d['known']>entry_time)&(d['known']<END_EXCL)]
    exit_signal=None
    for _,r in d.iterrows():
        against=float(r.close)<float(r.ema20) if direction=='long' else float(r.close)>float(r.ema20)
        if against: exit_signal=pd.Timestamp(r.known); break
    exit_time=pd.NaT; exit_price=None; why='CENSORED_UNRESOLVED'
    if exit_signal is not None:
        ex=h1[h1.time>=exit_signal].sort_values('time').head(1)
        if len(ex): exit_time=pd.Timestamp(ex.iloc[0].time); exit_price=float(ex.iloc[0].open); why='DAILY_EMA20_EXIT'
    if pd.notna(stop_time) and (pd.isna(exit_time) or stop_time<exit_time): return 'RUNNER_SL',-1.0,stop_time,float(stop)
    if pd.notna(exit_time):
        rm=(exit_price-entry)/(entry-stop) if direction=='long' else (entry-exit_price)/(stop-entry)
        return why,float(rm),exit_time,float(exit_price)
    return why,None,pd.NaT,None

def favorable_progress_R(h1,entry_time,add_confirm,direction,entry,risk):
    q=h1[(h1.time>=entry_time)&((h1.time+pd.Timedelta(hours=1))<=add_confirm)]
    if q.empty or risk<=0: return 0.0
    fav=float(q.high.max())-entry if direction=='long' else entry-float(q.low.min())
    return max(0.0,fav/risk)

def scan_add(engine,h4,h1,*,engine_name,stage,direction,daily,start,end,zone_lo,zone_hi,daily_atr,initial_route,initial_resolved_time,initial_confirm,initial_entry,initial_risk):
    q=h4[(h4.time>=start)&(h4.time<end)].reset_index(drop=True)
    for i in range(len(q)-1):
        candidate=q.iloc[i].to_dict(); nxt=q.iloc[i+1].to_dict()
        if pd.Timestamp(nxt['time'])>=end: break
        dec=engine.initial.evaluate_pair(engine=engine_name,stage=stage,direction=direction,daily=daily,candidate=candidate,nxt=nxt,zone_lo=zone_lo,zone_hi=zone_hi,daily_atr=daily_atr)
        if dec.action!='ENTER': continue
        ctime=pd.Timestamp(nxt['time'])+pd.Timedelta(hours=4)
        prog=favorable_progress_R(h1,initial_confirm,ctime,direction,float(initial_entry),float(initial_risk))
        ok,why=engine.add_policy_pass(initial_route=initial_route,initial_resolved_time=initial_resolved_time,add_confirm_time=ctime,add_decision=dec,daily=daily,direction=direction,favorable_progress_R=prog)
        if ok: return dec,pd.Timestamp(candidate['time']),ctime,prog,why
    return None

def replay_episode(engine,e,g,scored,h1,h4):
    g=g.sort_values('date').copy(); firstsig=g.iloc[0]; zone_lo=float(firstsig.zone_lo); zone_hi=float(firstsig.zone_hi)
    direction=str(e.direction); engine_name=str(e.engine); wins,expiry=signal_windows(g); scout=pd.Timestamp(firstsig.date)+pd.Timedelta(days=1)
    one=None; one_stage=None
    for s,start,end in wins:
        stage=int(s.stage)
        if not engine.initial.execution_eligible(engine_name,stage): continue
        d=base.daily_snapshot(scored,int(s.idx))
        found=base.scan_window(engine.initial,h1,engine_name=engine_name,stage=stage,direction=direction,daily=d,start=start,end=end,zone_lo=zone_lo,zone_hi=zone_hi,daily_atr=float(scored.iloc[int(s.idx)].atr14),tf_hours=1)
        if found: one=found; one_stage=stage; break
    if one is None:
        return {'episode_id':str(e.independent_episode_id),'family_id':str(e.family_id),'engine':engine_name,'direction':direction,'entry_1h':False,'add_4h':False,'scout_known':scout,'expiry':expiry},[]
    dec,rtime,ctime=one
    fout,fR,frt,fprice=resolve_fixed_end(h1,ctime,direction,float(dec.stop),float(dec.target))
    rout,rR,rrt,rprice=runner_resolve(h1,scored,ctime,direction,float(dec.entry),float(dec.stop))
    legs=[{'episode_id':str(e.independent_episode_id),'family_id':str(e.family_id),'engine':engine_name,'direction':direction,'leg':'1H_FIXED','route':dec.route,'risk_state':dec.risk_state,'confirm_time':ctime,'entry':dec.entry,'stop':dec.stop,'target':dec.target,'risk':dec.risk,'outcome':fout,'unit_R':fR,'resolved_time':frt,'exit_price':fprice,'risk_weight_R':engine.initial_risk_R*engine.fixed_fraction},
          {'episode_id':str(e.independent_episode_id),'family_id':str(e.family_id),'engine':engine_name,'direction':direction,'leg':'1H_RUNNER','route':dec.route,'risk_state':dec.risk_state,'confirm_time':ctime,'entry':dec.entry,'stop':dec.stop,'target':None,'risk':dec.risk,'outcome':rout,'unit_R':rR,'resolved_time':rrt,'exit_price':rprice,'risk_weight_R':engine.initial_risk_R*engine.runner_fraction}]
    add=None
    for s,start,end in wins:
        stage=int(s.stage)
        if not engine.initial.execution_eligible(engine_name,stage): continue
        d=base.daily_snapshot(scored,int(s.idx)); wstart=max(start,ctime)
        if wstart>=end: continue
        found=scan_add(engine,h4,h1,engine_name=engine_name,stage=stage,direction=direction,daily=d,start=wstart,end=end,zone_lo=zone_lo,zone_hi=zone_hi,daily_atr=float(scored.iloc[int(s.idx)].atr14),initial_route=dec.route,initial_resolved_time=frt if pd.notna(frt) else None,initial_confirm=ctime,initial_entry=dec.entry,initial_risk=dec.risk)
        if found: add=found; break
    if add:
        d4,r4,c4,prog,why=add; aout,aR,art,aprice=resolve_fixed_end(h1,c4,direction,float(d4.stop),float(d4.target))
        legs.append({'episode_id':str(e.independent_episode_id),'family_id':str(e.family_id),'engine':engine_name,'direction':direction,'leg':'4H_ADD','route':d4.route,'risk_state':d4.risk_state,'confirm_time':c4,'entry':d4.entry,'stop':d4.stop,'target':d4.target,'risk':d4.risk,'outcome':aout,'unit_R':aR,'resolved_time':art,'exit_price':aprice,'risk_weight_R':engine.add_risk_R,'favorable_progress_R_before_add':prog,'add_reason':why})
    st={'episode_id':str(e.independent_episode_id),'family_id':str(e.family_id),'engine':engine_name,'direction':direction,'entry_1h':True,'add_4h':bool(add),'scout_known':scout,'expiry':expiry,'first_route':dec.route,'first_risk_state':dec.risk_state,'first_confirm_time':ctime,'first_stage':one_stage,'fixed_outcome':fout,'fixed_resolved_time':frt,'runner_outcome':rout,'runner_resolved_time':rrt}
    return st,legs

def horizon_mark(h1,horizon):
    q=h1[((h1.time+pd.Timedelta(hours=1))<=horizon)].sort_values('time')
    return float(q.iloc[-1].close) if len(q) else None

def directional_ret(direction,entry,px,start_close):
    if px is None or not np.isfinite(px): return 0.0
    return ((px-entry) if direction=='long' else (entry-px))/start_close

def mcr_capture(row,h1,horizon_days,fixed_frac,runner_frac):
    if not row.get('entry_1h',False) or pd.isna(row.first_entry): return 0.0
    horizon=pd.Timestamp(row.start_date)+pd.Timedelta(days=horizon_days)
    if pd.Timestamp(row.first_confirm_time)>horizon: return 0.0
    mark=horizon_mark(h1,horizon)
    def px(exit_time,exit_px):
        if pd.notna(exit_time) and pd.Timestamp(exit_time)<=horizon and pd.notna(exit_px): return float(exit_px)
        return mark
    pfix=px(row.fixed_resolved_time,row.fixed_exit_price); prun=px(row.runner_resolved_time,row.runner_exit_price)
    cap=fixed_frac*directional_ret(row.direction,float(row.first_entry),pfix,float(row.start_close))+runner_frac*directional_ret(row.direction,float(row.first_entry),prun,float(row.start_close))
    return max(0.0,cap)

def max_dd(rs):
    c=rs.cumsum(); peak=c.cummax(); dd=c-peak
    return float(dd.min()) if len(dd) else 0.0

def main():
    engine=R14Engine()
    req=[base.DATA1D,base.EP,base.SIG,base.TRUTH,base.H1_RAW,base.H4_RAW]
    if any(not p.exists() for p in req): raise SystemExit('BASELINE_OUTPUTS_MISSING')
    raw1d=pd.read_csv(base.DATA1D); scored=s2.build(raw1d); scored['date']=pd.to_datetime(scored['date'],utc=True)
    ep=pd.read_csv(base.EP); sig=pd.read_csv(base.SIG); truth=pd.read_csv(base.TRUTH); h1raw=pd.read_csv(base.H1_RAW); h4raw=pd.read_csv(base.H4_RAW)
    ep['start_date']=pd.to_datetime(ep.start_date,utc=True); sig['date']=pd.to_datetime(sig.date,utc=True); h1raw['time']=pd.to_datetime(h1raw.time,utc=True); h4raw['time']=pd.to_datetime(h4raw.time,utc=True)
    h1=base.enrich_tf(h1raw,1); h4=base.prepare_h4(h4raw,h1)
    states=[]; legs=[]
    for _,e in ep.iterrows():
        g=sig[(sig.family_id.astype(str)==str(e.family_id))&(sig.engine.astype(str)==str(e.engine))].copy()
        st,lg=replay_episode(engine,e,g,scored,h1,h4); states.append(st); legs.extend(lg)
    st=pd.DataFrame(states); lg=pd.DataFrame(legs)
    oos=ep[(ep.start_date>=OOS_START)&(ep.start_date<=OOS_END)].copy().rename(columns={'independent_episode_id':'episode_id'}); oos['episode_id']=oos.episode_id.astype(str)
    ids=set(oos.episode_id); st=st[st.episode_id.astype(str).isin(ids)].copy(); lg=lg[lg.episode_id.astype(str).isin(ids)].copy()
    t=truth.rename(columns={'independent_episode_id':'episode_id'}).copy(); t['episode_id']=t.episode_id.astype(str)
    for c in ['available_90d','available_365d']: t[c]=as_bool(t[c])
    fixed=lg[lg.leg.eq('1H_FIXED')].sort_values('confirm_time').drop_duplicates('episode_id').copy(); runner=lg[lg.leg.eq('1H_RUNNER')].sort_values('confirm_time').drop_duplicates('episode_id').copy(); adds=lg[lg.leg.eq('4H_ADD')].copy()
    fk=fixed[['episode_id','confirm_time','entry','stop','target','risk','outcome','unit_R','resolved_time','exit_price','route','risk_state']].rename(columns={c:'first_'+c for c in ['confirm_time','entry','stop','target','risk','outcome','unit_R','resolved_time','exit_price','route','risk_state']})
    rk=runner[['episode_id','outcome','unit_R','resolved_time','exit_price']].rename(columns={c:'runner_'+c for c in ['outcome','unit_R','resolved_time','exit_price']})
    m=oos.merge(st,on=['episode_id','family_id','engine','direction'],how='left',validate='one_to_one').merge(t[['episode_id','available_90d','available_365d','medium_truth_status','long_truth_status','MFE_90d','MFE_365d']],on='episode_id',how='left',validate='one_to_one').merge(fk,on='episode_id',how='left',validate='one_to_one').merge(rk,on='episode_id',how='left',validate='one_to_one')
    m['entry_1h']=m.entry_1h.fillna(False).astype(bool); m['add_4h']=m.add_4h.fillna(False).astype(bool)
    for c in ['first_confirm_time','first_resolved_time','runner_resolved_time']: m[c]=pd.to_datetime(m[c],utc=True,errors='coerce')
    # episode weighted R
    lr=lg.copy(); lr['weighted_R']=pd.to_numeric(lr.unit_R,errors='coerce')*pd.to_numeric(lr.risk_weight_R,errors='coerce')
    episode_R=lr.groupby('episode_id').weighted_R.sum(min_count=1).rename('portfolio_net_R'); m=m.merge(episode_R,on='episode_id',how='left'); m.loc[~m.entry_1h,'portfolio_net_R']=0.0
    m['fixed_exit_price']=m.first_exit_price; m['fixed_resolved_time']=m.first_resolved_time
    m['runner_exit_price']=m.runner_exit_price
    med=m.medium_truth_status.isin(['MEDIUM_SUCCESS_20','MEDIUM_SUCCESS_30']); lng=m.long_truth_status.isin(['LONG_SUCCESS_PRIMARY','LONG_SUCCESS_EXTENSION'])
    m['confirmed_false_start_90d']=m.first_outcome.eq('SL_1R')&m.available_90d&~med
    m['missed_medium']=(~m.entry_1h)&m.available_90d&med; m['missed_long']=(~m.entry_1h)&m.available_365d&lng
    m['capture90']=m.apply(lambda r:mcr_capture(r,h1,90,engine.fixed_fraction,engine.runner_fraction),axis=1); m['capture365']=m.apply(lambda r:mcr_capture(r,h1,365,engine.fixed_fraction,engine.runner_fraction),axis=1)
    m['mcr90']=np.where(m.available_90d&med&pd.to_numeric(m.MFE_90d,errors='coerce').gt(0),np.minimum(1,m.capture90/pd.to_numeric(m.MFE_90d,errors='coerce')),np.nan)
    m['mcr365']=np.where(m.available_365d&lng&pd.to_numeric(m.MFE_365d,errors='coerce').gt(0),np.minimum(1,m.capture365/pd.to_numeric(m.MFE_365d,errors='coerce')),np.nan)
    exec90=m.entry_1h&m.available_90d; fsn=int(m.confirmed_false_start_90d.sum()); fsd=int(exec90.sum())
    dir_exp={}
    for d,q in m[m.entry_1h].groupby('direction'): dir_exp[str(d)]={'n':int(len(q)),'mean_R':float(q.portfolio_net_R.mean()),'sum_R':float(q.portfolio_net_R.sum())}
    resolved_episode=m[m.entry_1h].sort_values('first_confirm_time'); gross_pos=float(resolved_episode.portfolio_net_R[resolved_episode.portfolio_net_R>0].sum()); gross_neg=float(-resolved_episode.portfolio_net_R[resolved_episode.portfolio_net_R<0].sum())
    summary={
      'status':'R14_FULL_HISTORICAL_DIAGNOSTIC_ONLY',
      'episodes':int(len(m)),'first_entries':int(m.entry_1h.sum()),'adds_4h':int(m.add_4h.sum()),
      'portfolio':{'net_R':float(resolved_episode.portfolio_net_R.sum()),'mean_R_per_entered_episode':float(resolved_episode.portfolio_net_R.mean()),'directional':dir_exp,'capture_to_loss_ratio':gross_pos/gross_neg if gross_neg else None,'raw_episode_order_mdd_R':max_dd(resolved_episode.portfolio_net_R)},
      'initial_management':{'fixed_half':engine.fixed_fraction,'runner_half':engine.runner_fraction,'initial_risk_R':engine.initial_risk_R,'add_risk_R':engine.add_risk_R},
      'false_start':{'n':fsn,'denominator':fsd,'rate':fsn/fsd if fsd else None,'gate_max':engine.cfg['predeclared_gates']['confirmed_false_start_rate_max']},
      'missed_trend':{'medium_truth_positive':int((m.available_90d&med).sum()),'medium_missed':int(m.missed_medium.sum()),'medium_missed_rate':float(m.missed_medium.sum()/(m.available_90d&med).sum()),'long_truth_positive':int((m.available_365d&lng).sum()),'long_missed':int(m.missed_long.sum()),'long_missed_rate':float(m.missed_long.sum()/(m.available_365d&lng).sum())},
      'mcr':{'90d_mean':float(pd.to_numeric(m.mcr90,errors='coerce').mean()),'90d_n':int(pd.to_numeric(m.mcr90,errors='coerce').notna().sum()),'365d_mean':float(pd.to_numeric(m.mcr365,errors='coerce').mean()),'365d_n':int(pd.to_numeric(m.mcr365,errors='coerce').notna().sum())},
    }
    g=engine.cfg['predeclared_gates']; gates={
      'LONG_PORTFOLIO_EXPECTANCY_POSITIVE':'PASS' if dir_exp.get('long',{}).get('mean_R',-999)>g['long_portfolio_expectancy_gt_R'] else 'FAIL',
      'SHORT_PORTFOLIO_EXPECTANCY_POSITIVE':'PASS' if dir_exp.get('short',{}).get('mean_R',-999)>g['short_portfolio_expectancy_gt_R'] else 'FAIL',
      'FALSE_START_CONTROL':'PASS' if summary['false_start']['rate']<=g['confirmed_false_start_rate_max'] else 'FAIL',
      'MCR_90D_GE_20PCT':'PASS' if summary['mcr']['90d_mean']>=g['mcr_90d_mean_min'] else 'FAIL',
      'MCR_365D_GE_20PCT':'PASS' if summary['mcr']['365d_mean']>=g['mcr_365d_mean_min'] else 'FAIL',
      'CAPTURE_TO_LOSS_GT_1':'PASS' if summary['portfolio']['capture_to_loss_ratio'] and summary['portfolio']['capture_to_loss_ratio']>g['capture_to_loss_ratio_gt'] else 'FAIL',
      'ROBUSTNESS':'UNRESOLVED_PENDING_PREDECLARED_BATTERY',
      'CYCLE_INDEPENDENCE':'UNRESOLVED_PENDING_R14_CYCLE_REPLAY',
      'SCORE_MONOTONICITY':'UNRESOLVED_PENDING_R14_BIN_AUDIT',
      'FULL_RISK_GOVERNOR':'UNRESOLVED_EXTERNAL_SEVERE_RISK_NA',
      'EXACT_V2_6_H2H':'LINK_NA_EXACT_BASELINE_NOT_REPLAYABLE',
      'FORWARD_UNTOUCHED_OOS':'UNRESOLVED_IMMATURE_FROM_2026_09_05'
    }
    hard=[k for k,v in gates.items() if v=='FAIL']; unresolved=[k for k,v in gates.items() if v.startswith('UNRESOLVED') or v.startswith('LINK_NA')]
    final={'summary':summary,'gates':gates,'hard_failures':hard,'unresolved_blockers':unresolved,'promotion_decision':'HOLD' if hard or unresolved else 'PASS','production_promotion_evidence':False,'r14_config_sha256':engine.man['canonical_config_sha256']}
    st.to_csv(OUT/'r14_episode_state.csv',index=False); lg.to_csv(OUT/'r14_trade_legs.csv',index=False); m.to_csv(OUT/'r14_episode_metrics.csv',index=False); (OUT/'audit.json').write_text(json.dumps(final,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(final,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
