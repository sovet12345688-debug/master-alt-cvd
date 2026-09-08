from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
M = json.loads((HERE / "r26_freeze_manifest.json").read_text(encoding="utf-8"))


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def blob(p: Path) -> str:
    return subprocess.check_output(["git", "hash-object", str(p)], text=True).strip()


def main() -> None:
    frozen = json.loads((HERE / "r26_frozen_config.json").read_text(encoding="utf-8"))
    checks = {
        "candidate_sha": sha256(HERE / "r26_candidate_config.json") == M["candidate_config_sha256"],
        "candidate_blob": blob(HERE / "r26_candidate_config.json") == M["candidate_config_git_blob_sha1"],
        "frozen_sha": sha256(HERE / "r26_frozen_config.json") == M["frozen_config_sha256"],
        "frozen_blob": blob(HERE / "r26_frozen_config.json") == M["frozen_config_git_blob_sha1"],
        "engine_sha": sha256(HERE / "r26_engine.py") == M["r26_engine_sha256"],
        "engine_blob": blob(HERE / "r26_engine.py") == M["r26_engine_git_blob_sha1"],
        "contract_sha": sha256(HERE / "test_r26_contract.py") == M["r26_contract_sha256"],
        "contract_blob": blob(HERE / "test_r26_contract.py") == M["r26_contract_git_blob_sha1"],
        "r25_sha": sha256(ROOT / "r25/r25_frozen_config.json") == M["execution_parent_r25"]["frozen_config_sha256"],
        "r25_engine_blob": blob(ROOT / "r25/r25_engine.py") == M["execution_parent_r25"]["engine_git_blob_sha1"],
        "r25_contract_blob": blob(ROOT / "r25/test_r25_contract.py") == M["execution_parent_r25"]["contract_git_blob_sha1"],
        "model": frozen.get("model") == "MASTER_BTC_TREND_V3_R2_6",
        "status": frozen.get("status") == "FINAL_FROZEN_NO_REPLAY",
        "detector_no_execute": frozen["single_change"]["detector_can_execute"] is False,
        "execution_unchanged": M["execution_engine_changed"] is False,
        "replay_before_freeze_false": M["historical_replay_performed_before_freeze"] is False,
    }
    print(json.dumps(checks, indent=2))
    if not all(checks.values()):
        raise SystemExit("R26_FREEZE_GUARD_FAIL")
    print("R26_FREEZE_GUARD_PASS")


if __name__ == "__main__":
    main()
