"""Material controls: fill ordering, two-stage budget, funding and time travel."""
from pathlib import Path
import argparse,json
import numpy as np
import pandas as pd
from engine import Replay,load_data,shared_facts,metrics
from money_os_v4.workers.w5_trading_room.model import Config,risk_cost

def fixture(side=1):
 r=Replay.__new__(Replay);r.ts=pd.date_range('2024-01-01',periods=210,freq='15min',tz='UTC')
 r.a=np.tile([101.,102.,100.8,101.],(210,1)) if side==1 else np.tile([99.,99.2,98.,99.],(210,1))
 r.rows={};r.r4=np.zeros(210,dtype=int);r.fund_bar=np.zeros(210)
 r.fund=pd.DataFrame({'fundingRate':[0.]},index=[r.ts[0]])
 p=dict(side=side,entry=100.,stop=98. if side==1 else 102.,targets=[107.,108.,110.] if side==1 else [93.,92.,90.],
  atr=2.,planned_net_rr=4.,entry_zone_high=100.5,entry_zone_low=99.5)
 return r,p

def main():
 arg=argparse.ArgumentParser();arg.add_argument('--data',required=True);a=arg.parse_args();out=Path(a.data)
 cfg=Config('PULLBACK',1,.5,'BASIC','FULL',24);checks=[]
 for side in [1,-1]:
  r,p=fixture(side)
  r.a[1]=[101,111,97,101] if side==1 else [99,103,89,99]
  x=r.run_plan(0,p,cfg,save_legs=True)
  assert x['filled'] and x['outcome']=='SL' and abs(x['net_R']+.4)<1e-10
  assert x['ambiguous']==1
  checks.append(f'initial_{side}_stop_plus_cost_equals_40pct_budget_and_stop_first')
 r,p=fixture();r.a[1]=[101,102,99,101];r.a[2]=[101,111,100.8,110]
 x=r.run_plan(0,p,cfg,save_legs=True)
 qty=.4/(2+.0008*198)
 assert abs(x['net_R']-qty*(10-.0008*210))<1e-10
 checks.append('target_profit_independently_reconciled')
 r,p=fixture();x=r.run_plan(0,p,cfg)
 assert not x['filled'] and x['outcome']=='DAY_ORDER_EXPIRED' and x['net_R']==0
 checks.append('unfilled_plan_preserved_as_denominator')
 r,p=fixture();r.a[1]=[100.2,101,99.9,100.8];r.a[2]=[100.8,101,100,100.9]
 r.a[4]=[100.9,101,100.3,100.8];r.a[5]=[100.8,101,97,99]
 x=r.run_plan(0,p,cfg,save_legs=True)
 assert x['e2_filled'] and [e['bar'] for e in x['events'] if e['kind']=='ENTRY']==[1,4]
 assert abs(x['net_R']+1)<1e-10
 checks.append('E2_after_closed_reaction_hold_then_future_retest_100pct_budget')
 for side in [1,-1]:
  r,p=fixture(side);r.a[1]=[101,102,99,101] if side==1 else [99,101,98,99]
  r.a[2]=[101,111,100.8,110] if side==1 else [99,99.2,89,90]
  r.fund_bar[2]=1.;x=r.run_plan(0,p,cfg)
  assert abs(x['funding_R']-side*.4/risk_cost(100,p['stop']))<1e-10
  checks.append(f'funding_sign_and_quantity_{side}')
 raw,fund=load_data(out);cut=pd.Timestamp('2025-07-01T12:00:00Z')
 # Full cutoff and a later snapshot, independently rebuilt.
 older=raw[raw.index+pd.Timedelta(minutes=15)<=cut]
 later=raw[raw.index<cut+pd.Timedelta(days=10)]
 h1,h4=shared_facts(older,fund);f1,f4=shared_facts(later,fund)
 pd.testing.assert_frame_equal(h1,f1.loc[h1.index],check_freq=False)
 pd.testing.assert_frame_equal(h4,f4.loc[h4.index],check_freq=False)
 assert h1.loc[cut,'close']==raw.loc[cut-pd.Timedelta(minutes=15),'close']
 checks.append('prefix_invariance_including_confirmed_pivots_and_funding')
 result=dict(status='PASS',checks=checks,scope='synthetic event edge cases plus one independently rebuilt historical cutoff; not manual Champion parity')
 (out/'results/engine_validation.json').write_text(json.dumps(result,indent=2))
 print(json.dumps(result,indent=2))
if __name__=='__main__':main()
