from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
M = json.loads((HERE / 'r23_freeze_manifest.json').read_text(encoding='utf-8'))
F = json.loads((HERE / 'r23_frozen_config.json').read_text(encoding='utf-8'))
C = json.loads((HERE / 'r23_candidate_config.json').read_text(encoding='utf-8'))


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def blob(p: Path) -> str:
    return subprocess.check_output(['git','hash-object',str(p)], text=True).strip()


def semantic_identity() -> bool:
    c = dict(C)
    f = dict(F)
    c['status'] = 'NORMALIZED'
    f['status'] = 'NORMALIZED'
    return c == f

checks = {
    'model': F.get('model') == 'MASTER_BTC_TREND_V3_R2_3',
    'status': F.get('status') == 'FINAL_FROZEN_NO_REPLAY',
    'single_change': F.get('single_change',{}).get('id') == 'ADD_NON_EXECUTION_EARLY_DETECTION_LAYER_ONLY',
    'execution_unchanged': F.get('single_change',{}).get('execution_engine') == 'FROZEN_R2_1_UNCHANGED',
    'early_detection_no_execution': F.get('single_change',{}).get('early_detection_can_execute') is False,
    'early_detection_no_seed_change': F.get('single_change',{}).get('early_detection_can_modify_seed') is False,
    'early_detection_no_risk_change': F.get('single_change',{}).get('early_detection_can_modify_risk') is False,
    'early_detection_no_stop_change': F.get('single_change',{}).get('early_detection_can_modify_stop') is False,
    'frozen_sha': sha256(HERE/'r23_frozen_config.json') == M['frozen_config']['sha256'],
    'frozen_blob': blob(HERE/'r23_frozen_config.json') == M['frozen_config']['git_blob_sha1'],
    'detector_sha': sha256(HERE/'r23_detector.py') == M['detector']['sha256'],
    'detector_blob': blob(HERE/'r23_detector.py') == M['detector']['git_blob_sha1'],
    'contract_sha': sha256(HERE/'test_r23_contract.py') == M['contract']['sha256'],
    'contract_blob': blob(HERE/'test_r23_contract.py') == M['contract']['git_blob_sha1'],
    'semantic_identity': semantic_identity(),
    'no_replay_before_freeze': M.get('historical_replay_performed_before_freeze') is False,
    'same_history_cannot_promote': M.get('historical_same_window_can_promote') is False,
    'execution_engine_changed_false': M.get('execution_engine_changed') is False,
    'replay_output_absent': not (HERE/'output/historical_replay').exists(),
}

print(json.dumps({
    'freeze_guard':'PASS' if all(checks.values()) else 'FAIL',
    'checks':checks,
    'frozen_config_sha256':M['frozen_config']['sha256'],
    'detector_blob':M['detector']['git_blob_sha1'],
    'parent_r21_engine_blob':M['parent_r21']['engine_git_blob_sha1'],
    'historical_replay_performed_before_freeze':False,
}, indent=2))
if not all(checks.values()):
    sys.exit(1)
