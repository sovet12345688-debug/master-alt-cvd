"""Causal public-fact replay with plan-level 40/60 risk accounting."""
from pathlib import Path
import sys,json
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from money_os_v4.workers.w5_trading_room.model import Config,build_plan,risk_cost,planned_rr,FEE,SLIP,TICK

def aggregate(raw,freq,count):
 x=raw.copy();x.index=x.index+pd.Timedelta(minutes=15)
 a=x.resample(freq,label='right',closed='right').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum','quote_volume':'sum','taker_buy_quote':'sum'})
 n=x.close.resample(freq,label='right',closed='right').count()
 return a[n==count].copy()

def enrich(x):
 x=x.copy();p=x.close.shift(1)
 tr=pd.concat([x.high-x.low,(x.high-p).abs(),(x.low-p).abs()],axis=1).max(axis=1)
 x['atr']=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
 x['ema20']=x.close.ewm(span=20,adjust=False).mean();x['ma50']=x.close.rolling(50).mean()
 x['slope']=(x.ema20-x.ema20.shift(3))/x.atr
 x['rvol']=x.quote_volume/x.quote_volume.shift(1).rolling(20).mean()
 x['taker4']=(2*x.taker_buy_quote-x.quote_volume).rolling(4).sum()/x.quote_volume.rolling(4).sum()
 for n in [1,3,6,12]:x[f'lo{n}']=x.low.rolling(n).min();x[f'hi{n}']=x.high.rolling(n).max()
 x['prior_hi12']=x.high.shift(1).rolling(12).max();x['prior_lo12']=x.low.shift(1).rolling(12).min()
 return x

def shared_facts(raw,fund):
 h1=enrich(aggregate(raw,'1h',4));h4=enrich(aggregate(raw,'4h',16))
 h4['regime4']=np.where((h4.ema20>h4.ma50)&(h4.close>h4.ema20),1,np.where((h4.ema20<h4.ma50)&(h4.close<h4.ema20),-1,0))
 context=h4[['regime4','slope']].rename(columns={'slope':'slope4'}).reindex(h1.index,method='ffill')
 h1=h1.join(context);h1['last_funding']=fund.fundingRate.reindex(h1.index,method='ffill')
 # Confirm pivots only at closure of the second right-hand 4H candle.
 events=[]
 for i in range(4,len(h4)):
  k=i-2
  if h4.high.iloc[k]>h4.high.iloc[i-4:k].max() and h4.high.iloc[k]>=h4.high.iloc[k+1:i+1].max():events.append((h4.index[i],1,float(h4.high.iloc[k])))
  if h4.low.iloc[k]<h4.low.iloc[i-4:k].min() and h4.low.iloc[k]<=h4.low.iloc[k+1:i+1].min():events.append((h4.index[i],-1,float(h4.low.iloc[k])))
 events.sort();highs=[];lows=[];active=[];k=0
 for t in h1.index:
  while k<len(events) and events[k][0]<=t:active.append(events[k]);k+=1
  active=[e for e in active if t-e[0]<=pd.Timedelta(days=90)]
  highs.append([e[2] for e in active if e[1]==1]);lows.append([e[2] for e in active if e[1]==-1])
 h1['pivot_highs']=highs;h1['pivot_lows']=lows
 return h1,h4

class Replay:
 def __init__(self,raw,fund,facts=None):
  self.raw=raw;self.fund=fund
  self.h1,self.h4=shared_facts(raw,fund) if facts is None else facts
  self.ts=raw.index;self.a=raw[['open','high','low','close']].to_numpy()
  self.rows={int(raw.index.searchsorted(t)):r for t,r in zip(self.h1.index,self.h1.to_dict('records')) if t<raw.index[-1]}
  self.hour_indices=np.array(sorted(self.rows))
  # Funding credited/debited only for actual held research quantity.
  self.fund_bar=np.zeros(len(raw));self.fund_rate=np.zeros(len(raw))
  for t,r in fund.iterrows():
   j=raw.index.searchsorted(t,side='right')-1
   if j>=0 and pd.notna(r.markPrice):self.fund_bar[j]+=float(r.fundingRate*r.markPrice)
  self.r4=self.h1.regime4.reindex(raw.index,method='ffill').fillna(0).to_numpy(int)

 def run_plan(self,issue_i,p,cfg,slip=SLIP,save_legs=False):
  a=self.a;side=p['side'];ep=p['entry'];stop=p['stop'];targets=p['targets']
  issue_t=self.ts[issue_i];deadline=issue_i+96
  first=None;legs=[];gross=fees=slippage=funding=0.;add_index=None;add_price=None;add_filled=False
  target_seen=False;ambiguous=0;events=[];outcome='UNFILLED';last_i=deadline-1
  trigger_i=None;e1_trigger_i=None;e2_trigger_i=None;touch_i=None;last_validated=issue_i
  e1_activation=issue_i+1 if cfg.entry_mode=='EARLY' else None
  exit_weights=[0.,0.,1.] if cfg.exits=='FULL' else [.2,.3,.5]
  j=issue_i+1
  def close_leg(leg,qty,xp,k,reason):
   nonlocal gross,fees,slippage
   gross+=side*(xp-leg['entry'])*qty;fees+=FEE*xp*qty;slippage+=slip*xp*qty
   leg['remaining']-=qty
   events.append(dict(kind='EXIT',bar=k,stage=leg['stage'],price=xp,quantity=qty,reason=reason))
  while j<len(a):
   op,hi,lo,cl=a[j]
   if first is None:
    if j>=deadline:outcome='DAY_ORDER_EXPIRED';last_i=j-1;break
    if j in self.rows:
     last_validated=j
     if self.r4[j]==-side:outcome='REVALIDATION_CANCEL';last_i=j;break
    if side*(op-stop)<=0:outcome='PRICE_INVALID_BEFORE_FILL';last_i=j;break
    if e1_activation is None and j>=issue_i+3:
     k=j-2;ro,rh,rl,rc=a[k];ho,hh,hl,hc=a[k+1];rg=max(rh-rl,1e-9)
     wick=(min(ro,rc)-rl)/rg if side==1 else (rh-max(ro,rc))/rg
     reaction=side*(rc-ro)>0 and side*(rc-ep)>=0 and wick>=.2
     hold=(hl>=rl and hc>=ep) if side==1 else (hh<=rh and hc<=ep)
     near=rl<=p['entry_zone_high'] if side==1 else rh>=p['entry_zone_low']
     if reaction and hold and near:
      ae=min(rc,p['entry_zone_high']) if side==1 else max(rc,p['entry_zone_low'])
      ae=max(ep,ae) if side==1 else min(ep,ae)
      ae=np.floor(ae/TICK)*TICK if side==1 else np.ceil(ae/TICK)*TICK
      rate=float(self.fund.fundingRate.asof(self.ts[j]))
      if planned_rr(ae,stop,targets,side,cfg.exits,rate,slip)>=3:
       ep=float(ae);e1_activation=j+1;trigger_i=k;e1_trigger_i=k;touch_i=k
    touched=lo<=ep-TICK if side==1 else hi>=ep+TICK
    touched=touched and e1_activation is not None and j>=e1_activation
    if touched:
     first=j;touch_i=j if touch_i is None else touch_i
     qty=.4/risk_cost(ep,stop,slip)
     legs.append(dict(stage='E1',entry=ep,initial=qty,remaining=qty,bar=j,tps=set()))
     fees+=FEE*ep*qty;slippage+=slip*ep*qty
     events.append(dict(kind='ENTRY',bar=j,stage='E1',price=ep,quantity=qty))
    elif (lo<=stop if side==1 else hi>=stop):
     outcome='PRICE_INVALID_BEFORE_FILL';last_i=j;break
   else:
    # Closed reaction, then a later closed hold. Arm a separate retest order.
    if not add_filled and add_index is None and not target_seen and j>=first+2 and j<deadline:
     k=j-2;ro,rh,rl,rc=a[k];ho,hh,hl,hc=a[k+1]
     rg=max(rh-rl,1e-9)
     wick=(min(ro,rc)-rl)/rg if side==1 else (rh-max(ro,rc))/rg
     reaction=(side*(rc-ro)>0 and side*(rc-ep)>=0 and wick>=.2)
     hold=(hl>=rl and hc>=ep) if side==1 else (hh<=rh and hc<=ep)
     near=(rl<=p['entry_zone_high']) if side==1 else (rh>=p['entry_zone_low'])
     if reaction and hold and near:
      ae=min(rc,p['entry_zone_high']) if side==1 else max(rc,p['entry_zone_low'])
      ae=max(ep,ae) if side==1 else min(ep,ae)
      ae=np.floor(ae/TICK)*TICK if side==1 else np.ceil(ae/TICK)*TICK
      rate=self.fund.fundingRate.asof(self.ts[j])
      if planned_rr(ae,stop,targets,side,cfg.exits,float(rate),slip)>=3:
       add_index=j+1;add_price=float(ae);trigger_i=k;e2_trigger_i=k
    if add_index is not None and not add_filled and not target_seen and j>=add_index and j<deadline and side*(op-stop)>0:
     if (lo<=add_price-TICK if side==1 else hi>=add_price+TICK):
      qty=.6/risk_cost(add_price,stop,slip)
      legs.append(dict(stage='E2',entry=add_price,initial=qty,remaining=qty,bar=j,tps=set()))
      fees+=FEE*add_price*qty;slippage+=slip*add_price*qty;add_filled=True
      events.append(dict(kind='ENTRY',bar=j,stage='E2',price=add_price,quantity=qty))
   if first is not None:
    # Settlement timing inside the first fill bar is unknown: debit adverse
    # funding, withhold favorable credit for newly opened quantity in that bar.
    for leg in legs:
     if leg['remaining']>1e-15:
      f=side*self.fund_bar[j]*leg['remaining']
      if leg['bar']<j or f>0:funding+=f
    stop_hit=lo<=stop if side==1 else hi>=stop
    tp_hit=hi>=targets[0] if side==1 else lo<=targets[0]
    if stop_hit:
     ambiguous+=int(tp_hit)
     xp=min(stop,op) if side==1 else max(stop,op)
     for leg in legs:
      if leg['remaining']>1e-15:close_leg(leg,leg['remaining'],xp,j,'SL')
     outcome='SL';last_i=j;break
    if tp_hit:target_seen=True
    for leg in legs:
     if leg['bar']==j:continue # no optimistic same-bar target profit on a new fill
     for k,(target,weight) in enumerate(zip(targets,exit_weights)):
      if weight and k not in leg['tps'] and (hi>=target+TICK if side==1 else lo<=target-TICK):
       close_leg(leg,min(leg['remaining'],leg['initial']*weight),target,j,f'TP{k+1}');leg['tps'].add(k)
    if sum(x['remaining'] for x in legs)<1e-14:outcome='TARGET';last_i=j;break
    if j>=first+cfg.holding_hours*4-1:
     for leg in legs:
      if leg['remaining']>1e-15:close_leg(leg,leg['remaining'],cl,j,'DAY_POSITION_CLOSE')
     outcome='DAY_POSITION_CLOSE';last_i=j;break
   j+=1
  result=dict(config_id=cfg.id,issue_time=issue_t,issue_i=issue_i,side=side,entry=ep,stop=stop,tp1=targets[0],tp2=targets[1],tp3=targets[2],planned_net_rr=p['planned_net_rr'],
   filled=first is not None,entry_time=self.ts[first] if first is not None else None,entry_i=first,
   exit_time=self.ts[last_i]+pd.Timedelta(minutes=15),exit_i=last_i,fill_hours=(first-issue_i)/4 if first is not None else None,
   e2_filled=add_filled,trigger_i=trigger_i,e1_trigger_i=e1_trigger_i,e2_trigger_i=e2_trigger_i,touch_i=touch_i,last_validated_i=last_validated,outcome=outcome,
   gross_R=gross,fee_R=fees,slippage_R=slippage,funding_R=funding,net_R=gross-fees-slippage-funding,ambiguous=ambiguous,
   holding_hours=(last_i-first+1)/4 if first is not None else None)
  if save_legs:result['events']=events
  return result

 def run(self,cfg,start,end,slip=SLIP,save_legs=False):
  start=pd.Timestamp(start);end=pd.Timestamp(end)
  left=self.ts.searchsorted(start);right=self.ts.searchsorted(end-pd.Timedelta(hours=48))
  rows=[];next_i=left
  for i in self.hour_indices[(self.hour_indices>=left)&(self.hour_indices<right)]:
   if i<next_i:continue
   p=build_plan(self.rows[int(i)],cfg)
   if p is None:continue
   result=self.run_plan(int(i),p,cfg,slip,save_legs)
   assert result['exit_time']<=end
   rows.append(result);next_i=max(result['exit_i']+1,int(i)+24)
  return pd.DataFrame(rows)

def metrics(plans):
 if plans.empty:return dict(plans=0,trades=0,win_rate=None,payoff=None,fill_rate=None,mean_R=None,PF=None,day_coverage=None)
 t=plans[plans.filled];r=t.net_R.to_numpy(float)
 win=r[r>0];loss=r[r<0]
 return dict(plans=len(plans),trades=len(t),win_rate=float((r>0).mean()) if len(r) else None,
  payoff=float(win.mean()/-loss.mean()) if len(win) and len(loss) else None,
  fill_rate=float(plans.filled.mean()),mean_R=float(r.mean()) if len(r) else None,
  PF=float(win.sum()/-loss.sum()) if len(loss) else None,e2_fill_rate=float(t.e2_filled.mean()) if len(t) else None,
  fill_hours_median=float(t.fill_hours.median()) if len(t) else None,cumulative_R=float(r.sum()),
  issued_days=int(plans.issue_time.dt.floor('D').nunique()),profitable_plans=int((r>0).sum()),losing_plans=int((r<0).sum()))

def load_data(path):
 path=Path(path)
 raw=pd.read_csv(path/'data/BTCUSDT_15m.csv.gz',index_col=0);raw.index=pd.to_datetime(raw.index,utc=True)
 fund=pd.read_csv(path/'data/BTCUSDT_funding.csv.gz',index_col=0);fund.index=pd.to_datetime(fund.index,utc=True,format='mixed')
 return raw,fund
