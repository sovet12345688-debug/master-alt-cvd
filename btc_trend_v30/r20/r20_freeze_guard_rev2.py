from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "r20_freeze_manifest_rev2.json"
CANDIDATE = HERE / "r20_candidate_config.json"
FROZEN = HERE / "r20_frozen_config_rev2.json"
ENGINE = HERE / "r20_engine.py"
CONTRACT = HERE / "r20_contract.py"
PIT = HERE / "test_r20_engine_pit.py"
ROOT = HERE.parent.parent


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_blob(path: Path) -> str:
    return subprocess.check_output(["git", "hash-object", str(path)], text=True).strip()


def main() -> int:
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    c = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    f = json.loads(FROZEN.read_text(encoding="utf-8"))
    semantic = ["evidence_firewall", "data", "features", "long", "short", "holding", "reset", "predeclared_candidate_gates", "forbidden"]
    checks = {
        "model": c.get("model") == f.get("model") == m.get("model") == "MASTER_BTC_TREND_V3_R2_0",
        "candidate_status": c.get("status") == "PRE_FREEZE_CANDIDATE_NO_REPLAY",
        "frozen_status": f.get("status") == "FINAL_FROZEN_NO_REPLAY_REV2",
        "revision": f.get("freeze_metadata", {}).get("revision") == m.get("freeze_revision") == "REV2_PIT_AVAILABILITY_HOTFIX",
        "candidate_sha256": sha256(CANDIDATE) == m.get("candidate_config_sha256"),
        "candidate_blob": git_blob(CANDIDATE) == m.get("candidate_config_git_blob_sha1"),
        "frozen_blob": git_blob(FROZEN) == m.get("frozen_config_git_blob_sha1"),
        "engine_blob": git_blob(ENGINE) == m.get("engine_git_blob_sha1"),
        "contract_blob": git_blob(CONTRACT) == m.get("contract_git_blob_sha1"),
        "pit_blob": git_blob(PIT) == m.get("pit_test_git_blob_sha1"),
        "semantic_identity": all(c[k] == f[k] for k in semantic),
        "no_replay_before_freeze": f.get("freeze_metadata", {}).get("historical_replay_performed_before_freeze") is False and m.get("historical_replay_performed_before_freeze") is False,
        "historical_cannot_promote": f["evidence_firewall"]["historical_replay_can_promote"] is False and m.get("historical_diagnostic_only") is True,
        "forward_start": f["evidence_firewall"]["untouched_forward_start"] == "2026-09-05T00:00:00Z",
        "availability_semantics": f["data"].get("source_timestamp_semantics") == "CANDLE_OPEN_TIME_UTC" and f["data"].get("timeframe_available_offset_hours") == {"1H": 1, "4H": 4, "1D": 24} and f["data"].get("asof_context_must_use_availability_timestamp") is True,
        "gates_locked": f["predeclared_candidate_gates"] == c["predeclared_candidate_gates"],
        "risk_locked": f["long"]["seed"]["risk_R"] == 0.35 and f["long"]["confirm"]["risk_add_R"] == 0.35 and f["long"]["core"]["risk_add_R"] == 0.30 and f["short"]["seed"]["risk_R"] == 0.30 and f["short"]["confirm"]["risk_add_R"] == 0.30 and f["short"]["core"]["risk_add_R"] == 0.25,
        "replay_output_absent": not (HERE / "output/historical_replay/audit.json").exists() and not (HERE / "output/full_replay/audit.json").exists(),
    }
    ok = all(checks.values())
    out = {
        "freeze_guard_rev2": "PASS" if ok else "FAIL",
        "checks": checks,
        "candidate_config_sha256": sha256(CANDIDATE),
        "frozen_config_git_blob_sha1": git_blob(FROZEN),
        "engine_git_blob_sha1": git_blob(ENGINE),
        "contract_git_blob_sha1": git_blob(CONTRACT),
        "pit_test_git_blob_sha1": git_blob(PIT),
        "historical_replay_performed_before_freeze": False,
    }
    print(json.dumps(out, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
