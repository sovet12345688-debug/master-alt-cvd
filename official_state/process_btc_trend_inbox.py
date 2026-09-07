#!/usr/bin/env python3
"""Process MASTER BTC TREND OFFICIAL state inbox files.

This bridge does not calculate market direction. It only transports already-completed
actual OFFICIAL state payloads through the common OFFICIAL State publisher.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "official_state/inbox/btc_trend"
REJECTED = ROOT / "official_state/rejected/btc_trend"
PUBLISHER = ROOT / "official_state/publish_official_state.py"


def load_json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("payload must be a JSON object")
    return obj


def precheck(path: Path) -> None:
    state = load_json(path)
    if state.get("master_id") != "btc_trend":
        raise ValueError("master_id must be btc_trend")
    if state.get("state_status") != "STORED":
        raise ValueError("state_status must be STORED")
    run = state.get("run") or {}
    if run.get("run_type") != "OFFICIAL":
        raise ValueError("run.run_type must be OFFICIAL")
    run_id = run.get("run_id")
    if not isinstance(run_id, str) or not run_id.startswith("MBTC-V26-") or not run_id.endswith("-KST"):
        raise ValueError("run_id must match MBTC-V26-...-KST policy")
    lineage = state.get("lineage") or {}
    if lineage.get("state_origin") != "ACTUAL_STORED_RUN":
        raise ValueError("lineage.state_origin must be ACTUAL_STORED_RUN")
    privacy = state.get("privacy") or {}
    if privacy.get("contains_personal_account_data") is not False:
        raise ValueError("personal account data is forbidden")
    if privacy.get("public_repository_safe") is not True:
        raise ValueError("public_repository_safe must be true")


def reject(path: Path, reason: str) -> None:
    REJECTED.mkdir(parents=True, exist_ok=True)
    dest = REJECTED / path.name
    if dest.exists():
        dest = REJECTED / f"{path.stem}.duplicate-reject{path.suffix}"
    shutil.move(str(path), str(dest))
    error_path = dest.with_suffix(dest.suffix + ".error.txt")
    error_path.write_text(reason.strip() + "\n", encoding="utf-8")
    print(f"BTC_TREND_BRIDGE_REJECTED file={dest.relative_to(ROOT)} reason={reason}")


def publish(path: Path) -> tuple[bool, str]:
    try:
        precheck(path)
    except Exception as exc:
        return False, f"PRECHECK_FAIL: {exc}"

    proc = subprocess.run(
        [sys.executable, str(PUBLISHER), "--mode", "publish", "--input", str(path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    output = "\n".join(x for x in (proc.stdout.strip(), proc.stderr.strip()) if x).strip()
    if proc.returncode != 0:
        return False, output or f"publisher exit={proc.returncode}"
    return True, output


def main() -> int:
    INBOX.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in INBOX.glob("*.json") if p.is_file())
    if not files:
        print("BTC_TREND_BRIDGE=NO_INPUT")
        return 0

    published = 0
    rejected = 0
    for path in files:
        ok, message = publish(path)
        if ok:
            path.unlink()
            published += 1
            print(f"BTC_TREND_BRIDGE_PUBLISHED file={path.name} {message}")
        else:
            reject(path, message)
            rejected += 1

    print(f"BTC_TREND_BRIDGE_SUMMARY published={published} rejected={rejected}")
    return 1 if rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
