from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "market_whales"
STATE_PATH = BASE / "state" / "recorder_state.json"
SUMMARY_PATH = BASE / "output" / "latest_summary.json"
EVENTS_PATH = BASE / "output" / "latest_events.json"


def parse_iso(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except Exception:
        return None


def is_first_observation_event(event: dict[str, Any], wallet: dict[str, Any], run_dt: datetime | None) -> bool:
    """Suppress only a NEW event created on the wallet's first successful observation."""
    if event.get("event") != "NEW":
        return False
    if float(event.get("prev_position_usd") or 0.0) != 0.0:
        return False
    first_seen = parse_iso((wallet or {}).get("first_seen_utc"))
    if first_seen is None or run_dt is None:
        return False
    # Collector writes first_seen and last_run within the same live run. Allow a small clock buffer.
    return abs((run_dt - first_seen).total_seconds()) <= 600


def filter_events(events: list[dict[str, Any]], wallets: dict[str, Any], run_dt: datetime | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for event in events:
        address = str(event.get("address") or "").lower()
        wallet = wallets.get(address) or {}
        if is_first_observation_event(event, wallet, run_dt):
            suppressed.append(event)
        else:
            kept.append(event)
    return kept, suppressed


def apply_guard() -> dict[str, int]:
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    events_doc = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))

    wallets = state.get("wallets") or {}
    run_dt = parse_iso(state.get("last_run_utc"))
    raw_events = list(events_doc.get("events") or [])
    kept, suppressed = filter_events(raw_events, wallets, run_dt)

    events_doc["events"] = kept
    events_doc["baseline_suppressed_events"] = len(suppressed)
    events_doc["baseline_guard"] = "PASS"

    # Summary contains a capped copy of the same event stream.
    summary_events = list(summary.get("large_change_events") or [])
    summary_kept, summary_suppressed = filter_events(summary_events, wallets, run_dt)
    summary["large_change_events"] = summary_kept
    summary["baseline_suppressed_events"] = len(summary_suppressed)
    summary["baseline_guard"] = "PASS"
    notes = list(summary.get("notes") or [])
    note = "First successful observation of a wallet is BASELINE_ONLY; it cannot create NEW/20M/50M whale-change alerts."
    if note not in notes:
        notes.append(note)
    summary["notes"] = notes

    state["large_change_events"] = len(kept)
    state["baseline_suppressed_events"] = len(suppressed)
    state["baseline_guard"] = "PASS"

    EVENTS_PATH.write_text(json.dumps(events_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    result = {"kept": len(kept), "suppressed": len(suppressed)}
    print(json.dumps(result, ensure_ascii=False))
    return result


def self_test() -> None:
    now = datetime.now(timezone.utc)
    now_s = now.isoformat().replace("+00:00", "Z")
    old_s = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    wallets = {
        "0xnew": {"first_seen_utc": now_s},
        "0xold": {"first_seen_utc": old_s},
    }
    events = [
        {"address": "0xnew", "event": "NEW", "prev_position_usd": 0, "current_position_usd": 100_000_000},
        {"address": "0xold", "event": "NEW", "prev_position_usd": 0, "current_position_usd": 100_000_000},
        {"address": "0xnew", "event": "INCREASE", "prev_position_usd": 60_000_000, "current_position_usd": 90_000_000},
    ]
    kept, suppressed = filter_events(events, wallets, now)
    assert len(suppressed) == 1, (kept, suppressed)
    assert suppressed[0]["address"] == "0xnew"
    assert len(kept) == 2
    print("BASELINE_GUARD_SELF_TEST=PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        apply_guard()


if __name__ == "__main__":
    main()
