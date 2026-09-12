#!/usr/bin/env python3
"""Validate MASTER BTC TREND OFFICIAL persistence as one synchronized state.

The latest snapshot, latest index entry, and append-only history tail must identify
exactly the same persisted OFFICIAL run. This validator never reconstructs state.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "official_state/latest/btc_trend.json"
INDEX = ROOT / "official_state/latest/index.json"


def load_json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must contain a JSON object")
    return obj


def history_tail(path: Path) -> dict[str, Any]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"{path.relative_to(ROOT)} is empty")
    obj = json.loads(lines[-1])
    if not isinstance(obj, dict):
        raise ValueError(f"{path.relative_to(ROOT)} last row must be a JSON object")
    return obj


def validate() -> list[str]:
    errors: list[str] = []
    try:
        latest = load_json(LATEST)
        index = load_json(INDEX)
    except Exception as exc:
        return [str(exc)]

    entry = (index.get("masters") or {}).get("btc_trend") or {}
    latest_run = latest.get("run") or {}
    latest_validity = latest.get("validity") or {}

    checks = {
        "state_status": (latest.get("state_status"), entry.get("state_status")),
        "run_id": (latest_run.get("run_id"), entry.get("latest_official_run_id")),
        "executed_kst": (latest_run.get("executed_kst"), entry.get("executed_kst")),
        "freshness_status": (latest_validity.get("freshness_status"), entry.get("freshness_status")),
    }
    for label, (left, right) in checks.items():
        if left != right:
            errors.append(f"LATEST_INDEX_MISMATCH {label}: latest={left!r} index={right!r}")

    history_pointer = (latest.get("lineage") or {}).get("history_pointer")
    if not isinstance(history_pointer, str) or not history_pointer.startswith("official_state/history/btc_trend/"):
        errors.append(f"INVALID_HISTORY_POINTER: {history_pointer!r}")
        return errors

    history_path = ROOT / history_pointer
    if not history_path.exists():
        errors.append(f"HISTORY_MISSING: {history_pointer}")
        return errors

    try:
        tail = history_tail(history_path)
    except Exception as exc:
        errors.append(str(exc))
        return errors

    tail_run = tail.get("run") or {}
    history_checks = {
        "state_status": (latest.get("state_status"), tail.get("state_status")),
        "run_id": (latest_run.get("run_id"), tail_run.get("run_id")),
        "executed_kst": (latest_run.get("executed_kst"), tail_run.get("executed_kst")),
    }
    for label, (left, right) in history_checks.items():
        if left != right:
            errors.append(f"LATEST_HISTORY_MISMATCH {label}: latest={left!r} history_tail={right!r}")

    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("BTC_TREND_PERSISTENCE_CONSISTENCY=FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("BTC_TREND_PERSISTENCE_CONSISTENCY=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
