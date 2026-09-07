from __future__ import annotations
import hashlib, json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
CFG=ROOT/'r14_frozen_config.json'; MAN=ROOT/'r14_freeze_manifest.json'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    m=json.loads(MAN.read_text(encoding='utf-8')); c=json.loads(CFG.read_text(encoding='utf-8'))
    checks={
      'config_sha':sha(CFG)==m['canonical_config_sha256'],
      'model':c.get('model')=='MASTER_BTC_TREND_V3_R1_4',
      'status':c.get('status')=='RESEARCH_BASELINE_FROZEN_PRE_R14_REPLAY',
      'replay_zero_before_freeze':int(m.get('r14_replay_runs_before_freeze',-1))==0,
      'firewall_locked':m.get('r14_replay_firewall')=='LOCKED',
      'r13_untouched':m.get('r13_modified') is False,
      'v26_untouched':m.get('v2_6_modified') is False,
      'risk_budget_sums_1R':abs(float(c['episode_risk_budget']['initial_1h_risk_R'])+float(c['episode_risk_budget']['max_4h_add_risk_R'])-1.0)<1e-12,
      'runner_split_sums_1':abs(float(c['initial_position_management']['fixed_target_fraction'])+float(c['initial_position_management']['runner_fraction'])-1.0)<1e-12,
      'mcr90_gate_20pct':float(c['predeclared_gates']['mcr_90d_mean_min'])==0.2,
      'mcr365_gate_20pct':float(c['predeclared_gates']['mcr_365d_mean_min'])==0.2,
    }
    out={'freeze_guard':'PASS' if all(checks.values()) else 'FAIL','checks':checks,'config_sha256':sha(CFG)}
    print(json.dumps(out,ensure_ascii=False,indent=2))
    if out['freeze_guard']!='PASS': raise SystemExit('R14_FREEZE_GUARD_FAIL')
if __name__=='__main__': main()
