from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = HERE / "R26_FORWARD_LEDGER_INTEGRITY_SPEC_V1.json"
STATE = HERE / "ledger_integrity_state.json"
RUNS = HERE / "ledger_integrity_runs.jsonl"
REPORT = HERE / "ledger_integrity_report.json"
TRACKER_STATE = HERE / "state.json"

EXPECTED_SPEC_BLOB = "49efdab5da40d88f03a3dcf9ede3830ef31f71e3"
STRICT_PHASE = "STRICT_FORWARD"


def canonical_bytes(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_obj(obj) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def git_blob(path: Path) -> str:
    return subprocess.check_output(["git", "hash-object", str(path)], text=True).strip()


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or not path.read_text(encoding="utf-8-sig").strip():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return []
        return [{str(k): "" if v is None else str(v) for k, v in raw.items()} for raw in reader]


def strict_rows(filename: str) -> list[dict[str, str]]:
    return [r for r in read_rows(HERE / filename) if r.get("phase") == STRICT_PHASE]


def bool_true(v: str) -> bool:
    return str(v).strip().lower() in {"true", "1", "yes", "y"}


def make_key(row: dict[str, str], fields: list[str], label: str) -> str:
    missing = [f for f in fields if f not in row]
    if missing:
        raise RuntimeError(f"{label}:MISSING_KEY_FIELDS:{missing}")
    vals = [row.get(f, "") for f in fields]
    if any(v == "" for v in vals):
        raise RuntimeError(f"{label}:EMPTY_KEY_FIELD:{dict(zip(fields, vals))}")
    return json.dumps(vals, ensure_ascii=False, separators=(",", ":"))


def row_hash(row: dict[str, str], fields: list[str] | None = None) -> str:
    if fields is None:
        payload = {k: row[k] for k in sorted(row)}
    else:
        missing = [f for f in fields if f not in row]
        if missing:
            raise RuntimeError(f"MISSING_HASH_FIELDS:{missing}")
        payload = {f: row.get(f, "") for f in fields}
    return sha256_obj(payload)


def build_map(rows: list[dict[str, str]], key_fields: list[str], label: str, hash_fields: list[str] | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in rows:
        k = make_key(row, key_fields, label)
        if k in out:
            raise RuntimeError(f"{label}:DUPLICATE_KEY:{k}")
        out[k] = row_hash(row, hash_fields)
    return out


def verify_subset(previous: dict[str, str], current: dict[str, str], label: str) -> dict:
    missing = sorted(k for k in previous if k not in current)
    changed = sorted(k for k, h in previous.items() if k in current and current[k] != h)
    if missing or changed:
        raise RuntimeError(f"{label}:IMMUTABILITY_FAIL:missing={missing[:5]} changed={changed[:5]}")
    return {
        "previous": len(previous),
        "current": len(current),
        "new": len(current) - len(previous),
        "missing": 0,
        "changed": 0,
        "pass": True,
    }


def verify_prior_chain(state: dict, log_rows: list[dict]) -> None:
    if not log_rows:
        raise RuntimeError("AUDIT_CHAIN_EMPTY_WITH_STATE")
    prev = ""
    for i, rec in enumerate(log_rows, start=1):
        actual_chain = str(rec.get("chain_sha256", ""))
        core = {k: v for k, v in rec.items() if k != "chain_sha256"}
        if str(core.get("prev_chain_sha256", "")) != prev:
            raise RuntimeError(f"AUDIT_CHAIN_PREV_MISMATCH:{i}")
        if actual_chain != sha256_obj(core):
            raise RuntimeError(f"AUDIT_CHAIN_HASH_MISMATCH:{i}")
        prev = actual_chain
    if str(state.get("last_chain_sha256", "")) != prev:
        raise RuntimeError("STATE_LAST_CHAIN_MISMATCH")
    if int(state.get("generation", -1)) != int(log_rows[-1].get("generation", -2)):
        raise RuntimeError("STATE_GENERATION_MISMATCH")
    core_state = {k: v for k, v in state.items() if k != "last_chain_sha256"}
    if sha256_obj(core_state) != str(log_rows[-1].get("state_sha256", "")):
        raise RuntimeError("STATE_HASH_MISMATCH")


def load_log() -> list[dict]:
    if not RUNS.exists():
        return []
    rows = []
    for n, line in enumerate(RUNS.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception as exc:
            raise RuntimeError(f"AUDIT_LOG_JSON_FAIL:{n}:{exc}") from exc
    return rows


def write_report(payload: dict) -> None:
    REPORT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_guard() -> dict:
    spec_blob = git_blob(SPEC)
    if spec_blob != EXPECTED_SPEC_BLOB:
        raise RuntimeError(f"LEDGER_SPEC_IDENTITY_FAIL:{spec_blob}")
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    tracker = json.loads(TRACKER_STATE.read_text(encoding="utf-8"))
    asof = str(tracker["asof_utc"])
    if str(tracker.get("strict_forward_start", "")).replace("+00:00", "Z") != str(spec["strict_forward_start"]):
        raise RuntimeError("LEDGER_STRICT_START_MISMATCH")

    p = spec["protected_ledgers"]
    det = build_map(strict_rows(p["detections"]["file"]), p["detections"]["key_fields"], "detections")
    evt = build_map(strict_rows(p["events"]["file"]), p["events"]["key_fields"], "events")
    sed = build_map(strict_rows(p["seeds"]["file"]), p["seeds"]["key_fields"], "seeds")
    tx = build_map(strict_rows(p["transactions"]["file"]), p["transactions"]["key_fields"], "transactions")

    position_rows = strict_rows(p["position_identity"]["file"])
    pos_identity = build_map(position_rows, p["position_identity"]["key_fields"], "position_identity", p["position_identity"]["hash_fields"])
    resolved_rows = [r for r in position_rows if bool_true(r.get(p["resolved_positions"]["resolved_field"], ""))]
    resolved = build_map(resolved_rows, p["resolved_positions"]["key_fields"], "resolved_positions")

    current = {
        "detections": det,
        "events": evt,
        "seeds": sed,
        "transactions": tx,
        "position_identities": pos_identity,
        "resolved_positions": resolved,
    }
    counts = {k: len(v) for k, v in current.items()}

    state_exists = STATE.exists()
    log_exists = RUNS.exists()
    if state_exists != log_exists:
        raise RuntimeError("LEDGER_BASELINE_PARTIAL_FILES")

    bootstrap = not state_exists
    deltas = {}
    previous_chain = ""
    if bootstrap:
        if any(counts.values()):
            raise RuntimeError(f"BOOTSTRAP_FORBIDDEN_AFTER_STRICT_EVIDENCE:{counts}")
        generation = 1
        initialized_at = asof
        for name, mapping in current.items():
            deltas[name] = {"previous": 0, "current": len(mapping), "new": len(mapping), "missing": 0, "changed": 0, "pass": True}
    else:
        previous_state = json.loads(STATE.read_text(encoding="utf-8"))
        log_rows = load_log()
        verify_prior_chain(previous_state, log_rows)
        if previous_state.get("spec") != spec["spec"] or previous_state.get("spec_blob") != EXPECTED_SPEC_BLOB:
            raise RuntimeError("LEDGER_PREVIOUS_SPEC_IDENTITY_FAIL")
        generation = int(previous_state["generation"]) + 1
        initialized_at = str(previous_state["initialized_at_utc"])
        previous_chain = str(previous_state["last_chain_sha256"])
        prior_maps = previous_state["protected"]
        for name, mapping in current.items():
            deltas[name] = verify_subset(prior_maps.get(name, {}), mapping, name)

    core_state = {
        "version": 1,
        "spec": spec["spec"],
        "spec_blob": EXPECTED_SPEC_BLOB,
        "strict_forward_start": spec["strict_forward_start"],
        "generation": generation,
        "initialized_at_utc": initialized_at,
        "last_verified_at_utc": asof,
        "protected": current,
        "counts": counts,
    }
    state_hash = sha256_obj(core_state)
    chain_core = {
        "generation": generation,
        "asof_utc": asof,
        "prev_chain_sha256": previous_chain,
        "state_sha256": state_hash,
        "counts": counts,
    }
    chain_hash = sha256_obj(chain_core)
    new_state = dict(core_state)
    new_state["last_chain_sha256"] = chain_hash

    STATE.write_text(json.dumps(new_state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with RUNS.open("a", encoding="utf-8") as f:
        f.write(json.dumps({**chain_core, "chain_sha256": chain_hash}, sort_keys=True, separators=(",", ":")) + "\n")

    report = {
        "spec": spec["spec"],
        "spec_blob": EXPECTED_SPEC_BLOB,
        "asof_utc": asof,
        "generation": generation,
        "bootstrap": bootstrap,
        "pass": True,
        "counts": counts,
        "deltas": deltas,
        "state_sha256": state_hash,
        "previous_chain_sha256": previous_chain,
        "chain_sha256": chain_hash,
        "rules": {
            "strict_only": True,
            "immutable_detections_events_seeds_transactions": True,
            "open_position_dynamic_fields_allowed": True,
            "position_identity_immutable": True,
            "resolved_position_full_row_immutable": True,
            "retroactive_rebaseline_forbidden": True,
        },
    }
    write_report(report)
    return report


def main() -> None:
    try:
        report = run_guard()
    except BaseException as exc:
        failure = {
            "pass": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "spec_expected_blob": EXPECTED_SPEC_BLOB,
        }
        try:
            if TRACKER_STATE.exists():
                failure["asof_utc"] = json.loads(TRACKER_STATE.read_text(encoding="utf-8")).get("asof_utc")
            write_report(failure)
        finally:
            print(json.dumps(failure, indent=2, sort_keys=True))
        raise
    print(json.dumps(report, indent=2, sort_keys=True))
    print("R26_FORWARD_LEDGER_INTEGRITY_GUARD_PASS")


if __name__ == "__main__":
    main()
