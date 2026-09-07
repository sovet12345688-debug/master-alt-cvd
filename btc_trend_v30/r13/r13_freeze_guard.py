from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CFG = ROOT / "r13_frozen_config.json"
MANIFEST = ROOT / "r13_freeze_manifest.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    actual = sha256_file(CFG)
    expected = m["canonical_config_sha256"]
    checks = {
        "config_exists": CFG.exists(),
        "manifest_exists": MANIFEST.exists(),
        "config_sha256_match": actual == expected,
        "freeze_status_locked": m.get("freeze_status") == "FINAL_FREEZE_PRE_OOS",
        "oos_firewall_locked": m.get("oos_firewall") == "LOCKED",
        "oos_runs_before_freeze_zero": int(m.get("oos_runs_before_freeze", -1)) == 0,
        "v2_6_untouched": m.get("v2_6_modified") is False,
    }
    result = {
        "model": m.get("model"),
        "freeze_guard": "PASS" if all(checks.values()) else "FAIL",
        "expected_config_sha256": expected,
        "actual_config_sha256": actual,
        "checks": checks,
        "rule": "Any canonical config change creates a new baseline/version and requires a new pre-OOS freeze. Do not silently update this hash.",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["freeze_guard"] != "PASS":
        raise SystemExit("R1.3 FREEZE GUARD FAILED")


if __name__ == "__main__":
    main()
