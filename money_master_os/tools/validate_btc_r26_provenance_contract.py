#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INTEGRATION_REF = "origin/btc-trend-v30-r26-final-integration"
PROV_PATH = "btc_trend_v30/r26/forward_oos/provenance.json"
errors: list[str] = []


def fail(msg: str) -> None:
    errors.append(msg)


def load_json(path: str) -> dict:
    try:
        return json.loads((ROOT / path).read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"cannot read {path}: {exc}")
        return {}


def text(path: str) -> str:
    try:
        return (ROOT / path).read_text(encoding="utf-8")
    except Exception as exc:
        fail(f"cannot read {path}: {exc}")
        return ""


def integration_file_exists(path: str) -> bool:
    return subprocess.run(
        ["git", "cat-file", "-e", f"{INTEGRATION_REF}:{path}"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


link = load_json("state/master_btc_trend_r26_schedule_link_contract.json")
prov = link.get("provenance_and_integrity", {})
sel = link.get("selection_rule", {})

if link.get("schema_version") != "1.1":
    fail("link contract schema_version must be 1.1")
if prov.get("provenance_manifest_path") != PROV_PATH:
    fail("provenance manifest path drift")
if prov.get("provenance_contract") != "R26_SAME_RUN_PROVENANCE_V1":
    fail("provenance contract drift")
if prov.get("join_key") != "WORKFLOW_RUN_ID_PLUS_SNAPSHOT_HASH_SHA256":
    fail("provenance join key drift")
if prov.get("snapshot_hash_basis") != ["latest.md", "state.json"]:
    fail("snapshot hash basis drift")
if prov.get("same_run_artifact_required") is not True:
    fail("same-run artifact must be required")
if prov.get("artifact_name_template") != "r26-forward-oos-{workflow_run_id}":
    fail("artifact name template drift")
required_fields = {
    "workflow_run_id",
    "workflow_run_attempt",
    "snapshot_hash_sha256",
    "snapshot_hash_basis",
    "file_sha256",
    "expected_artifact_name",
}
if set(prov.get("required_manifest_fields", [])) != required_fields:
    fail("required provenance fields drift")
required_sequence = [
    "LOAD_PROVENANCE_MANIFEST_FROM_SELECTED_FORWARD_SNAPSHOT",
    "MATCH_PROVENANCE_WORKFLOW_RUN_ID_TO_GITHUB_ACTIONS_RUN",
    "REQUIRE_WORKFLOW_STATUS_COMPLETED_AND_CONCLUSION_SUCCESS",
    "REQUIRE_WORKFLOW_COMPLETED_BEFORE_OFFICIAL_REPORT_START",
    "MATCH_EXPECTED_ARTIFACT_NAME_TO_SAME_RUN_ARTIFACT",
    "RECOMPUTE_LATEST_MD_AND_STATE_JSON_SHA256",
    "RECOMPUTE_SNAPSHOT_HASH_SHA256_AND_MATCH_MANIFEST",
    "REQUIRE_LEDGER_INTEGRITY_PASS",
    "REQUIRE_FROZEN_IDENTITY_NO_DRIFT",
    "ONLY_THEN_MARK_CURRENT",
]
if prov.get("verification_sequence") != required_sequence:
    fail("provenance verification sequence drift")
if sel.get("scheduled_slot_is_logical_target_not_exact_start_timestamp") is not True:
    fail("cron slot must be logical target, not exact timestamp")
if sel.get("accept_actual_run_time_if_same_logical_slot_and_completed_before_official_report") is not True:
    fail("delayed same-slot successful run acceptance rule missing")

manifest = load_json("money_master_os/masters/btc_trend/manifest.json")
ble = manifest.get("btc_leading_signal_engine", {})
if ble.get("provenance_manifest") != PROV_PATH:
    fail("manifest provenance path drift")
if ble.get("provenance_join") != "WORKFLOW_RUN_ID_PLUS_SNAPSHOT_HASH_SHA256":
    fail("manifest provenance join drift")
if ble.get("same_run_artifact_required") is not True:
    fail("manifest same-run artifact rule missing")
if ble.get("provenance_user_visible") is not False:
    fail("raw provenance must remain hidden from normal user-visible output")

wf = text(".github/workflows/btc_trend_v30_r26_forward_oos.yml")
for signature in [
    "Build same-run provenance manifest",
    "R26_SAME_RUN_PROVENANCE_V1",
    "workflow_run_id",
    "snapshot_hash_sha256",
    "expected_artifact_name",
    "Verify provenance join before commit",
    "R26_PROVENANCE_JOIN_PRECOMMIT_PASS=1",
    "btc_trend_v30/r26/forward_oos/provenance.json",
    "r26-forward-oos-${{ github.run_id }}",
]:
    if signature not in wf:
        fail(f"Forward provenance workflow signature missing: {signature}")

# The frozen Forward tracker and engine remain untouched; the provenance writer is workflow plumbing only.
locked_blobs = {
    "btc_trend_v30/r26/r26_engine.py": "c511b99205314d351bacb441cc88d70d9a4947ee",
    "btc_trend_v30/r26/r26_frozen_config.json": "09d6d6689f46c16f3a40b16c58495a999e4f9e75",
    "btc_trend_v30/r26/test_r26_contract.py": "ae6e8bd3cbafdc8b8a2c9672db5ee16e324850b3",
    "btc_trend_v30/r26/forward_oos/forward_tracker.py": "2cb6e6221a14e85309f66058f89e97fe05482e8e",
}
for path, expected in locked_blobs.items():
    try:
        actual = subprocess.check_output(
            ["git", "rev-parse", f"{INTEGRATION_REF}:{path}"], cwd=ROOT, text=True
        ).strip()
    except Exception as exc:
        fail(f"cannot read frozen blob {path}: {exc}")
        continue
    if actual != expected:
        fail(f"Frozen asset drift: {path} {actual} != {expected}")

live_provenance_present = integration_file_exists(PROV_PATH)

if errors:
    print("BTC_R26_PROVENANCE_CONTRACT_GUARD=FAIL")
    for item in errors:
        print(f"- ERROR: {item}")
    sys.exit(1)

print("BTC_R26_PROVENANCE_CONTRACT_GUARD=PASS")
print("- run_id + snapshot_hash join contract locked")
print("- delayed cron start is explicitly not a provenance failure")
print("- same-run artifact requirement locked")
print("- frozen R2.6 engine/tracker identities unchanged")
if live_provenance_present:
    print("- LIVE_PROVENANCE_FILE=PRESENT (dynamic content must be verified against its GitHub Actions run)")
else:
    print("- LIVE_PROVENANCE_FILE=PENDING_NEXT_FORWARD_RUN")
