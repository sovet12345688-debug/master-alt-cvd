from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CANDIDATE = HERE / "r24_candidate_config.json"
FROZEN = HERE / "r24_frozen_config.json"
ENGINE = HERE / "r24_engine.py"
CONTRACT = HERE / "test_r24_contract.py"
MANIFEST = HERE / "r24_freeze_manifest.json"
R21_ENGINE = ROOT / "r21/r21_engine.py"
R21_FROZEN = ROOT / "r21/r21_frozen_config.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def blob(path: Path) -> str:
    return subprocess.check_output(["git", "hash-object", str(path)], text=True).strip()


def normalize_candidate(x: dict) -> dict:
    y = copy.deepcopy(x)
    y.pop("status", None)
    return y


def normalize_frozen(x: dict) -> dict:
    y = copy.deepcopy(x)
    y.pop("status", None)
    y.pop("freeze_metadata", None)
    return y


def main() -> None:
    c = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    f = json.loads(FROZEN.read_text(encoding="utf-8"))
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))

    checks = {
        "model": f.get("model") == "MASTER_BTC_TREND_V3_R2_4",
        "frozen_status": f.get("status") == "FINAL_FROZEN_NO_REPLAY",
        "candidate_status": c.get("status") == "PRE_FREEZE_CANDIDATE_NO_REPLAY",
        "candidate_sha256": sha256(CANDIDATE) == m["candidate_config_sha256"],
        "candidate_blob": blob(CANDIDATE) == m["candidate_config_git_blob_sha1"],
        "frozen_sha256": sha256(FROZEN) == m["frozen_config_sha256"],
        "frozen_blob": blob(FROZEN) == m["frozen_config_git_blob_sha1"],
        "engine_blob": blob(ENGINE) == m["r24_engine_git_blob_sha1"],
        "contract_blob": blob(CONTRACT) == m["r24_contract_git_blob_sha1"],
        "parent_r21_engine_blob": blob(R21_ENGINE) == m["parent_r21_engine_git_blob_sha1"],
        "parent_r21_frozen_blob": blob(R21_FROZEN) == m["parent_r21_frozen_git_blob_sha1"],
        "semantic_identity": normalize_candidate(c) == normalize_frozen(f),
        "single_change": f["single_change"]["id"] == "MATURE_BEAR_SEED_ONLY_RISK_CAP",
        "risk_only_scope": f["single_change"]["applies_to"] == "SHORT_RISK_ALLOCATION_ONLY",
        "no_hard_veto": f["risk_action"]["mature_bear"]["hard_veto"] is False,
        "mature_seed_preserved": abs(float(f["risk_action"]["mature_bear"]["seed_risk_R"]) - 0.30) < 1e-12,
        "mature_no_confirm_add": abs(float(f["risk_action"]["mature_bear"]["confirm_risk_add_R"])) < 1e-12,
        "mature_no_core_add": abs(float(f["risk_action"]["mature_bear"]["core_risk_add_R"])) < 1e-12,
        "mature_cap": abs(float(f["risk_action"]["mature_bear"]["max_episode_risk_R"]) - 0.30) < 1e-12,
        "normal_r21_risk_identity": (
            abs(float(f["risk_action"]["not_mature_bear"]["seed_risk_R"]) - 0.30) < 1e-12
            and abs(float(f["risk_action"]["not_mature_bear"]["confirm_risk_add_R"]) - 0.30) < 1e-12
            and abs(float(f["risk_action"]["not_mature_bear"]["core_risk_add_R"]) - 0.25) < 1e-12
            and abs(float(f["risk_action"]["not_mature_bear"]["max_episode_risk_R"]) - 0.85) < 1e-12
        ),
        "no_posthoc_forensic_cutoffs": set(f["mature_bear_classifier"]["explicitly_not_used"]) == {
            "DISTANCE_BELOW_EMA200_THRESHOLD",
            "DAYS_BELOW_EMA200_THRESHOLD",
            "DAYS_SINCE_60D_HIGH_THRESHOLD",
            "NEW_VOLUME_THRESHOLD",
            "NEW_BREAK_DEPTH_THRESHOLD",
            "WINNER_LOSER_MEDIAN_CUTOFF",
        },
        "no_replay_before_freeze_candidate": c["evidence_firewall"]["r24_historical_replay_performed_before_freeze"] is False,
        "no_replay_before_freeze_frozen": f["evidence_firewall"]["r24_historical_replay_performed_before_freeze"] is False,
        "manifest_no_replay": m["historical_replay_performed_before_freeze"] is False,
        "same_history_cannot_promote": f["evidence_firewall"]["same_2021_2026_window_cannot_promote_production"] is True,
        "replay_script_absent": not (HERE / "r24_historical_diagnostic_replay.py").exists(),
        "replay_output_absent": not (HERE / "output").exists(),
    }

    out = {
        "freeze_guard": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "frozen_config_sha256": sha256(FROZEN),
        "r24_engine_blob": blob(ENGINE),
        "r24_contract_blob": blob(CONTRACT),
        "parent_r21_engine_blob": blob(R21_ENGINE),
        "historical_replay_performed_before_freeze": False,
    }
    print(json.dumps(out, indent=2))
    if not all(checks.values()):
        raise SystemExit("R24_FINAL_FREEZE_GUARD_FAIL")


if __name__ == "__main__":
    main()
