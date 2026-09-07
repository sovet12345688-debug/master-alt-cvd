from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MANIFEST = json.loads((HERE / 'r21_freeze_manifest.json').read_text(encoding='utf-8'))
CAND = HERE / 'r21_candidate_config.json'
FROZEN = HERE / 'r21_frozen_config.json'
ENGINE = HERE / 'r21_engine.py'
CONTRACT = HERE / 'test_r21_contract.py'
R20_FROZEN = ROOT / 'r20' / 'r20_frozen_config_rev2.json'
R20_ENGINE = ROOT / 'r20' / 'r20_engine.py'


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def blob(path: Path) -> str:
    return subprocess.check_output(['git', 'hash-object', str(path)], text=True).strip()

checks = {
    'model': MANIFEST['model'] == 'MASTER_BTC_TREND_V3_R2_1',
    'status': MANIFEST['status'] == 'FINAL_FROZEN_NO_REPLAY',
    'candidate_sha256': sha256(CAND) == MANIFEST['candidate_config_sha256'],
    'candidate_blob': blob(CAND) == MANIFEST['candidate_config_git_blob_sha1'],
    'frozen_sha256': sha256(FROZEN) == MANIFEST['frozen_config_sha256'],
    'frozen_blob': blob(FROZEN) == MANIFEST['frozen_config_git_blob_sha1'],
    'r21_engine_blob': blob(ENGINE) == MANIFEST['r21_engine_git_blob_sha1'],
    'r21_contract_blob': blob(CONTRACT) == MANIFEST['r21_contract_git_blob_sha1'],
    'parent_frozen_blob': blob(R20_FROZEN) == MANIFEST['parent_r20_frozen_config_blob_sha1'],
    'parent_engine_blob': blob(R20_ENGINE) == MANIFEST['parent_r20_engine_blob_sha1'],
    'single_change': MANIFEST['single_change_id'] == 'SHORT_CORE_REQUIRES_SEPARATE_COMPLETED_DAILY_CONFIRMATION',
    'no_replay_before_freeze': MANIFEST['historical_replay_performed_before_freeze'] is False,
    'same_history_cannot_promote': MANIFEST['same_historical_window_can_promote'] is False,
    'replay_output_absent': not (HERE / 'output' / 'historical_replay' / 'audit.json').exists(),
}

cand = json.loads(CAND.read_text(encoding='utf-8'))
frozen = json.loads(FROZEN.read_text(encoding='utf-8'))
checks['semantic_identity'] = all([
    cand['parent'] == frozen['parent'],
    cand['single_change'] == frozen['single_change'],
    cand['inherited_unchanged'] == frozen['inherited_unchanged'],
    cand['risk_identity'] == frozen['risk_identity'],
    cand['evidence_firewall'] == frozen['evidence_firewall'],
    cand['forbidden_changes'] == frozen['forbidden_changes'],
])
checks['frozen_status'] = frozen['status'] == 'FINAL_FROZEN_NO_REPLAY'

result = {
    'freeze_guard': 'PASS' if all(checks.values()) else 'FAIL',
    'checks': checks,
    'frozen_config_sha256': sha256(FROZEN),
    'r21_engine_blob': blob(ENGINE),
    'r21_contract_blob': blob(CONTRACT),
    'historical_replay_performed_before_freeze': False,
}
print(json.dumps(result, indent=2))
if result['freeze_guard'] != 'PASS':
    raise SystemExit(1)
