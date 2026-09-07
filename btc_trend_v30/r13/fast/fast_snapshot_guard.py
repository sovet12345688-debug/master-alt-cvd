from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--snapshot-root", required=True)
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    snapshot_root = Path(args.snapshot_root)
    repo_root = Path(args.repo_root)

    file_checks = {}
    for rel, expected in manifest["files"].items():
        path = snapshot_root / rel
        actual = sha256_file(path) if path.exists() else None
        file_checks[rel] = {
            "exists": path.exists(),
            "expected": expected,
            "actual": actual,
            "pass": actual == expected,
        }

    cfg = repo_root / "btc_trend_v30/r13/r13_frozen_config.json"
    engine = repo_root / "btc_trend_v30/r13/r13_engine.py"
    engine_blob = (
        subprocess.check_output(["git", "hash-object", str(engine)], text=True).strip()
        if engine.exists()
        else None
    )

    audit_path = snapshot_root / "r13/output/historical_diagnostic/audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else {}

    checks = {
        "all_snapshot_file_hashes": all(v["pass"] for v in file_checks.values()),
        "config_sha_exact": cfg.exists()
        and sha256_file(cfg) == manifest["contract"]["canonical_config_sha256"],
        "engine_blob_exact": engine_blob == manifest["contract"]["r13_engine_git_blob_sha"],
        "governance_status_exact": audit.get("status")
        == manifest["contract"]["governance_status_required"],
        "baseline_identity_pass": audit.get("baseline_identity_pass") is True,
        "promotion_firewall": audit.get("interpretation_guardrails", {}).get(
            "this_replay_can_promote_to_production"
        )
        is False,
    }

    result = {
        "mode": manifest["mode"],
        "fast_snapshot_guard": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "file_checks": file_checks,
        "snapshot_run": manifest["source_full_run"],
        "fallback": manifest["fallback_policy"]["on_any_hash_or_contract_mismatch"],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["fast_snapshot_guard"] != "PASS":
        raise SystemExit("FAST SNAPSHOT GUARD FAILED; REQUIRE NEW FULL REPLAY")


if __name__ == "__main__":
    main()
