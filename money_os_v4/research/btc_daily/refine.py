from pathlib import Path
import argparse,json,hashlib,datetime
import pandas as pd
from engine import Replay,load_data,metrics
from search import attainment
from money_os_v4.workers.w5_trading_room.model import confirmed_configs

def main():
 p=argparse.ArgumentParser();p.add_argument('--data',required=True);a=p.parse_args();root=Path(a.data)
 raw,fund=load_data(root);cut=pd.Timestamp('2026-01-01',tz='UTC')
 r=Replay(raw[raw.index<cut],fund[fund.index<cut]);rows=[]
 for cfg in confirmed_configs():
  mt=metrics(r.run(cfg,'2024-01-01T00:00:00Z','2025-01-01T00:00:00Z'))
  mv=metrics(r.run(cfg,'2025-01-01T00:00:00Z','2026-01-01T00:00:00Z'))
  enough=mt.get('trades',0)>=60 and mv.get('trades',0)>=60
  eligible=bool(enough and mt['mean_R']>0 and mv['mean_R']>0 and mt['fill_rate']>=.5 and mv['fill_rate']>=.5)
  rows.append(dict(config_id=cfg.id,config=cfg.to_dict(),enough=bool(enough),eligible=eligible,attainment=min(attainment(mt),attainment(mv)),mean_floor=min(mt.get('mean_R') or -999,mv.get('mean_R') or -999),train=mt,validation=mv))
 (root/'results/refinement_all.json').write_text(json.dumps(rows,indent=2,allow_nan=False))
 combined=json.loads((root/'results/search_all.json').read_text())+rows
 pool=[x for x in combined if x['eligible']] or [x for x in combined if x['enough']] or combined
 pool=sorted(pool,key=lambda x:(-x['attainment'],-x['mean_floor'],-min(x['train']['trades'],x['validation']['trades']),x['config_id']))
 lock=dict(frozen_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),selection_scope='2024+2025 only; no 2026 candidate results inspected',variants_tested=len(combined),eligible_count=sum(x['eligible'] for x in combined),adequate_sample_count=sum(x['enough'] for x in combined),selected=pool[0])
 lock['refinement_sha256']=hashlib.sha256((root/'results/refinement_all.json').read_bytes()).hexdigest()
 (root/'provenance/final_selection_lock.json').write_text(json.dumps(lock,indent=2))
 print(json.dumps(lock,indent=2),flush=True)
 flat=[dict(config_id=x['config_id'],**x['config'],eligible=x['eligible'],enough=x['enough'],attainment=x['attainment'],**{f'{sp}_{k}':v for sp in ['train','validation'] for k,v in x[sp].items()}) for x in combined]
 pd.DataFrame(flat).to_csv(root/'results/all_252_variants.csv',index=False)
 print('REFINEMENT COMPLETE',flush=True)
if __name__=='__main__':main()
