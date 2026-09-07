from __future__ import annotations
import hashlib, json, sys
from pathlib import Path
from typing import Any, Mapping
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
from r13.r13_engine import R13Engine, EntryDecision

CFG_PATH=HERE/'r14_frozen_config.json'; MAN_PATH=HERE/'r14_freeze_manifest.json'

def sha(p:Path)->str: return hashlib.sha256(p.read_bytes()).hexdigest()

class R14ContractError(RuntimeError): pass

class R14Engine:
    def __init__(self):
        self.cfg=json.loads(CFG_PATH.read_text(encoding='utf-8')); self.man=json.loads(MAN_PATH.read_text(encoding='utf-8'))
        if sha(CFG_PATH)!=self.man['canonical_config_sha256']: raise R14ContractError('R14 config SHA mismatch')
        if self.cfg['model']!='MASTER_BTC_TREND_V3_R1_4': raise R14ContractError('wrong model')
        self.initial=R13Engine()
    def evaluate_initial(self,**kwargs)->EntryDecision:
        return self.initial.evaluate_pair(**kwargs)
    def add_policy_pass(self,*,initial_route:str,initial_resolved_time,add_confirm_time,add_decision:EntryDecision,daily:Mapping[str,Any],direction:str,favorable_progress_R:float)->tuple[bool,str]:
        c=self.cfg['four_hour_add']
        if add_decision.action!='ENTER': return False,'ADD_CANDIDATE_NOT_ENTER'
        if add_decision.route!=initial_route: return False,'ROUTE_CHANGE_NO_ADD'
        if initial_resolved_time is not None and add_confirm_time>=initial_resolved_time: return False,'INITIAL_ALREADY_RESOLVED'
        if float(favorable_progress_R)<float(c['favorable_progress_before_add_min_R']): return False,'INSUFFICIENT_FAVORABLE_PROGRESS'
        if add_decision.risk_state!=c['risk_state_required']: return False,'ADD_RISK_NOT_OPEN'
        if not self.initial.daily_transition_alignment(daily,direction): return False,'DAILY_TRANSITION_NOT_ALIGNED'
        return True,'ADD_THESIS_CONTINUITY_PASS'
    @property
    def initial_risk_R(self): return float(self.cfg['episode_risk_budget']['initial_1h_risk_R'])
    @property
    def add_risk_R(self): return float(self.cfg['episode_risk_budget']['max_4h_add_risk_R'])
    @property
    def fixed_fraction(self): return float(self.cfg['initial_position_management']['fixed_target_fraction'])
    @property
    def runner_fraction(self): return float(self.cfg['initial_position_management']['runner_fraction'])

if __name__=='__main__':
    e=R14Engine(); print(json.dumps({'model':e.cfg['model'],'status':'IMPLEMENTATION_LOAD_PASS','config_sha256':sha(CFG_PATH),'r14_replay_performed':False},ensure_ascii=False,indent=2))
