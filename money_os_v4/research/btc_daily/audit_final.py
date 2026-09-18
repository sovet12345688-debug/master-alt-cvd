from pathlib import Path
from datetime import timedelta
import argparse,json
import numpy as np
import pandas as pd
from engine import Replay,load_data,metrics
from validate_engine import fixture
from money_os_v4.workers.w5_trading_room.model import Config
from money_os_v4.workers.w5_trading_room.worker import evaluate,final_navigator_downgrade,trading_trigger_alert_allowed

def main():
 p=argparse.ArgumentParser();p.add_argument('--data',required=True);a=p.parse_args();root=Path(a.data)
 raw,fund=load_data(root);r=Replay(raw,fund)
 checked=0
 for file in (root/'results').glob('challenger_*.json'):
  for plan in json.loads(file.read_text()):
   if not plan['filled']:continue
   entries={e['stage']:e for e in plan['events'] if e['kind']=='ENTRY'}
   exits=[e for e in plan['events'] if e['kind']=='EXIT']
   gross=fee=slip=funding=0.
   for e in entries.values():fee+=.0006*e['price']*e['quantity'];slip+=.0002*e['price']*e['quantity']
   for e in exits:
    entry=entries[e['stage']];qty=e['quantity'];gross+=plan['side']*(e['price']-entry['price'])*qty
    fee+=.0006*e['price']*qty;slip+=.0002*e['price']*qty
    # Per exited portion, independently rebuild holding-settlement membership.
    start=entry['bar'];end=e['bar']
    signed=plan['side']*r.fund_bar[start:end+1].copy();signed[0]=max(0.,signed[0])
    funding+=signed.sum()*qty
   for calculated,key in [(gross,'gross_R'),(fee,'fee_R'),(slip,'slippage_R'),(funding,'funding_R'),(gross-fee-slip-funding,'net_R')]:
    assert np.isclose(calculated,plan[key],atol=1e-8), (file.name,key,calculated,plan[key])
   assert plan['entry_i']>plan['issue_i'] and plan['fill_hours']<24
   assert plan['holding_hours']<=24
   if plan['e2_filled']:
    e2=entries['E2'];assert e2['bar']>=plan['e2_trigger_i']+3
   checked+=1
 allp=pd.read_csv(root/'results/all_plans.csv',parse_dates=['issue_time','exit_time'])
 for (model,split),g in allp.groupby(['model','split']):
  g=g.sort_values('issue_time');assert (g.issue_time.iloc[1:].to_numpy()>=g.exit_time.iloc[:-1].to_numpy()).all()
 s=pd.read_csv(root/'results/summary.csv')
 for (model,split),g in allp.groupby(['model','split']):
  m=metrics(g);expected=s[(s.model==model)&(s.split==split)].iloc[0]
  for field in ['plans','trades','win_rate','payoff','fill_rate','mean_R']:assert np.isclose(m[field],expected[field])
 # New confirmation mode: no trigger means no fill; trigger+hold cannot fill early.
 cfg=Config('PULLBACK',3,.5,'BASIC','FULL',24,'CONFIRMED')
 f,plan=fixture();q=f.run_plan(0,plan,cfg);assert not q['filled']
 f,plan=fixture();f.a[1]=[100.2,101,99.9,100.8];f.a[2]=[100.8,101,100,100.9];f.a[4]=[100.9,101,100.3,100.8];f.a[5]=[100.8,101,97,99]
 q=f.run_plan(0,plan,cfg,save_legs=True);assert q['entry_i']==4 and abs(q['net_R']+.4)<1e-9
 # Worker fail-closed boundaries and actual interface output from a historical fixture.
 selected=json.loads((root/'results/verdict.json').read_text())
 first=allp[(allp.model=='CHALLENGER')&allp.filled].iloc[0];t=first.issue_time
 facts=dict(h1=r.h1.loc[t].to_dict(),closed_asof=t.isoformat(),completed_candles=True,
  current={'price':float(r.h1.loc[t,'close']),'asof':t.isoformat()},severe_risk_veto=False,core_source_health='OK')
 req=dict(request_id='HISTORICAL_TEST_FIXTURE_ONLY',strategy_type='BTC_TRADE',asset='BTCUSDT',request_mode='PLAN',permission='ALLOW')
 out=evaluate(req,facts,selected,now=t.to_pydatetime()+timedelta(seconds=1))
 assert out['status']=='WAIT' and out['plan'] is not None and not trading_trigger_alert_allowed(out,'ENTER')
 assert final_navigator_downgrade('WAIT','ENTER')=='WAIT'
 assert final_navigator_downgrade('ENTER','ENTER',True)=='AVOID'
 bad=dict(facts,severe_risk_veto=True);assert 'RISK_VETO_OR_UNKNOWN' in evaluate(req,bad,selected,now=t.to_pydatetime())['reasons']
 bad=dict(facts,current={'price':float('nan'),'asof':t.isoformat()});assert evaluate(req,bad,selected,now=t.to_pydatetime())['plan'] is None
 assert evaluate(dict(req,final_entry=1),facts,selected,now=t.to_pydatetime())['reasons']==['NAVIGATOR_MUST_NOT_PRECOMPUTE_W5_PLAN']
 optional=dict(facts,h1=dict(facts['h1'],last_funding=None,taker4=None))
 o=evaluate(req,optional,selected,now=t.to_pydatetime());assert 'MANDATORY_CORE_INVALID' not in o['reasons'] and o['cost_assumptions']['funding_observed'] is None
 (root/'results/worker_example_historical.json').write_text(json.dumps(out,indent=2,default=str))
 result=dict(status='PASS',plans_independently_reconciled=checked,checks=['entry and hold timing','E2 strictly after closed confirmation','no position overlap','recomputed KPI denominators','fill-by-fill gross fees slippage funding','confirmed E1 gating','Navigator cannot upgrade','risk and invalid Current blocked','optional NA distinct from zero','no live trading or alert capability'])
 (root/'results/final_validation.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if __name__=='__main__':main()
