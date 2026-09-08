from __future__ import annotations

import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MANIFEST = HERE / "R26_FORWARD_SCORECARD_FREEZE_MANIFEST_V1.json"


def git_blob(path: Path) -> str:
    return subprocess.check_output(["git", "hash-object", str(path)], text=True).strip()


def main() -> None:
    spec = json.loads(MANIFEST.read_text(encoding="utf-8"))
    checks = {}
    for rel, expected in spec["files"].items():
        path = ROOT / rel
        actual = git_blob(path) if path.exists() else None
        checks[rel] = {"expected": expected, "actual": actual, "pass": actual == expected}
    result = {
        "manifest": spec["manifest"],
        "status": spec["status"],
        "checks": checks,
        "pass": all(v["pass"] for v in checks.values()),
    }
    print(json.dumps(result, indent=2))
    if not result["pass"]:
        raise SystemExit("R26_FORWARD_SCORECARD_FREEZE_GUARD_FAIL")
    print("R26_FORWARD_SCORECARD_FREEZE_GUARD_PASS")


if __name__ == "__main__":
    main()
