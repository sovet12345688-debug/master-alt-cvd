#!/usr/bin/env python3
import argparse
import hashlib
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "money_master_os/acceptance/LONG_RUN_STABILITY_V1.json"
ACCEPTANCE = ROOT / "money_master_os/acceptance/ACCEPTANCE_CRITERIA_V1.json"
REGISTRY = ROOT / "money_master_os/registry/MASTER_REGISTRY.json"
SH_REGISTRY = ROOT / "source_health/registry.json"
SH_LATEST = ROOT / "source_health/output/latest.json"
FV_REGISTRY = ROOT / "shared_fact_vault/registry.json"
FV_LATEST = ROOT / "shared_fact_vault/output/latest.json"
RUNTIME = ROOT / "p2_long_run"
STATE = RUNTIME / "state.json"
LATEST = RUNTIME / "latest.json"
ACCEPTANCE_STATE = ROOT / "state/money_master_os_p2_acceptance_latest.json"

BAD_CORE = {"FAILED", "STALE", "MISSING", "UNKNOWN"}
TERMINAL = {"FINAL_PASS", "FINAL_FAIL"}


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_dt(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint_paths():
    registry = load_json(REGISTRY)
    paths = {
        REGISTRY,
        ROOT / "money_master_os/shared/COMMON_RULES.md",
        ROOT / "money_master_os/shared/DATA_POLICY.md",
        ROOT / "money_master_os/shared/RISK_GATE.md",
        SH_REGISTRY,
        FV_REGISTRY,
        ROOT / "shared_fact_vault/schema.json",
        ROOT / "official_state/schema.json",
        ROOT / "watch_events/registry.json",
        ROOT / "watch_events/schema.json",
        ACCEPTANCE,
        CONTRACT,
    }
    for entry in registry.get("masters", {}).values():
        for key in ("manifest_path", "source_path", "contract_path"):
            rel = entry.get(key)
            if rel:
                paths.add(ROOT / rel)
    return sorted(paths, key=lambda p: str(p.relative_to(ROOT)))


def system_fingerprint():
    h = hashlib.sha256()
    receipt = []
    for path in fingerprint_paths():
        rel = str(path.relative_to(ROOT))
        if not path.exists():
            raise FileNotFoundError(rel)
        digest = sha256_file(path)
        receipt.append({"path": rel, "sha256": digest})
        h.update(rel.encode())
        h.update(b"\0")
        h.update(digest.encode())
        h.update(b"\n")
    return h.hexdigest(), receipt


def run_acceptance():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "money_master_os/tools/run_p2_acceptance.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    lines = (proc.stdout or "").splitlines()
    errors = [line.replace("- ERROR: ", "", 1) for line in lines if line.startswith("- ERROR: ")]
    warnings = [line.replace("- WARNING: ", "", 1) for line in lines if line.startswith("- WARNING: ")]
    return {
        "returncode": proc.returncode,
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "errors": errors,
        "warnings": warnings,
        "stdout_tail": lines[-12:],
    }


def core_unsafe_sources(sh_registry, sh_latest):
    out = []
    latest = sh_latest.get("sources", {})
    for sid, spec in sh_registry.get("sources", {}).items():
        impacts = spec.get("impact", {})
        if not any(v == "CORE" for v in impacts.values()):
            continue
        status = (latest.get(sid) or {}).get("status", "MISSING")
        if status in BAD_CORE:
            out.append({"source_id": sid, "status": status})
    return out


def age_minutes(ts, now):
    dt = parse_dt(ts)
    if not dt:
        return None
    return round((now - dt).total_seconds() / 60.0, 1)


def initial_state(now, fingerprint, fp_receipt):
    return {
        "schema_version": "1.0",
        "long_run_contract": "MONEY_MASTER_OS_V2_P2_LONG_RUN_V1",
        "run_id": f"P2LR-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}",
        "status": "COLLECTING",
        "started_at_utc": iso(now),
        "baseline_fingerprint": fingerprint,
        "baseline_fingerprint_files": fp_receipt,
        "latest_checkpoint_at_utc": None,
        "checkpoint_count": 0,
        "source_health_cycle_ids": [],
        "shared_fact_cycle_ids": [],
        "acceptance_pass_count": 0,
        "consecutive_core_unsafe": 0,
        "consecutive_monitoring_stale": 0,
        "hard_fail_reasons": [],
        "finalized_at_utc": None,
    }


def evaluate_progress(state, checkpoint, contract, now):
    state = json.loads(json.dumps(state))
    state["checkpoint_count"] += 1
    state["latest_checkpoint_at_utc"] = checkpoint["checkpoint_at_utc"]

    sh_id = checkpoint.get("source_health_evaluated_at_utc")
    fv_id = checkpoint.get("shared_fact_generated_at_utc")
    if sh_id and sh_id not in state["source_health_cycle_ids"]:
        state["source_health_cycle_ids"].append(sh_id)
    if fv_id and fv_id not in state["shared_fact_cycle_ids"]:
        state["shared_fact_cycle_ids"].append(fv_id)

    if checkpoint["acceptance"]["status"] == "PASS":
        state["acceptance_pass_count"] += 1

    state["consecutive_core_unsafe"] = state["consecutive_core_unsafe"] + 1 if checkpoint["core_unsafe_sources"] else 0
    state["consecutive_monitoring_stale"] = state["consecutive_monitoring_stale"] + 1 if checkpoint["monitoring_snapshot_stale"] else 0

    hard = list(checkpoint["immediate_hard_fail_reasons"])
    if state["consecutive_core_unsafe"] >= 2:
        hard.append("CORE_SOURCE_UNSAFE_TWO_CONSECUTIVE_CHECKPOINTS")
    if state["consecutive_monitoring_stale"] >= 2:
        hard.append("MONITORING_SNAPSHOT_STALE_TWO_CONSECUTIVE_CHECKPOINTS")
    for reason in hard:
        if reason not in state["hard_fail_reasons"]:
            state["hard_fail_reasons"].append(reason)

    elapsed = round((now - parse_dt(state["started_at_utc"])).total_seconds() / 3600.0, 3)
    minimums = contract["minimums"]
    progress = {
        "observation_hours": elapsed,
        "source_health_cycles": len(state["source_health_cycle_ids"]),
        "shared_fact_cycles": len(state["shared_fact_cycle_ids"]),
        "successful_acceptance_checkpoints": state["acceptance_pass_count"],
        "required": minimums,
    }

    if state["hard_fail_reasons"]:
        status = "FINAL_FAIL"
    elif checkpoint["core_unsafe_sources"] or checkpoint["monitoring_snapshot_stale"]:
        status = "AT_RISK"
    else:
        enough = (
            elapsed >= minimums["observation_hours"]
            and progress["source_health_cycles"] >= minimums["distinct_source_health_cycles"]
            and progress["shared_fact_cycles"] >= minimums["distinct_shared_fact_cycles"]
            and progress["successful_acceptance_checkpoints"] >= minimums["successful_acceptance_checkpoints"]
            and checkpoint["acceptance"]["status"] == "PASS"
        )
        if enough:
            status = "FINAL_PASS"
        elif elapsed >= minimums["observation_hours"]:
            status = "COLLECTING_INSUFFICIENT_CYCLES"
        else:
            status = "COLLECTING"

    state["status"] = status
    if status in TERMINAL and not state.get("finalized_at_utc"):
        state["finalized_at_utc"] = iso(now)
    return state, progress


def build_checkpoint(state, now):
    contract = load_json(CONTRACT)
    sh_reg = load_json(SH_REGISTRY)
    sh = load_json(SH_LATEST)
    fv = load_json(FV_LATEST)
    fingerprint, fp_receipt = system_fingerprint()
    acceptance = run_acceptance()

    sh_ts = sh.get("evaluated_at_utc")
    fv_ts = fv.get("generated_at_utc")
    sh_age = age_minutes(sh_ts, now)
    fv_age = age_minutes(fv_ts, now)
    fresh_cfg = contract["snapshot_freshness"]
    stale = (
        sh_age is None
        or fv_age is None
        or sh_age > fresh_cfg["max_source_health_snapshot_age_minutes"]
        or fv_age > fresh_cfg["max_shared_fact_snapshot_age_minutes"]
    )
    core_bad = core_unsafe_sources(sh_reg, sh)

    immediate = []
    if fingerprint != state["baseline_fingerprint"]:
        immediate.append("MASTER_OR_CANONICAL_DRIFT_RESET_REQUIRED")

    # A single transient CORE outage is allowed to be AT_RISK. Any other baseline
    # acceptance failure is an immediate long-run failure.
    if acceptance["status"] == "FAIL":
        if not acceptance["errors"]:
            immediate.append("BASELINE_ACCEPTANCE_FAILURE_WITHOUT_REASON")
        else:
            non_a3 = [e for e in acceptance["errors"] if not e.startswith("A3 ")]
            if non_a3:
                immediate.append("NON_A3_BASELINE_ACCEPTANCE_FAILURE")

    checkpoint = {
        "schema_version": "1.0",
        "checkpoint_id": f"LRCP-{now.strftime('%Y%m%dT%H%M%SZ')}",
        "run_id": state["run_id"],
        "checkpoint_at_utc": iso(now),
        "system_fingerprint": fingerprint,
        "fingerprint_matches_baseline": fingerprint == state["baseline_fingerprint"],
        "source_health_evaluated_at_utc": sh_ts,
        "source_health_age_minutes": sh_age,
        "source_health_overall": sh.get("overall"),
        "source_health_counts": sh.get("counts", {}),
        "core_unsafe_sources": core_bad,
        "shared_fact_generated_at_utc": fv_ts,
        "shared_fact_age_minutes": fv_age,
        "shared_fact_status": fv.get("vault_status"),
        "shared_fact_source_coverage_pct": fv.get("registered_fact_source_coverage_pct"),
        "shared_fact_count": len(fv.get("facts") or []),
        "monitoring_snapshot_stale": stale,
        "acceptance": acceptance,
        "immediate_hard_fail_reasons": immediate,
        "fingerprint_file_count": len(fp_receipt),
    }
    return checkpoint


def checkpoint_path(now):
    return RUNTIME / "checkpoints" / f"{now.strftime('%Y-%m')}.jsonl"


def write_runtime(state, checkpoint, progress, now):
    RUNTIME.mkdir(parents=True, exist_ok=True)
    cp = checkpoint_path(now)
    cp.parent.mkdir(parents=True, exist_ok=True)
    with cp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(checkpoint, ensure_ascii=False, separators=(",", ":")) + "\n")

    summary = {
        "schema_version": "1.0",
        "system": "MONEY_MASTER_OS_P2_LONG_RUN",
        "run_id": state["run_id"],
        "status": state["status"],
        "started_at_utc": state["started_at_utc"],
        "latest_checkpoint_at_utc": state["latest_checkpoint_at_utc"],
        "finalized_at_utc": state.get("finalized_at_utc"),
        "progress": progress,
        "checkpoint_count": state["checkpoint_count"],
        "consecutive_core_unsafe": state["consecutive_core_unsafe"],
        "consecutive_monitoring_stale": state["consecutive_monitoring_stale"],
        "hard_fail_reasons": state["hard_fail_reasons"],
        "latest_checkpoint": checkpoint,
        "rule": "No synthetic historical backfill. Terminal status requires explicit reset before a new observation window."
    }
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    LATEST.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if state["status"] in TERMINAL and ACCEPTANCE_STATE.exists():
        a = load_json(ACCEPTANCE_STATE)
        a["p2_final_status"] = "PASS" if state["status"] == "FINAL_PASS" else "FAIL"
        a["long_run_gate"] = {
            "required_for_p2_final_pass": True,
            "status": state["status"],
            "run_id": state["run_id"],
            "started_at_utc": state["started_at_utc"],
            "finalized_at_utc": state.get("finalized_at_utc"),
            "progress": progress,
            "hard_fail_reasons": state["hard_fail_reasons"],
            "latest_path": "p2_long_run/latest.json"
        }
        ACCEPTANCE_STATE.write_text(json.dumps(a, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def self_test():
    contract = load_json(CONTRACT)
    base = {
        "schema_version": "1.0", "long_run_contract": contract["long_run_id"], "run_id": "TEST",
        "status": "COLLECTING", "started_at_utc": "2026-09-01T00:00:00Z", "baseline_fingerprint": "abc",
        "baseline_fingerprint_files": [], "latest_checkpoint_at_utc": None, "checkpoint_count": 0,
        "source_health_cycle_ids": [], "shared_fact_cycle_ids": [], "acceptance_pass_count": 0,
        "consecutive_core_unsafe": 0, "consecutive_monitoring_stale": 0, "hard_fail_reasons": [], "finalized_at_utc": None
    }
    clean = {
        "checkpoint_at_utc": "2026-09-02T00:00:00Z", "source_health_evaluated_at_utc": "sh1",
        "shared_fact_generated_at_utc": "fv1", "acceptance": {"status": "PASS"}, "core_unsafe_sources": [],
        "monitoring_snapshot_stale": False, "immediate_hard_fail_reasons": []
    }
    # 24h alone is insufficient without distinct cycles.
    s, _ = evaluate_progress(base, clean, contract, parse_dt("2026-09-02T00:00:00Z"))
    assert s["status"] == "COLLECTING_INSUFFICIENT_CYCLES"
    # One CORE checkpoint is only AT_RISK; two consecutive are FINAL_FAIL.
    b2 = dict(base)
    b2["started_at_utc"] = "2026-09-01T00:00:00Z"
    risk = dict(clean); risk["core_unsafe_sources"] = [{"source_id": "x", "status": "FAILED"}]
    s1, _ = evaluate_progress(b2, risk, contract, parse_dt("2026-09-01T01:00:00Z"))
    assert s1["status"] == "AT_RISK"
    s2, _ = evaluate_progress(s1, risk, contract, parse_dt("2026-09-01T02:00:00Z"))
    assert s2["status"] == "FINAL_FAIL"
    # Drift is immediate final fail.
    drift = dict(clean); drift["immediate_hard_fail_reasons"] = ["MASTER_OR_CANONICAL_DRIFT_RESET_REQUIRED"]
    sd, _ = evaluate_progress(base, drift, contract, parse_dt("2026-09-01T01:00:00Z"))
    assert sd["status"] == "FINAL_FAIL"
    # Full clean 24h with enough cycles is FINAL_PASS.
    sf = dict(base)
    sf["source_health_cycle_ids"] = [f"sh{i}" for i in range(11)]
    sf["shared_fact_cycle_ids"] = [f"fv{i}" for i in range(11)]
    sf["acceptance_pass_count"] = 2
    final_cp = dict(clean); final_cp["source_health_evaluated_at_utc"] = "sh11"; final_cp["shared_fact_generated_at_utc"] = "fv11"
    sp, _ = evaluate_progress(sf, final_cp, contract, parse_dt("2026-09-02T00:00:00Z"))
    assert sp["status"] == "FINAL_PASS"
    print("P2_LONG_RUN_SELF_TEST=PASS")


def validate_runtime():
    if not STATE.exists() or not LATEST.exists():
        print("P2_LONG_RUN_RUNTIME=NOT_STARTED")
        print("P2_LONG_RUN_VALIDATE=PASS")
        return
    s = load_json(STATE); l = load_json(LATEST)
    assert s.get("run_id") == l.get("run_id")
    assert s.get("status") == l.get("status")
    assert s.get("checkpoint_count", 0) >= 1
    assert l.get("latest_checkpoint", {}).get("run_id") == s.get("run_id")
    if s.get("status") == "FINAL_PASS":
        p = l["progress"]; m = p["required"]
        assert p["observation_hours"] >= m["observation_hours"]
        assert p["source_health_cycles"] >= m["distinct_source_health_cycles"]
        assert p["shared_fact_cycles"] >= m["distinct_shared_fact_cycles"]
        assert p["successful_acceptance_checkpoints"] >= m["successful_acceptance_checkpoints"]
    print(f"P2_LONG_RUN_RUNTIME={s.get('status')}")
    print("P2_LONG_RUN_VALIDATE=PASS")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["collect", "dry-run", "validate", "self-test"], required=True)
    args = ap.parse_args()
    if args.mode == "self-test":
        self_test(); return
    if args.mode == "validate":
        validate_runtime(); return

    now = now_utc()
    fp, receipt = system_fingerprint()
    if STATE.exists():
        state = load_json(STATE)
        if state.get("status") in TERMINAL:
            print(f"P2_LONG_RUN_TERMINAL={state.get('status')}")
            print("- Explicit reset is required before starting a new observation window.")
            return
    else:
        state = initial_state(now, fp, receipt)

    checkpoint = build_checkpoint(state, now)
    contract = load_json(CONTRACT)
    new_state, progress = evaluate_progress(state, checkpoint, contract, now)

    print("MONEY MASTER OS P2 LONG-RUN CHECKPOINT")
    print(f"- run_id={new_state['run_id']}")
    print(f"- status={new_state['status']}")
    print(f"- observation_hours={progress['observation_hours']}")
    print(f"- source_health_cycles={progress['source_health_cycles']}/{progress['required']['distinct_source_health_cycles']}")
    print(f"- shared_fact_cycles={progress['shared_fact_cycles']}/{progress['required']['distinct_shared_fact_cycles']}")
    print(f"- acceptance_pass={progress['successful_acceptance_checkpoints']}/{progress['required']['successful_acceptance_checkpoints']}")
    print(f"- core_unsafe={checkpoint['core_unsafe_sources']}")
    print(f"- monitoring_snapshot_stale={checkpoint['monitoring_snapshot_stale']}")
    print(f"- fingerprint_match={checkpoint['fingerprint_matches_baseline']}")

    if args.mode == "collect":
        write_runtime(new_state, checkpoint, progress, now)
        print("P2_LONG_RUN_CHECKPOINT_WRITTEN=YES")
    else:
        print("P2_LONG_RUN_CHECKPOINT_WRITTEN=NO_DRY_RUN")

    if new_state["status"] == "FINAL_FAIL":
        sys.exit(1)


if __name__ == "__main__":
    main()
