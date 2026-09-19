from pathlib import Path
import argparse,json,datetime
import numpy as np
import pandas as pd
from engine import Replay,load_data,metrics
from money_os_v4.workers.w5_trading_room.model import Config,risk_cost,FEE,SLIP

SPLITS=[('TRAIN_2024','2024-01-01T00:00:00Z','2025-01-01T00:00:00Z'),('VALIDATION_2025','2025-01-01T00:00:00Z','2026-01-01T00:00:00Z'),('CHECK_2026_TO_SEP17','2026-01-01T00:00:00Z','2026-09-18T00:00:00Z')]

def baseline(replay,start,end,slip=SLIP):
 start=pd.Timestamp(start);end=pd.Timestamp(end);raw=replay.raw;a=replay.a;rows=[];until=0
 for i in replay.hour_indices:
  t=replay.ts[i]
  if t<start or t>=end-pd.Timedelta(hours=48) or i<until:continue
  f=replay.rows[int(i)]
  d=1 if f['regime4']==1 and f['slope4']>.1 and f['close']>f['ema20'] and f['ema20']>=f['ma50']*.99 else -1 if f['regime4']==-1 and f['slope4']<-.1 and f['close']<f['ema20'] and f['ema20']<=f['ma50']*1.01 else 0
  if not d:continue
  k=int(i)+1;ep=float(a[k,0]);stop=f['prior_lo12']-.1*f['atr'] if d==1 else f['prior_hi12']+.1*f['atr'];risk=d*(ep-stop)
  if risk<=0 or risk/ep>.06 or risk<.2*f['atr']:continue
  tp=ep+d*3*risk;quantity=1/risk_cost(ep,stop,slip);reason='TIME';j=k+95;xp=a[j,3]
  for b in range(k,k+96):
   op,hi,lo,cl=a[b]
   if lo<=stop if d==1 else hi>=stop:
    j=b;xp=min(stop,op) if d==1 else max(stop,op);reason='SL';break
   if hi>=tp if d==1 else lo<=tp:
    j=b;xp=tp;reason='TP3R';break
  # Market entry at the opening is known before all later settlements.
  ft=replay.fund[(replay.fund.index>replay.ts[k])&(replay.fund.index<replay.ts[j]+pd.Timedelta(minutes=15))]
  funding=d*quantity*float((ft.fundingRate*ft.markPrice).sum());gross=d*(xp-ep)*quantity
  fee=FEE*(ep+xp)*quantity;sc=slip*(ep+xp)*quantity
  rows.append(dict(config_id='LEGACY_PRICE_PROXY',issue_time=t,issue_i=int(i),side=d,entry=ep,stop=stop,tp1=tp,tp2=tp,tp3=tp,planned_net_rr=None,
   filled=True,entry_time=replay.ts[k],entry_i=k,exit_time=replay.ts[j]+pd.Timedelta(minutes=15),exit_i=j,fill_hours=.25,e2_filled=False,
   outcome=reason,gross_R=gross,fee_R=fee,slippage_R=sc,funding_R=funding,net_R=gross-fee-sc-funding,holding_hours=(j-k+1)/4))
  until=max(j+1,int(i)+24)
 return pd.DataFrame(rows)

def gate(m,min_trades):
 return dict(win_rate=bool(m.get('win_rate') is not None and m['win_rate']>=.5),payoff=bool(m.get('payoff') is not None and m['payoff']>=2),fill_rate=bool(m.get('fill_rate') is not None and m['fill_rate']>=.5),sample_size=bool(m.get('trades',0)>=min_trades),positive_expectancy=bool(m.get('mean_R') is not None and m['mean_R']>0))

def main():
 p=argparse.ArgumentParser();p.add_argument('--data',required=True);a=p.parse_args();root=Path(a.data)
 lock=json.loads((root/'provenance/final_selection_lock.json').read_text());cfg=Config(**lock['selected']['config'])
 raw,fund=load_data(root);r=Replay(raw,fund);allp=[];summaries=[];bootstrap=[];monthly=[];sensitivity=[]
 rng=np.random.default_rng(20260918)
 for name,start,end in SPLITS:
  for model in ['CHALLENGER','LEGACY_PRICE_PROXY']:
   q=r.run(cfg,start,end,save_legs=True) if model=='CHALLENGER' else baseline(r,start,end)
   q['split']=name;q['model']=model
   allp.append(q);m=metrics(q)
   days=int((pd.Timestamp(end)-pd.Timestamp(start)-pd.Timedelta(hours=48)).days)
   m['calendar_days']=days;m['plan_day_coverage']=m.get('issued_days',0)/days;m['plans_per_day']=m.get('plans',0)/days
   summaries.append(dict(split=name,model=model,**m))
   # Calendar weeks resampled as blocks; same subset membership retained.
   q['week']=((q.issue_time-pd.Timestamp(start)).dt.total_seconds()//(7*86400)).astype(int)
   groups=[g for _,g in q.groupby('week')]
   b=[]
   for _ in range(3000):
    indexes=rng.integers(0,len(groups),size=len(groups));z=pd.concat([groups[i] for i in indexes],ignore_index=True);mz=metrics(z)
    b.append([mz.get('win_rate'),mz.get('payoff'),mz.get('fill_rate'),mz.get('mean_R')])
   ar=np.array(b,float)
   for k,metric in enumerate(['win_rate','payoff','fill_rate','mean_R']):
    low,high=np.nanquantile(ar[:,k],[.025,.975]);bootstrap.append(dict(split=name,model=model,metric=metric,low=low,high=high,blocks=len(groups),resamples=3000))
   for month,g in q.groupby(q.issue_time.dt.strftime('%Y-%m')):monthly.append(dict(month=month,model=model,**metrics(g)))
   if model=='CHALLENGER':
    # Keep selected issued/filled plans fixed: extra execution cost only.
    t=q[q.filled].copy();t['net_R']=t.net_R-t.slippage_R
    stressq=pd.concat([t,q[~q.filled]],ignore_index=True)
    sensitivity.append(dict(split=name,stress='same_plan_same_qty_double_slippage',**metrics(stressq)))
    q.drop(columns=['week'],errors='ignore').to_json(root/'results'/f'challenger_{name}.json',orient='records',date_format='iso',indent=2)
  print('evaluated',name,flush=True)
 allplans=pd.concat(allp,ignore_index=True)
 allplans.drop(columns=['events','week'],errors='ignore').to_csv(root/'results/all_plans.csv',index=False)
 full=[]
 for model,g in allplans.groupby('model'):full.append(dict(split='ALL_MATURED_SPLITS',model=model,**metrics(g)))
 summaries+=full
 pd.DataFrame(summaries).to_csv(root/'results/summary.csv',index=False)
 pd.DataFrame(bootstrap).to_csv(root/'results/uncertainty.csv',index=False)
 pd.DataFrame(monthly).to_csv(root/'results/monthly.csv',index=False)
 pd.DataFrame(sensitivity).to_csv(root/'results/slippage_stress.csv',index=False)
 by={s['split']:s for s in summaries if s['model']=='CHALLENGER'}
 gates={name:gate(by[name],40 if name.startswith('CHECK') else 60) for name in ['VALIDATION_2025','CHECK_2026_TO_SEP17']}
 passed=all(all(v.values()) for v in gates.values())
 verdict=dict(evaluated_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),config_id=cfg.id,variants_tested=lock['variants_tested'],performance_pass=passed,
  verdict='SHADOW_CANDIDATE_ONLY' if passed else 'REJECT_FOR_ADOPTION',gates=gates,
  champion_actual_performance='N/A',runtime='NOT_ACTIVATED',production_weight=0,existing_champion='PRESERVE',selected_configuration=cfg.to_dict())
 (root/'results/verdict.json').write_text(json.dumps(verdict,indent=2))
 print(pd.DataFrame(summaries)[['split','model','plans','trades','win_rate','payoff','fill_rate','mean_R']].round(5).to_string(index=False),flush=True)
 print(json.dumps(verdict,indent=2),flush=True)
if __name__=='__main__':main()
