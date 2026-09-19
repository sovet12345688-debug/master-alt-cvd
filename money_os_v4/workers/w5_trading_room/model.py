"""BTC daily challenger plan builder. Consumes completed shared facts only.

No data fetching, scheduling, alerts, account access or official-state writes.
This deterministic operationalization is not the discretionary legacy Champion.
"""
from dataclasses import dataclass,asdict
from itertools import product
import math

FEE=.0006
SLIP=.0002
TICK=.1

@dataclass(frozen=True)
class Config:
 family:str
 stop_bars:int
 depth:float
 quality:str
 exits:str
 holding_hours:int
 entry_mode:str='EARLY'
 @property
 def id(self):
  base=f'{self.family}_S{self.stop_bars}_D{int(self.depth*100)}_{self.quality}_{self.exits}_H{self.holding_hours}'
  return base if self.entry_mode=='EARLY' else base+'_CONFIRMED'
 def to_dict(self):return asdict(self)

def candidate_configs():
 return [Config(*v) for v in product(['PULLBACK','BREAKOUT','SWEEP'],[1,3,6],[.25,.5,.75],['BASIC','STRONG'],['FULL','SCALED'],[12,24])]

def confirmed_configs():
 return [Config(f,s,d,'BASIC',e,24,'CONFIRMED') for f,s,d,e in product(['PULLBACK','BREAKOUT','SWEEP'],[3,6,12],[.5,.75],['FULL','SCALED'])]

def risk_cost(entry,stop,slip=SLIP):
 return abs(entry-stop)+(FEE+slip)*(entry+stop)

def planned_rr(entry,stop,targets,side,exits,last_known_funding=0.,slip=SLIP):
 weights=[0.,0.,1.] if exits=='FULL' else [.2,.3,.5]
 average=sum(p*w for p,w in zip(targets,weights))
 reward=side*(average-entry)-(FEE+slip)*(entry+average)-3*abs(last_known_funding)*entry
 return reward/risk_cost(entry,stop,slip)

def build_plan(f,cfg):
 """f is a single time-consistent completed shared-fact snapshot."""
 av=float(f['atr']);close=float(f['close']);op=float(f['open'])
 for key in ['atr','open','high','low','close','ema20','rvol','slope4','prior_hi12','prior_lo12',f'lo{cfg.stop_bars}',f'hi{cfg.stop_bars}','last_funding']:
  if not math.isfinite(float(f[key])):return None
 if not math.isfinite(av) or av<=0 or not math.isfinite(float(f['ema20'])):return None
 span=float(f['high']-f['low'])
 if span<=0 or float(f['rvol'])<(.8 if cfg.quality=='BASIC' else 1.):return None
 r4=int(f['regime4']);side=0
 for d in [1,-1]:
  location=(close-f['low'])/span if d==1 else (f['high']-close)/span
  if d*(close-op)<=0 or location<.6:continue
  if r4==-d:continue
  flow=float(f['taker4']) if f.get('taker4') is not None else float('nan')
  if cfg.quality=='STRONG' and (r4!=d or d*f['slope4']<.1 or (math.isfinite(flow) and d*flow<=0)):continue
  if cfg.family=='PULLBACK':
   ok=(f['low']<=f['ema20']+.1*av and close>f['ema20']) if d==1 else (f['high']>=f['ema20']-.1*av and close<f['ema20'])
  elif cfg.family=='BREAKOUT':
   level=f['prior_hi12'] if d==1 else f['prior_lo12']
   ok=d*(close-level)>0 and d*(close-level)<=1.5*av
  else:
   level=f['prior_lo12'] if d==1 else f['prior_hi12']
   wick=(min(close,op)-f['low'])/span if d==1 else (f['high']-max(close,op))/span
   ok=(f['low']<level and close>level) if d==1 else (f['high']>level and close<level)
   ok=ok and wick>=.2
  if ok:side=d;break
 if not side:return None
 entry=f['low']+cfg.depth*(close-f['low']) if side==1 else f['high']-cfg.depth*(f['high']-close)
 if cfg.family=='BREAKOUT':
  level=f['prior_hi12'] if side==1 else f['prior_lo12']
  entry=max(level-.25*av,min(entry,level+.1*av)) if side==1 else min(level+.25*av,max(entry,level-.1*av))
 entry=math.floor(entry/TICK)*TICK if side==1 else math.ceil(entry/TICK)*TICK
 stop=f[f'lo{cfg.stop_bars}']-.1*av if side==1 else f[f'hi{cfg.stop_bars}']+.1*av
 stop=math.floor(stop/TICK)*TICK if side==1 else math.ceil(stop/TICK)*TICK
 distance=side*(entry-stop)
 if distance<.2*av or distance/entry>.06 or side*(close-entry)<TICK or abs(close-entry)>1.5*av:return None
 available=f['pivot_highs'] if side==1 else f['pivot_lows']
 levels=sorted((float(x) for x in available if side*(x-entry)>.2*av),reverse=side==-1)
 distinct=[]
 for price in levels:
  if not distinct or abs(price-distinct[-1])>.1*av:distinct.append(price)
  if len(distinct)==3:break
 if len(distinct)<3:return None
 rr=planned_rr(entry,stop,distinct,side,cfg.exits,float(f['last_funding']))
 if rr<3:return None
 return dict(side=side,entry=entry,stop=stop,targets=distinct,atr=av,planned_net_rr=rr,
  last_known_funding=float(f['last_funding']),trade_frame='1H',family=cfg.family,
  price_invalidation=stop,entry_zone_high=min(close,entry+.25*av) if side==1 else entry,
  entry_zone_low=entry if side==1 else max(close,entry-.25*av))
