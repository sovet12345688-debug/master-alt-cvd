#!/usr/bin/env python3
"""Persist meaningful WATCH events without calculating MASTER decisions."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "money_master_os/registry/MASTER_REGISTRY.json"
WATCH_REGISTRY_PATH = ROOT / "watch_events/registry.json"
SCHEMA_PATH = ROOT / "watch_events/schema.json"
INDEX_PATH = ROOT / "watch_events/latest/index.json"
LATEST_DIR = ROOT / "watch_events/latest"
HISTORY_ROOT = ROOT / "watch_events/history"
MASTER_IDS = {"market", "btc_trend", "alt_top100", "alt_final20", "trading"}
KST = timezone(timedelta(hours=9))
SENSITIVE_KEYS = {
    "account_balance", "actual_position_size", "account_identifier", "api_key",
    "api_secret", "private_execution_details", "password", "secret", "credential"
}
EXECUTION_KEYS = {
    "entry", "entry_zone", "stop_loss", "sl", "tp", "tp1", "tp2", "tp3",
    "risk_reward", "rr", "leverage", "order_size", "position_size"
}
FORBIDDEN_ACTION_VALUES = {"ENTER", "SMALL ENTER", "ADD"}


def load_json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"{path} must contain an object")
    return obj


def write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None


def find_keys(obj: Any, forbidden: set[str], prefix: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            p = f"{prefix}.{key}" if prefix else key
            if key.lower() in forbidden:
                hits.append(p)
            hits.extend(find_keys(value, forbidden, p))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            hits.extend(find_keys(value, forbidden, f"{prefix}[{i}]"))
    return hits


def find_forbidden_actions(obj: Any) -> list[str]:
    hits: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, str) and value.strip().upper() in FORBIDDEN_ACTION_VALUES:
                hits.append(key)
            hits.extend(find_forbidden_actions(value))
    elif isinstance(obj, list):
        for value in obj:
            hits.extend(find_forbidden_actions(value))
    return hits


def validate_index() -> list[str]:
    errors: list[str] = []
    reg = load_json(WATCH_REGISTRY_PATH)
    idx = load_json(INDEX_PATH)
    if idx.get("schema_version") != "1.0":
        errors.append("index schema_version must be 1.0")
    if set((idx.get("masters") or {}).keys()) != MASTER_IDS:
        errors.append("index must contain exactly five MASTER IDs")
    for mid, cfg in reg.get("master_producers", {}).items():
        ent = (idx.get("masters") or {}).get(mid) or {}
        if ent.get("watch_enabled") is not cfg.get("enabled"):
            errors.append(f"{mid}: watch_enabled mismatch")
        path = ent.get("latest_event_path")
        event_id = ent.get("latest_event_id")
        if path is None:
            if event_id is not None:
                errors.append(f"{mid}: latest_event_id exists without latest_event_path")
        else:
            p = ROOT / path
            if not p.exists():
                errors.append(f"{mid}: latest_event_path missing")
            else:
                latest = load_json(p)
                if latest.get("event_id") != event_id:
                    errors.append(f"{mid}: latest event id/path mismatch")
    for sid, ent in (idx.get("systems") or {}).items():
        path = ent.get("latest_event_path")
        event_id = ent.get("latest_event_id")
        if path is None:
            if event_id is not None:
                errors.append(f"{sid}: latest_event_id exists without latest_event_path")
        else:
            p = ROOT / path
            if not p.exists():
                errors.append(f"{sid}: latest_event_path missing")
            else:
                latest = load_json(p)
                if latest.get("event_id") != event_id:
                    errors.append(f"{sid}: latest event id/path mismatch")
    pol = idx.get("policy") or {}
    for key in ("empty_index_is_valid", "no_change_is_not_persisted", "watch_is_not_official", "no_historical_backfill", "latest_copy_is_lookup_only"):
        if pol.get(key) is not True:
            errors.append(f"index policy not locked true: {key}")
    return errors


def validate_event(event: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    schema = load_json(SCHEMA_PATH)
    os_registry = load_json(REGISTRY_PATH)
    watch_registry = load_json(WATCH_REGISTRY_PATH)

    for field in schema.get("required", []):
        if field not in event:
            errors.append(f"missing required field: {field}")
    if event.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    if event.get("event_class") not in schema.get("allowed_event_classes", []):
        errors.append("invalid event_class")
    if event.get("change_type") not in schema.get("allowed_change_types", []):
        errors.append("invalid change_type; unchanged/no-change events are forbidden")
    if event.get("event_level") not in schema.get("allowed_event_levels", []):
        errors.append("invalid event_level")
    if not isinstance(event.get("event_id"), str) or not event.get("event_id", "").strip():
        errors.append("event_id required")
    if not isinstance(event.get("correlation_key"), str) or not event.get("correlation_key", "").strip():
        errors.append("correlation_key required")

    detected = parse_dt(event.get("detected_at_kst"))
    if detected is None or detected.utcoffset() != timedelta(hours=9):
        errors.append("detected_at_kst must be offset-aware +09:00")
    if not isinstance(event.get("summary"), str) or not event.get("summary", "").strip():
        errors.append("summary required")
    if not isinstance(event.get("reason_codes"), list) or not event.get("reason_codes"):
        errors.append("reason_codes must be non-empty list")
    if not isinstance(event.get("evidence_refs"), list) or not event.get("evidence_refs"):
        errors.append("evidence_refs must be non-empty list")
    if not isinstance(event.get("watch_payload"), dict):
        errors.append("watch_payload must be object")

    producer = event.get("producer") or {}
    if not isinstance(producer, dict):
        errors.append("producer must be object")
        producer = {}
    ptype = producer.get("type")
    pid = producer.get("id")
    master_id = event.get("master_id")

    if ptype == "MASTER":
        if master_id not in MASTER_IDS or pid != master_id:
            errors.append("MASTER producer id/master_id mismatch")
        cfg = (watch_registry.get("master_producers") or {}).get(master_id) or {}
        if cfg.get("enabled") is not True:
            errors.append(f"{master_id}: WATCH producer disabled by canonical policy")
        expected = ((os_registry.get("masters") or {}).get(master_id) or {}).get("expected_version")
        if producer.get("version") != expected:
            errors.append(f"{master_id}: producer version must equal Registry expected_version")
        if event.get("event_class") not in cfg.get("allowed_event_classes", []):
            errors.append(f"{master_id}: event_class not allowed")
        if not producer.get("run_id"):
            errors.append("MASTER producer requires run_id")
    elif ptype == "SYSTEM":
        if master_id is not None:
            errors.append("SYSTEM event master_id must be null")
        cfg = (watch_registry.get("system_producers") or {}).get(pid) or {}
        if cfg.get("enabled") is not True:
            errors.append("unknown/disabled SYSTEM producer")
        if event.get("event_class") not in cfg.get("allowed_event_classes", []):
            errors.append("SYSTEM event_class not allowed")
    else:
        errors.append("producer.type must be MASTER or SYSTEM")

    notification = event.get("notification") or {}
    if not isinstance(notification, dict) or not isinstance(notification.get("eligible"), bool):
        errors.append("notification.eligible must be boolean")
    if ptype == "SYSTEM" and notification.get("eligible") is True:
        errors.append("SYSTEM events are non-notifying in V1")

    lineage = event.get("lineage") or {}
    required_lineage = {
        "canonical_rule_verified": True,
        "central_recalculation_applied": False,
        "reconstructed": False,
        "provisional": False,
        "official_state_write": False,
    }
    for key, value in required_lineage.items():
        if lineage.get(key) is not value:
            errors.append(f"lineage.{key} must be {value}")
    if lineage.get("source_kind") not in {"ACTUAL_WATCH_OUTPUT", "SYSTEM_STATUS_TRANSITION"}:
        errors.append("invalid lineage.source_kind")
    if ptype == "MASTER" and lineage.get("source_kind") != "ACTUAL_WATCH_OUTPUT":
        errors.append("MASTER event must come from ACTUAL_WATCH_OUTPUT")
    if ptype == "SYSTEM" and lineage.get("source_kind") != "SYSTEM_STATUS_TRANSITION":
        errors.append("SYSTEM event must come from SYSTEM_STATUS_TRANSITION")

    privacy = event.get("privacy") or {}
    if privacy.get("contains_personal_account_data") is not False:
        errors.append("contains_personal_account_data must be false")
    if privacy.get("public_repository_safe") is not True:
        errors.append("public_repository_safe must be true")

    sensitive = find_keys(event, SENSITIVE_KEYS)
    if sensitive:
        errors.append("sensitive keys forbidden: " + ", ".join(sensitive))
    execution = find_keys(event, EXECUTION_KEYS)
    if execution:
        errors.append("execution-order fields forbidden in WATCH Event Store: " + ", ".join(execution))
    action_hits = find_forbidden_actions(event)
    if action_hits:
        errors.append("ENTER/SMALL ENTER/ADD cannot be persisted as WATCH event action")
    return errors


def duplicate_event_id(event_id: str) -> bool:
    if not HISTORY_ROOT.exists():
        return False
    needle_a = f'"event_id":"{event_id}"'
    needle_b = f'"event_id": "{event_id}"'
    for path in HISTORY_ROOT.glob("**/*.jsonl"):
        text = path.read_text(encoding="utf-8")
        if needle_a in text or needle_b in text:
            return True
    return False


def publish(input_path: Path) -> None:
    event = load_json(input_path)
    errors = validate_event(event)
    if errors:
        raise SystemExit("WATCH_EVENT_PUBLISH=BLOCKED\n- " + "\n- ".join(errors))
    if duplicate_event_id(event["event_id"]):
        raise SystemExit("WATCH_EVENT_PUBLISH=BLOCKED: duplicate event_id")

    detected = parse_dt(event["detected_at_kst"])
    assert detected is not None
    producer = event["producer"]
    pid = producer["id"]
    month = detected.astimezone(KST).strftime("%Y-%m")
    history_rel = f"watch_events/history/{pid}/{month}.jsonl"
    history_path = ROOT / history_rel
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")

    latest_rel = f"watch_events/latest/{pid}.json"
    write_json(ROOT / latest_rel, event)

    idx = load_json(INDEX_PATH)
    idx["generated_kst"] = datetime.now(KST).isoformat(timespec="seconds")
    idx["event_count_total"] = int(idx.get("event_count_total", 0)) + 1
    if producer["type"] == "MASTER":
        ent = idx["masters"][event["master_id"]]
        ent["latest_event_id"] = event["event_id"]
        ent["latest_event_path"] = latest_rel
        ent["latest_detected_at_kst"] = event["detected_at_kst"]
        ent["event_count"] = int(ent.get("event_count", 0)) + 1
    else:
        ent = idx["systems"][pid]
        ent["latest_event_id"] = event["event_id"]
        ent["latest_event_path"] = latest_rel
        ent["event_count"] = int(ent.get("event_count", 0)) + 1
    write_json(INDEX_PATH, idx)
    print(f"WATCH_EVENT_PUBLISH=PASS event_id={event['event_id']} history={history_rel}")


def self_test() -> None:
    errors = validate_index()
    if errors:
        raise SystemExit("WATCH_EVENT_SELF_TEST=FAIL\n- " + "\n- ".join(errors))
    sample = {
        "schema_version": "1.0",
        "event_id": "SELFTEST-MARKET-001",
        "correlation_key": "SELFTEST-MARKET-RISK",
        "event_class": "MASTER_WATCH",
        "change_type": "NEW",
        "event_level": "LEVEL2",
        "producer": {"type": "MASTER", "id": "market", "version": "V1.2 FINAL", "run_id": "SELFTEST"},
        "master_id": "market",
        "detected_at_kst": "2026-09-07T13:20:00+09:00",
        "summary": "self-test meaningful change",
        "reason_codes": ["SELF_TEST"],
        "evidence_refs": [{"path": "shared_fact_vault/output/latest.json"}],
        "watch_payload": {"watch_status": "TEST_ONLY"},
        "notification": {"eligible": True},
        "lineage": {"canonical_rule_verified": True, "central_recalculation_applied": False, "reconstructed": False, "provisional": False, "official_state_write": False, "source_kind": "ACTUAL_WATCH_OUTPUT"},
        "privacy": {"contains_personal_account_data": False, "public_repository_safe": True}
    }
    errs = validate_event(sample)
    if errs:
        raise SystemExit("WATCH_EVENT_SELF_TEST=FAIL valid sample rejected\n- " + "\n- ".join(errs))
    btc = json.loads(json.dumps(sample))
    btc["producer"].update({"id": "btc_trend", "version": "V2.6 PRODUCTION"})
    btc["master_id"] = "btc_trend"
    if not validate_event(btc):
        raise SystemExit("WATCH_EVENT_SELF_TEST=FAIL BTC hourly WATCH was not blocked")
    trading = json.loads(json.dumps(sample))
    trading["producer"].update({"id": "trading", "version": "CURRENT + TIME VALIDITY V2.1 OVERLAY"})
    trading["master_id"] = "trading"
    if not validate_event(trading):
        raise SystemExit("WATCH_EVENT_SELF_TEST=FAIL TRADING recurring WATCH was not blocked")
    bad = json.loads(json.dumps(sample))
    bad["watch_payload"] = {"action": "ENTER"}
    if not validate_event(bad):
        raise SystemExit("WATCH_EVENT_SELF_TEST=FAIL ENTER action was not blocked")
    execution = json.loads(json.dumps(sample))
    execution["watch_payload"] = {"entry": 1, "sl": 0.9, "tp1": 1.3}
    if not validate_event(execution):
        raise SystemExit("WATCH_EVENT_SELF_TEST=FAIL execution fields were not blocked")
    print("WATCH_EVENT_SELF_TEST=PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["validate-latest", "self-test", "publish"])
    parser.add_argument("--input", type=Path)
    args = parser.parse_args()
    if args.mode == "validate-latest":
        errors = validate_index()
        if errors:
            print("WATCH_EVENT_VALIDATION=FAIL")
            for e in errors:
                print("-", e)
            sys.exit(1)
        print("WATCH_EVENT_VALIDATION=PASS")
    elif args.mode == "self-test":
        self_test()
    else:
        if args.input is None:
            raise SystemExit("--input required for publish")
        publish(args.input)


if __name__ == "__main__":
    main()
