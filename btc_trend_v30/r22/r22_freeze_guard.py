from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
M = json.loads((HERE / 'r22_freeze_manifest.json').read_text(encoding='utf-8'))
F = json.loads((HERE / 'r22_frozen_config.json').read_text(encoding='utf-8'))
C = json.loads((HERE / 'r22_candidate_config.json').read_text(encoding='utf-8'))


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def blob(p: Path) -> str:
    return subprocess.check_output(['git','hash-object',str(p)], text=True).strip()

checks = {
    'model': F.get('model') == 'MASTER_BTC_TREND_V3_R2_2',
    'status': F.get('status') == 'FINAL_FROZEN_NO_REPLAY',
    'single_change': F.get('single_change',{}).get('id') == 'DAILY_20D_EXTREME_DISTANCE_HARD_GATE_TO_PRIORITY_ONLY',
    'threshold_unchanged': F.get('single_change',{}).get('unchanged_distance_threshold_atr') == 1.0,
    'priority_retained': F.get('single_change',{}).get('priority_flag_retained') is True,
    'frozen_sha': sha256(HERE/'r22_frozen_config.json') == M['frozen_config']['sha256'],
    'frozen_blob': blob(HERE/'r22_frozen_config.json') == M['frozen_config']['git_blob_sha1'],
    'engine_sha': sha256(HERE/'r22_engine.py') == M['engine']['sha256'],
    'engine_blob': blob(HERE/'r22_engine.py') == M['engine']['git_blob_sha1'],
    'contract_sha': sha256(HERE/'test_r22_contract.py') == M['contract']['sha256'],
    'contract_blob': blob(HERE/'test_r22_contract.py') == M['contract']['git_blob_sha1'],
    'candidate_single_change_identity': C.get('single_change') == F.get('single_change'),
    'candidate_inherited_identity': C.get('inherited_unchanged') == F.get('inherited_unchanged'),
    'candidate_forbidden_identity': C.get('forbidden_changes') == F.get('forbidden_changes'),
    'no_replay_before_freeze': M.get('historical_replay_performed_before_freeze') is False,
    'same_history_cannot_promote': M.get('historical_same_window_can_promote') is False,
    'replay_output_absent': not (HERE/'output/historical_replay').exists(),
}

print(json.dumps({'freeze_guard':'PASS' if all(checks.values()) else 'FAIL','checks':checks,'frozen_config_sha256':M['frozen_config']['sha256'],'engine_blob':M['engine']['git_blob_sha1'],'historical_replay_performed_before_freeze':False}, indent=2))
if not all(checks.values()):
    sys.exit(1)
