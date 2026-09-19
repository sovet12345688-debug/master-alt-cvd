from pathlib import Path
import argparse,json,hashlib,datetime
import numpy as np
import pandas as pd
from engine import Replay,load_data,metrics
from money_os_v4.workers.w5_trading_room.model import candidate_configs

def attainment(m):
 if not m.get('trades') or m.get('payoff') is None:return -1.
 return min(m['win_rate']/.5,m['payoff']/2,m['fill_rate']/.5)

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--data',required=True);a=parser.parse_args();root=Path(a.data).resolve()
 raw,fund=load_data(root)
 # Do not give the search replay any 2026 observations.
 cut=pd.Timestamp('2026-01-01',tz='UTC');raw=raw[raw.index<cut];fund=fund[fund.index<cut]
 replay=Replay(raw,fund);rows=[]
 for i,cfg in enumerate(candidate_configs()):
  train=replay.run(cfg,'2024-01-01T00:00:00Z','2025-01-01T00:00:00Z')
  val=replay.run(cfg,'2025-01-01T00:00:00Z','2026-01-01T00:00:00Z')
  mt=metrics(train);mv=metrics(val)
  enough=mt.get('trades',0)>=60 and mv.get('trades',0)>=60
  eligible=bool(enough and mt['mean_R']>0 and mv['mean_R']>0 and mt['fill_rate']>=.5 and mv['fill_rate']>=.5)
  floor=min(attainment(mt),attainment(mv));mean_floor=min(mt.get('mean_R') or -999,mv.get('mean_R') or -999)
  rows.append(dict(config_id=cfg.id,config=cfg.to_dict(),enough=bool(enough),eligible=eligible,attainment=floor,mean_floor=mean_floor,train=mt,validation=mv))
  if (i+1)%18==0:print('evaluated',i+1,'of',len(candidate_configs()),flush=True)
 (root/'results/search_all.json').write_text(json.dumps(rows,indent=2,allow_nan=False))
 flat=[dict(config_id=r['config_id'],**r['config'],eligible=r['eligible'],enough=r['enough'],attainment=r['attainment'],**{f'{p}_{k}':v for p in ['train','validation'] for k,v in r[p].items()}) for r in rows]
 pd.DataFrame(flat).to_csv(root/'results/search_all.csv',index=False)
 pool=[r for r in rows if r['eligible']] or [r for r in rows if r['enough']] or rows
 ranked=sorted(pool,key=lambda r:(-r['attainment'],-r['mean_floor'],-min(r['train']['trades'],r['validation']['trades']),r['config_id']))
 selected=ranked[0]
 lock={'frozen_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'selection_scope':'2024 train and 2025 validation only; no 2026 rows available to search replay','variants_tested':len(rows),'eligible_count':sum(r['eligible'] for r in rows),'adequate_sample_count':sum(r['enough'] for r in rows),'selected':selected}
 lock['search_sha256']=hashlib.sha256((root/'results/search_all.json').read_bytes()).hexdigest()
 (root/'provenance/selection_lock.json').write_text(json.dumps(lock,indent=2))
 print(json.dumps(lock,indent=2),flush=True)
 print('TOP5',json.dumps(ranked[:5],indent=2),flush=True)
if __name__=='__main__':main()
