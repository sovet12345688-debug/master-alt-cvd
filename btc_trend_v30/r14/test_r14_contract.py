from __future__ import annotations
import sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
from r13.r13_engine import EntryDecision
from r14.r14_engine import R14Engine

def dec(route='RETEST',state='OPEN'):
    return EntryDecision('ENTER',route,state,100.0,95.0,115.0,5.0,80.0,4,100.0,'PASS')

def main():
    e=R14Engine(); dlong={'up_transition':True,'down_transition':False}
    assert abs(e.initial_risk_R+e.add_risk_R-1.0)<1e-12
    assert abs(e.fixed_fraction+e.runner_fraction-1.0)<1e-12
    ok,why=e.add_policy_pass(initial_route='RETEST',initial_resolved_time=None,add_confirm_time=10,add_decision=dec(),daily=dlong,direction='long',favorable_progress_R=1.0); assert ok
    ok,_=e.add_policy_pass(initial_route='RETEST',initial_resolved_time=None,add_confirm_time=10,add_decision=dec('IGNITION'),daily=dlong,direction='long',favorable_progress_R=2.0); assert not ok
    ok,_=e.add_policy_pass(initial_route='RETEST',initial_resolved_time=9,add_confirm_time=10,add_decision=dec(),daily=dlong,direction='long',favorable_progress_R=2.0); assert not ok
    ok,_=e.add_policy_pass(initial_route='RETEST',initial_resolved_time=None,add_confirm_time=10,add_decision=dec(),daily=dlong,direction='long',favorable_progress_R=0.999); assert not ok
    ok,_=e.add_policy_pass(initial_route='RETEST',initial_resolved_time=None,add_confirm_time=10,add_decision=dec('RETEST','CAUTION'),daily=dlong,direction='long',favorable_progress_R=2.0); assert not ok
    ok,_=e.add_policy_pass(initial_route='RETEST',initial_resolved_time=None,add_confirm_time=10,add_decision=dec(),daily={'up_transition':False},direction='long',favorable_progress_R=2.0); assert not ok
    print('R14_CONTRACT_TEST_PASS 6/6 add-policy cases + risk/fraction invariants')
if __name__=='__main__': main()
