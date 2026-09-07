#!/usr/bin/env python3
"""Publish and validate MONEY MASTER OS V2 OFFICIAL State.

Standard-library only. This tool persists actual OFFICIAL MASTER decisions; it never
calculates a market direction or reconstructs missing state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "money_master_os/registry/MASTER_REGISTRY.json"
INDEX_PATH = ROOT / "official_state/latest/index.json"
LATEST_DIR = ROOT / "official_state/latest"
HISTORY_DIR = ROOT / "official_state/history"
MASTER_IDS = {"market", "btc_trend", "alt_top100", "alt_final20", "trading"}
STATE_STATUSES = {"STORED", "NO_STORED_OFFICIAL_RUN", "INVALID"}
FRESHNESS = {"CURRENT", "EXPIRED", "UNKNOWN", "NOT_APPLICABLE"}
KST = timezone(timedelta(hours=9))
SENSITIVE_KEYS = {
    "account_balance",
    "actual_position_size",
    "account_identifier",
    "api_key",
    "api_secret",
    "private_execution_details",
    "secret",
    "password",
}


def load_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"cannot parse {path.relative_to(ROOT)}: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must contain a JSON object")
    return obj


def write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def git_blob_sha(path: Path) -> str:
    content = path.read_bytes()
    header = f"blob {len(content)}\0".encode()
    return hashlib.sha1(header + content).hexdigest()


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
    if dt.tzinfo is None:
        return None
    return dt


def find_sensitive_keys(obj: Any, prefix: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            p = f"{prefix}.{key}" if prefix else key
            if key.lower() in SENSITIVE_KEYS:
                hits.append(p)
            hits.extend(find_sensitive_keys(value, p))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            hits.extend(find_sensitive_keys(value, f"{prefix}[{i}]"))
    return hits


def registry() -> dict[str, Any]:
    reg = load_json(REGISTRY_PATH)
    masters = reg.get("masters") or {}
    if set(masters) != MASTER_IDS:
        raise ValueError("MASTER Registry must contain exactly five canonical IDs")
    return reg


def validate_state(state: dict[str, Any], *, allow_placeholder: bool = True) -> list[str]:
    errors: list[str] = []
    reg = registry()
    masters = reg["masters"]

    if state.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    status = state.get("state_status")
    if status not in STATE_STATUSES:
        errors.append(f"invalid state_status={status}")
    mid = state.get("master_id")
    if mid not in MASTER_IDS:
        errors.append(f"invalid master_id={mid}")
        return errors
    entry = masters[mid]
    if entry.get("status") != "READY":
        errors.append(f"{mid}: registry status must be READY")
    if state.get("master_version") != entry.get("expected_version"):
        errors.append(f"{mid}: master_version must equal Registry expected_version")
    if state.get("source_path") != entry.get("source_path"):
        errors.append(f"{mid}: source_path mismatch")
    if state.get("contract_path") != entry.get("contract_path"):
        errors.append(f"{mid}: contract_path mismatch")

    source_path = ROOT / str(entry.get("source_path"))
    if not source_path.exists():
        errors.append(f"{mid}: canonical source missing")
    else:
        declared_sha = state.get("current_canonical_source_sha")
        if declared_sha and declared_sha != git_blob_sha(source_path):
            errors.append(f"{mid}: current_canonical_source_sha mismatch")

    run = state.get("run")
    if not isinstance(run, dict):
        errors.append("run must be an object")
        run = {}
    if run.get("run_type") != "OFFICIAL":
        errors.append("run.run_type must be OFFICIAL")

    validity = state.get("validity")
    if not isinstance(validity, dict):
        errors.append("validity must be an object")
        validity = {}
    if validity.get("freshness_status") not in FRESHNESS:
        errors.append("invalid validity.freshness_status")
    for field in ("valid_until_kst", "next_official_run_kst"):
        v = validity.get(field)
        if v is not None and parse_dt(v) is None:
            errors.append(f"{field} must be offset-aware ISO8601 or null")

    dq = state.get("data_quality")
    if not isinstance(dq, dict):
        errors.append("data_quality must be an object")
        dq = {}
    cov = dq.get("coverage_pct")
    if cov is not None and (not isinstance(cov, (int, float)) or isinstance(cov, bool) or cov < 0 or cov > 100):
        errors.append("coverage_pct must be null or 0..100")
    shp = dq.get("source_health_snapshot_pointer")
    if shp not in (None, "source_health/output/latest.json"):
        errors.append("Source Health pointer must be null or source_health/output/latest.json")
    for list_field in ("known_na", "stale_sources", "time_mismatch_warnings"):
        if not isinstance(dq.get(list_field), list):
            errors.append(f"data_quality.{list_field} must be a list")

    decision = state.get("decision")
    if not isinstance(decision, dict):
        errors.append("decision must be an object")
        decision = {}
    for list_field in ("risk_veto", "invalidations"):
        if not isinstance(decision.get(list_field), list):
            errors.append(f"decision.{list_field} must be a list")

    if not isinstance(state.get("core_state"), dict):
        errors.append("core_state must be an object")

    lineage = state.get("lineage")
    if not isinstance(lineage, dict):
        errors.append("lineage must be an object")
        lineage = {}
    if lineage.get("canonical_identity_verified") is not True:
        errors.append("canonical_identity_verified must be true")
    if lineage.get("cross_master_decision_dependency") is not False:
        errors.append("cross_master_decision_dependency must be false")
    if lineage.get("run_record_source") == "source_health/output/latest.json":
        errors.append("Source Health cannot be the OFFICIAL run record source")

    privacy = state.get("privacy")
    if not isinstance(privacy, dict):
        errors.append("privacy must be an object")
        privacy = {}
    if privacy.get("contains_personal_account_data") is not False:
        errors.append("contains_personal_account_data must be false")
    if privacy.get("public_repository_safe") is not True:
        errors.append("public_repository_safe must be true")
    hits = find_sensitive_keys(state)
    if hits:
        errors.append("sensitive public-repo keys forbidden: " + ", ".join(hits))

    if not isinstance(state.get("do_not_reconstruct"), list):
        errors.append("do_not_reconstruct must be a list")

    if status == "STORED":
        if lineage.get("state_origin") != "ACTUAL_STORED_RUN":
            errors.append("STORED requires lineage.state_origin=ACTUAL_STORED_RUN")
        if not run.get("run_id"):
            errors.append("STORED requires run.run_id")
        if parse_dt(run.get("executed_kst")) is None:
            errors.append("STORED requires offset-aware run.executed_kst")
        updated = run.get("updated_kst")
        if updated is not None and parse_dt(updated) is None:
            errors.append("run.updated_kst must be offset-aware ISO8601 or null")
    elif status == "NO_STORED_OFFICIAL_RUN":
        if not allow_placeholder:
            errors.append("publisher accepts STORED states only")
        if lineage.get("state_origin") != "NO_STORED_RUN":
            errors.append("NO_STORED_OFFICIAL_RUN requires lineage.state_origin=NO_STORED_RUN")
        if any(run.get(k) is not None for k in ("run_id", "executed_kst", "updated_kst", "prior_official_run_id")):
            errors.append("NO_STORED_OFFICIAL_RUN must not contain run values")
        if any(decision.get(k) is not None for k in ("direction", "permission_or_environment", "action")):
            errors.append("NO_STORED_OFFICIAL_RUN must not contain decision values")
        if dq.get("coverage_pct") is not None or dq.get("confidence") is not None:
            errors.append("NO_STORED_OFFICIAL_RUN must not invent coverage/confidence")
    return errors


def validate_latest() -> list[str]:
    errors: list[str] = []
    idx = load_json(INDEX_PATH)
    if idx.get("schema_version") != "1.0" or idx.get("master_count") != 5:
        errors.append("latest index schema/master_count invalid")
    entries = idx.get("masters") or {}
    if set(entries) != MASTER_IDS:
        errors.append("latest index must contain exactly five MASTER IDs")
        return errors
    for mid in sorted(MASTER_IDS):
        path = LATEST_DIR / f"{mid}.json"
        if not path.exists():
            errors.append(f"missing latest state: {mid}")
            continue
        try:
            state = load_json(path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        errors.extend(f"{mid}: {e}" for e in validate_state(state, allow_placeholder=True))
        ent = entries[mid]
        if ent.get("state_path") != f"official_state/latest/{mid}.json":
            errors.append(f"{mid}: index state_path mismatch")
        if ent.get("state_status") != state.get("state_status"):
            errors.append(f"{mid}: index state_status mismatch")
        if ent.get("latest_official_run_id") != (state.get("run") or {}).get("run_id"):
            errors.append(f"{mid}: index latest_official_run_id mismatch")
        if ent.get("executed_kst") != (state.get("run") or {}).get("executed_kst"):
            errors.append(f"{mid}: index executed_kst mismatch")
        if ent.get("freshness_status") != (state.get("validity") or {}).get("freshness_status"):
            errors.append(f"{mid}: index freshness_status mismatch")
    policy = idx.get("policy") or {}
    for key in (
        "missing_official_run_is_not_reconstructed",
        "source_health_is_not_official_decision",
        "watch_is_not_official",
        "provisional_is_not_official",
        "cross_master_decisions_remain_independent",
        "public_repo_private_trading_state_forbidden",
    ):
        if policy.get(key) is not True:
            errors.append(f"index policy not locked true: {key}")
    return errors


def publish(input_path: Path) -> None:
    state = load_json(input_path)
    errors = validate_state(state, allow_placeholder=False)
    if errors:
        raise SystemExit("OFFICIAL_STATE_PUBLISH=BLOCKED\n- " + "\n- ".join(errors))
    mid = state["master_id"]
    latest_path = LATEST_DIR / f"{mid}.json"
    previous = load_json(latest_path) if latest_path.exists() else None
    prev_run_id = None
    prev_dt = None
    if previous and previous.get("state_status") == "STORED":
        prev_run_id = (previous.get("run") or {}).get("run_id")
        prev_dt = parse_dt((previous.get("run") or {}).get("executed_kst"))
    run = state["run"]
    current_dt = parse_dt(run.get("executed_kst"))
    if current_dt is None:
        raise SystemExit("OFFICIAL_STATE_PUBLISH=BLOCKED: invalid executed_kst")
    if prev_dt is not None and current_dt <= prev_dt:
        raise SystemExit("OFFICIAL_STATE_PUBLISH=BLOCKED: run time must be newer than current latest")
    if prev_run_id:
        declared_prior = run.get("prior_official_run_id")
        if declared_prior not in (None, prev_run_id):
            raise SystemExit("OFFICIAL_STATE_PUBLISH=BLOCKED: prior_official_run_id mismatch")
        run["prior_official_run_id"] = prev_run_id

    month = current_dt.astimezone(KST).strftime("%Y-%m")
    history_rel = f"official_state/history/{mid}/{month}.jsonl"
    history_path = ROOT / history_rel
    state["lineage"]["history_pointer"] = history_rel
    history_path.parent.mkdir(parents=True, exist_ok=True)
    if history_path.exists():
        existing = history_path.read_text(encoding="utf-8")
        if f'"run_id":"{run["run_id"]}"' in existing or f'"run_id": "{run["run_id"]}"' in existing:
            raise SystemExit("OFFICIAL_STATE_PUBLISH=BLOCKED: duplicate run_id in history")
    with history_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n")
    write_json(latest_path, state)

    idx = load_json(INDEX_PATH)
    idx["generated_kst"] = datetime.now(KST).isoformat(timespec="seconds")
    idx["masters"][mid] = {
        "state_path": f"official_state/latest/{mid}.json",
        "state_status": "STORED",
        "latest_official_run_id": run.get("run_id"),
        "executed_kst": run.get("executed_kst"),
        "freshness_status": state["validity"]["freshness_status"],
    }
    write_json(INDEX_PATH, idx)
    print(f"OFFICIAL_STATE_PUBLISH=PASS master={mid} run_id={run.get('run_id')}")


def self_test() -> None:
    idx_errors = validate_latest()
    if idx_errors:
        raise SystemExit("OFFICIAL_STATE_SELF_TEST=FAIL\n- " + "\n- ".join(idx_errors))
    market = load_json(LATEST_DIR / "market.json")
    if market.get("state_status") != "STORED":
        raise SystemExit("OFFICIAL_STATE_SELF_TEST=FAIL market migration missing")
    for mid in MASTER_IDS - {"market"}:
        state = load_json(LATEST_DIR / f"{mid}.json")
        if state.get("state_status") != "NO_STORED_OFFICIAL_RUN":
            raise SystemExit(f"OFFICIAL_STATE_SELF_TEST=FAIL unexpected synthetic state for {mid}")
    print("OFFICIAL_STATE_SELF_TEST=PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["validate-latest", "self-test", "publish"], required=True)
    parser.add_argument("--input", type=Path)
    args = parser.parse_args()
    if args.mode == "validate-latest":
        errors = validate_latest()
        if errors:
            print("OFFICIAL_STATE_VALIDATION=FAIL")
            for e in errors:
                print("-", e)
            raise SystemExit(1)
        print("OFFICIAL_STATE_VALIDATION=PASS")
    elif args.mode == "self-test":
        self_test()
    else:
        if args.input is None:
            raise SystemExit("--input is required for publish")
        publish(args.input)


if __name__ == "__main__":
    main()
