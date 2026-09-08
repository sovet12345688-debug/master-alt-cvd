from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
M = json.loads((HERE / "r25_freeze_manifest.json").read_text())


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def blob(p: Path) -> str:
    return subprocess.check_output(["git", "hash-object", str(p)], text=True).strip()


def main() -> None:
    checks = {
        "frozen_sha": sha256(HERE / "r25_frozen_config.json") == M["frozen_config_sha256"],
        "frozen_blob": blob(HERE / "r25_frozen_config.json") == M["frozen_config_git_blob_sha1"],
        "engine_blob": blob(HERE / "r25_engine.py") == M["r25_engine_git_blob_sha1"],
        "contract_blob": blob(HERE / "test_r25_contract.py") == M["r25_contract_git_blob_sha1"],
        "parent_sha": sha256(ROOT / "r24/r24_frozen_config.json") == M["parent_r24_frozen_config_sha256"],
        "parent_engine_blob": blob(ROOT / "r24/r24_engine.py") == M["parent_r24_engine_git_blob_sha1"],
        "replay_before_freeze_false": M["historical_replay_performed_before_freeze"] is False,
    }
    print(json.dumps(checks, indent=2))
    if not all(checks.values()):
        raise SystemExit("R25_FREEZE_GUARD_FAIL")
    print("R25_FREEZE_GUARD_PASS")


if __name__ == "__main__":
    main()
