#!/usr/bin/env python3
"""End-to-end operating integration test for all five MONEY MASTER OS masters.

This test never writes synthetic OFFICIAL/WATCH data to the checked-out repository.
It copies the repository into a temporary isolated directory, exercises the real
publish/validation code paths there, and destroys the copy at exit.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MASTER_IDS = ["market", "btc_trend", "alt_top100", "alt_final20", "trading"]
WATCH_ENABLED = {"market", "alt_top100", "alt_final20"}
WATCH_DISABLED = {"btc_trend", "trading"}


def load_json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise AssertionError(f"{path} must contain a JSON object")
    return obj


def run(cmd: list[str], cwd: Path, expect_success: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if expect_success and proc.returncode != 0:
        raise AssertionError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    if not expect_success and proc.returncode == 0:
        raise AssertionError(f"command unexpectedly succeeded: {' '.join(cmd)}\n{proc.stdout}")
    return proc


def make_stored_state(base: dict[str, Any], master_id: str, seq: int) -> dict[str, Any]:
    state = json.loads(json.dumps(base))
    state["state_status"] = "STORED"
    state["run"] = {
        "run_type": "OFFICIAL",
        "run_id": f"E2E-{master_id.upper()}-20300101-{seq:02d}00-KST",
        "executed_kst": f"2030-01-01T{seq:02d}:00:00+09:00",
        "updated_kst": f"2030-01-01T{seq:02d}:00:30+09:00",
        "prior_official_run_id": None,
    }
    state["validity"] = {
        "valid_until_kst": None,
        "next_official_run_kst": None,
        "freshness_status": "UNKNOWN",
    }
    state["data_quality"] = {
        "coverage_pct": 80,
        "confidence": "E2E_TEST",
        "known_na": [],
        "stale_sources": [],
        "time_mismatch_warnings": [],
        "source_health_snapshot_pointer": "source_health/output/latest.json",
    }
    state["decision"] = {
        "direction": "E2E_TEST_ONLY",
        "permission_or_environment": "E2E_TEST_ONLY",
        "action": "E2E_TEST_ONLY",
        "risk_veto": [],
        "invalidations": [],
    }
    state["core_state"] = {"e2e_fixture": True, "synthetic_test_only": True}
    state["lineage"] = {
        "state_origin": "ACTUAL_STORED_RUN",
        "run_record_source": "E2E_ISOLATED_FIXTURE",
        "history_pointer": f"official_state/history/{master_id}/",
        "canonical_identity_verified": True,
        "cross_master_decision_dependency": False,
    }
    state["privacy"] = {
        "contains_personal_account_data": False,
        "public_repository_safe": True,
    }
    state["do_not_reconstruct"] = ["E2E synthetic fixture must never leave isolated test copy"]
    return state


def make_watch_event(registry: dict[str, Any], master_id: str, seq: int) -> dict[str, Any]:
    version = registry["masters"][master_id]["expected_version"]
    return {
        "schema_version": "1.0",
        "event_id": f"E2E-WATCH-{master_id.upper()}-{seq:02d}",
        "correlation_key": f"E2E-{master_id.upper()}-MEANINGFUL-CHANGE",
        "event_class": "MASTER_WATCH",
        "change_type": "NEW",
        "event_level": "LEVEL2",
        "producer": {
            "type": "MASTER",
            "id": master_id,
            "version": version,
            "run_id": f"E2E-WATCH-RUN-{master_id.upper()}-{seq:02d}",
        },
        "master_id": master_id,
        "detected_at_kst": f"2030-01-02T{seq:02d}:00:00+09:00",
        "summary": "E2E isolated meaningful-change fixture",
        "reason_codes": ["E2E_INTEGRATION_TEST"],
        "evidence_refs": [{"path": "shared_fact_vault/output/latest.json"}],
        "watch_payload": {"watch_status": "E2E_TEST_ONLY"},
        "notification": {"eligible": True},
        "lineage": {
            "canonical_rule_verified": True,
            "central_recalculation_applied": False,
            "reconstructed": False,
            "provisional": False,
            "official_state_write": False,
            "source_kind": "ACTUAL_WATCH_OUTPUT",
        },
        "privacy": {
            "contains_personal_account_data": False,
            "public_repository_safe": True,
        },
    }


def main() -> None:
    receipts: dict[str, dict[str, Any]] = {mid: {} for mid in MASTER_IDS}

    # 1) Validate live control-plane linkage before isolation.
    registry = load_json(ROOT / "money_master_os/registry/MASTER_REGISTRY.json")
    source_health = load_json(ROOT / "source_health/output/latest.json")
    fact_registry = load_json(ROOT / "shared_fact_vault/registry.json")
    fact_vault = load_json(ROOT / "shared_fact_vault/output/latest.json")
    official_index = load_json(ROOT / "official_state/latest/index.json")
    watch_registry = load_json(ROOT / "watch_events/registry.json")
    watch_index = load_json(ROOT / "watch_events/latest/index.json")

    if list(registry.get("masters", {}).keys()) != MASTER_IDS:
        raise AssertionError("Registry does not contain exact ordered five MASTER IDs")
    if set((official_index.get("masters") or {}).keys()) != set(MASTER_IDS):
        raise AssertionError("OFFICIAL index does not contain exact five MASTER IDs")
    if set((watch_index.get("masters") or {}).keys()) != set(MASTER_IDS):
        raise AssertionError("WATCH index does not contain exact five MASTER IDs")
    if source_health.get("system") != "MONEY_MASTER_OS_SOURCE_HEALTH":
        raise AssertionError("Source Health latest missing/invalid")
    if fact_vault.get("vault_status") not in {"HEALTHY", "DEGRADED"}:
        raise AssertionError("Shared Fact Vault latest invalid")
    if (fact_vault.get("source_health_snapshot") or {}).get("path") != "source_health/output/latest.json":
        raise AssertionError("Shared Fact Vault is not linked to Source Health")

    active = fact_registry.get("active_adapters") or {}
    health_sources = source_health.get("sources") or {}
    vault_sources = fact_vault.get("sources") or {}
    for source_id, cfg in active.items():
        if source_id not in health_sources:
            raise AssertionError(f"active fact source missing from Source Health: {source_id}")
        if source_id not in vault_sources:
            raise AssertionError(f"active fact source missing from Vault latest: {source_id}")
        if cfg.get("source_health_id") != source_id:
            raise AssertionError(f"fact adapter/source-health identity mismatch: {source_id}")

    for mid in MASTER_IDS:
        allowed_sources = [sid for sid, cfg in active.items() if mid in (cfg.get("allowed_consumers") or [])]
        if not allowed_sources:
            raise AssertionError(f"{mid}: no Shared Fact Vault source is available to this consumer")
        usable_facts = sum(int((vault_sources.get(sid) or {}).get("fact_count", 0)) for sid in allowed_sources)
        if usable_facts <= 0:
            raise AssertionError(f"{mid}: consumer has zero registered facts")
        receipts[mid]["fact_sources"] = len(allowed_sources)
        receipts[mid]["registered_facts"] = usable_facts
        receipts[mid]["pre_bootstrap"] = "PASS"

    # Canonical WATCH ownership must exactly match the current policy.
    producers = watch_registry.get("master_producers") or {}
    for mid in WATCH_ENABLED:
        if (producers.get(mid) or {}).get("enabled") is not True:
            raise AssertionError(f"{mid}: expected WATCH producer enabled")
    for mid in WATCH_DISABLED:
        if (producers.get(mid) or {}).get("enabled") is not False:
            raise AssertionError(f"{mid}: expected WATCH producer disabled")

    # 2) Copy repository into an isolated directory and exercise real publishers.
    with tempfile.TemporaryDirectory(prefix="money-master-e2e-") as td:
        isolated = Path(td) / "repo"
        shutil.copytree(ROOT, isolated, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
        inputs = isolated / ".e2e_inputs"
        inputs.mkdir(parents=True, exist_ok=True)

        # Baseline validators in the isolated clone.
        run([sys.executable, "money_master_os/tools/test_bootstrap_all_masters.py"], isolated)
        run([sys.executable, "official_state/publish_official_state.py", "--mode", "validate-latest"], isolated)
        run([sys.executable, "watch_events/publish_watch_event.py", "--mode", "validate-latest"], isolated)

        # 3) Publish one synthetic OFFICIAL run per MASTER through the real publisher.
        for seq, mid in enumerate(MASTER_IDS, start=1):
            base = load_json(isolated / f"official_state/latest/{mid}.json")
            state = make_stored_state(base, mid, seq)
            inp = inputs / f"official_{mid}.json"
            inp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            run(
                [sys.executable, "official_state/publish_official_state.py", "--mode", "publish", "--input", str(inp)],
                isolated,
            )
            receipts[mid]["official_publish"] = "PASS"

        run([sys.executable, "official_state/publish_official_state.py", "--mode", "validate-latest"], isolated)
        post_official_index = load_json(isolated / "official_state/latest/index.json")
        for mid in MASTER_IDS:
            ent = post_official_index["masters"][mid]
            if ent.get("state_status") != "STORED" or not str(ent.get("latest_official_run_id", "")).startswith("E2E-"):
                raise AssertionError(f"{mid}: isolated OFFICIAL publish did not update latest index")
            history = list((isolated / f"official_state/history/{mid}").glob("*.jsonl"))
            if not history or "E2E-" not in "".join(p.read_text(encoding="utf-8") for p in history):
                raise AssertionError(f"{mid}: isolated OFFICIAL history append missing")

        # 4) New-room restoration after all five OFFICIAL writes.
        run([sys.executable, "money_master_os/tools/test_bootstrap_all_masters.py"], isolated)
        for mid in MASTER_IDS:
            receipts[mid]["restore_after_official"] = "PASS"

        # Snapshot OFFICIAL index; WATCH must not mutate it.
        official_before_watch = (isolated / "official_state/latest/index.json").read_text(encoding="utf-8")

        # 5) Allowed WATCH producers must publish; disabled producers must fail closed.
        for seq, mid in enumerate(MASTER_IDS, start=1):
            event = make_watch_event(registry, mid, seq)
            inp = inputs / f"watch_{mid}.json"
            inp.write_text(json.dumps(event, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if mid in WATCH_ENABLED:
                run(
                    [sys.executable, "watch_events/publish_watch_event.py", "--mode", "publish", "--input", str(inp)],
                    isolated,
                )
                receipts[mid]["watch"] = "PUBLISH_PASS"
            else:
                proc = run(
                    [sys.executable, "watch_events/publish_watch_event.py", "--mode", "publish", "--input", str(inp)],
                    isolated,
                    expect_success=False,
                )
                if "WATCH producer disabled" not in (proc.stdout + proc.stderr):
                    raise AssertionError(f"{mid}: WATCH failed, but not for canonical disabled-producer policy")
                receipts[mid]["watch"] = "BLOCKED_EXPECTED"

        run([sys.executable, "watch_events/publish_watch_event.py", "--mode", "validate-latest"], isolated)
        isolated_watch_index = load_json(isolated / "watch_events/latest/index.json")
        if isolated_watch_index.get("event_count_total") != 3:
            raise AssertionError("isolated WATCH event total must be exactly 3")
        for mid in WATCH_ENABLED:
            ent = isolated_watch_index["masters"][mid]
            if ent.get("event_count") != 1 or not ent.get("latest_event_path"):
                raise AssertionError(f"{mid}: WATCH latest/history lookup not updated")
            if not (isolated / ent["latest_event_path"]).exists():
                raise AssertionError(f"{mid}: WATCH latest event file missing")
        for mid in WATCH_DISABLED:
            ent = isolated_watch_index["masters"][mid]
            if ent.get("event_count") != 0 or ent.get("latest_event_id") is not None:
                raise AssertionError(f"{mid}: disabled WATCH producer leaked an event")

        if (isolated / "official_state/latest/index.json").read_text(encoding="utf-8") != official_before_watch:
            raise AssertionError("WATCH event publish mutated OFFICIAL State index")

        # 6) Re-run full bootstrap after WATCH writes; identity/decision continuity must survive.
        run([sys.executable, "money_master_os/tools/test_bootstrap_all_masters.py"], isolated)
        for mid in MASTER_IDS:
            receipts[mid]["restore_after_watch"] = "PASS"

    print("MONEY MASTER OS P1 OPERATING E2E RECEIPTS")
    for mid in MASTER_IDS:
        r = receipts[mid]
        print(
            f"- {mid} | facts={r['registered_facts']} from {r['fact_sources']} sources | "
            f"bootstrap={r['pre_bootstrap']} | official={r['official_publish']} | "
            f"watch={r['watch']} | restore_official={r['restore_after_official']} | "
            f"restore_watch={r['restore_after_watch']}"
        )
    print("P1_OPERATING_INTEGRATION_TEST=PASS")
    print("- Synthetic OFFICIAL/WATCH fixtures were isolated in a temporary copy and were not persisted to the real repository.")
    print("- WATCH did not mutate OFFICIAL State, and BTC/TRADING WATCH remained fail-closed by canonical policy.")


if __name__ == "__main__":
    main()
