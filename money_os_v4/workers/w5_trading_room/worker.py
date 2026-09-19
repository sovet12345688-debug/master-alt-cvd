"""Reviewable W5 BTC_DAILY shadow adapter. No broker or alert-sending capability."""
from datetime import datetime,timezone
import math
from .model import Config,build_plan,planned_rr

MODES={'PLAN','TRIGGER_CHECK','ADD_CHECK','REVALIDATE'}

def _stamp(value):
 if not isinstance(value,str):raise ValueError('timestamp string required')
 t=datetime.fromisoformat(value.replace('Z','+00:00'))
 if t.tzinfo is None:raise ValueError('timezone required')
 return t

def evaluate(request,facts,release,now=None):
 """Emit a typed shadow plan; the caller owns persistence and orchestration.

Even a successful study does not activate trading. This adapter deliberately
cannot promote a live ENTER/ADD until the separate runtime/cutover work exists.
"""
 now=now or datetime.now(timezone.utc)
 out=dict(owner='W5',worker='TRADING_ROOM_EXECUTION',profile='BTC_DAILY',
  mode='SHADOW',status='WAIT',request_id=request.get('request_id'),
  action_owner='FINAL_NAVIGATOR',execution_enabled=False,production_weight=0,
  trading_permission=False,alert_candidate=None,plan=None,reasons=[],
  fill_probability_24h=None,fill_probability_label='NOT_CALIBRATED')
 if request.get('strategy_type')!='BTC_TRADE' or request.get('asset')!='BTCUSDT' or request.get('request_mode') not in MODES:
  out['reasons']=['INVALID_TYPED_REQUEST'];return out
 forbidden={'final_entry','structural_sl','tp1','tp2','tp3','net_rr','execution_trigger_pass','position_size','leverage'}
 if forbidden.intersection(request):out['reasons']=['NAVIGATOR_MUST_NOT_PRECOMPUTE_W5_PLAN'];return out
 try:
  current=facts['current'];h1=dict(facts['h1']);asof=_stamp(facts['closed_asof']);price_time=_stamp(current['asof'])
  if not facts.get('completed_candles') or asof>now or (now-asof).total_seconds()>4500:out['reasons'].append('CLOSED_FACTS_MISSING_OR_STALE')
  if not math.isfinite(float(current['price'])) or float(current['price'])<=0 or price_time>now or (now-price_time).total_seconds()>60:out['reasons'].append('CURRENT_MISSING_OR_STALE')
  if facts.get('severe_risk_veto') is not False:out['reasons'].append('RISK_VETO_OR_UNKNOWN')
  if facts.get('core_source_health')!='OK':out['reasons'].append('CORE_SOURCE_HEALTH_NOT_OK')
  if request.get('permission')!='ALLOW':out['reasons'].append('NAVIGATOR_PERMISSION_NOT_ALLOW')
  cfg=Config(**release['selected_configuration'])
  if h1.get('last_funding') is None:
   h1['last_funding']=.0003
   out['cost_assumptions']={'funding_observed':None,'funding_reserve_per_settlement':.0003,'label':'ASSUMPTION_WHEN_OPTIONAL_FUNDING_NA'}
  if out['reasons']:return out
  plan=build_plan(h1,cfg)
 except (KeyError,TypeError,ValueError,OverflowError):
  out['reasons'].append('MANDATORY_CORE_INVALID');return out
 if plan is None:out['reasons']=['NO_STRUCTURALLY_VALID_PLAN'];return out
 price=float(current['price']);side=plan['side']
 if side*(price-plan['stop'])<=0:out['reasons']=['PRICE_INVALIDATED'];return out
 if abs(price-plan['entry'])>1.5*plan['atr']:out['reasons']=['CURRENT_OUTSIDE_NONCHASE_RANGE'];return out
 out['plan']=dict(asset='BTCUSDT',direction='LONG' if side==1 else 'SHORT',trade_frame='1H',
  entry_zone=[plan['entry_zone_low'],plan['entry_zone_high']],e1_limit=plan['entry'],
  e2_limit=None,e2_condition='closed 15m reaction -> later closed hold -> future retest; recompute net R:R >=3',
  risk_budget_split=[.4,.6],structural_sl=plan['stop'],tp1=plan['targets'][0],tp2=plan['targets'][1],tp3=plan['targets'][2],
  target_weights=[0,0,1] if cfg.exits=='FULL' else [.2,.3,.5],net_rr=plan['planned_net_rr'],
  order_horizon_hours=24,position_horizon_hours=cfg.holding_hours,
  price_invalidation=plan['stop'],first_seen=None,history_origin='NOT_PERSISTED_SHADOW_PREVIEW',
  trigger_state='UNVERIFIED',candidate_id=cfg.id)
 out['reasons']=['SHADOW_PREVIEW_ONLY']
 if not release.get('performance_pass',False):out['reasons'].append('PERFORMANCE_TARGETS_NOT_MET')
 out['reasons'].append('RUNTIME_AND_PRODUCTION_CUTOVER_NOT_APPROVED')
 return out

def final_navigator_downgrade(w5_status,requested_status,severe_risk=False):
 order={'AVOID':0,'WAIT':1,'WATCH':2,'SMALL_ENTER':3,'ENTER':4}
 if w5_status not in order or requested_status not in order:return 'WAIT'
 if severe_risk:return 'AVOID'
 return min([w5_status,requested_status],key=order.get)

def trading_trigger_alert_allowed(w5,final_navigator_status):
 return bool(w5.get('mode')=='PRODUCTION' and w5.get('execution_enabled') is True
  and w5.get('status') in {'ENTER','SMALL_ENTER'}
  and final_navigator_status in {'ENTER','SMALL_ENTER'}
  and not w5.get('reasons'))
