#!/usr/bin/env python3
"""Enqueue an actual MASTER BTC TREND V2.6 OFFICIAL state for publication.

This is the only producer-side write path. It validates the completed state and writes
one transport JSON into official_state/inbox/btc_trend/. It never writes latest,
history, or index directly.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from publish_official_state import validate_state

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "official_state/inbox/btc_trend"
LATEST = ROOT / "official_state/latest/btc_trend.json"
RUN_ID_RE = re.compile(r"^MBTC-V26-\d{8}-(?:\d{4}|MANUAL-\d{4})-KST$")


def load_json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("payload must be a JSON object")
    return obj


def write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def enqueue(input_path: Path) -> Path:
    state = load_json(input_path)
    errors = validate_state(state, allow_placeholder=False)
    if errors:
        raise SystemExit("BTC_TREND_ENQUEUE=BLOCKED\n- " + "\n- ".join(errors))
    if state.get("master_id") != "btc_trend":
        raise SystemExit("BTC_TREND_ENQUEUE=BLOCKED: master_id must be btc_trend")

    run = state.get("run") or {}
    run_id = run.get("run_id")
    if not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None:
        raise SystemExit("BTC_TREND_ENQUEUE=BLOCKED: invalid BTC TREND run_id")

    if LATEST.exists():
        latest = load_json(LATEST)
        latest_run_id = (latest.get("run") or {}).get("run_id")
        if latest_run_id == run_id:
            raise SystemExit("BTC_TREND_ENQUEUE=BLOCKED: run_id already equals latest")

    dest = INBOX / f"{run_id}.json"
    if dest.exists():
        raise SystemExit(f"BTC_TREND_ENQUEUE=BLOCKED: inbox already contains {dest.name}")

    write_json(dest, state)
    print(f"BTC_TREND_ENQUEUE=PASS path={dest.relative_to(ROOT)} run_id={run_id}")
    return dest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    enqueue(args.input)


if __name__ == "__main__":
    main()
