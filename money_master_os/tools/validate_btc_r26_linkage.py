#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO = "sovet12345688-debug/master-alt-cvd"
INTEGRATION_REF = "origin/btc-trend-v30-r26-final-integration"
errors: list[str] = []


def fail(msg: str) -> None:
    errors.append(msg)


def load_json(path: str) -> dict:
    p = ROOT / path
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        fail(f"cannot read {path}: {e}")
        return {}


def text(path: str) -> str:
    p = ROOT / path
    try:
        return p.read_text(encoding="utf-8")
    except Exception as e:
        fail(f"cannot read {path}: {e}")
        return ""


def git_show(ref: str, path: str) -> bytes:
    try:
        return subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT)
    except Exception as e:
        fail(f"cannot git-show {ref}:{path}: {e}")
        return b""


def git_blob(ref: str, path: str) -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", f"{ref}:{path}"], cwd=ROOT, text=True).strip()
    except Exception as e:
        fail(f"cannot read blob {ref}:{path}: {e}")
        return ""


# 1) MASTER BTC TREND authoritative chain on main.
registry = load_json("money_master_os/registry/MASTER_REGISTRY.json")
btc_reg = registry.get("masters", {}).get("btc_trend", {})
if btc_reg.get("status") != "READY":
    fail("BTC registry status must be READY")
if btc_reg.get("production_version") != "V2.6" or btc_reg.get("research_version") != "V3.0":
    fail("BTC production/research version separation drift")
if btc_reg.get("source_path") != "master_prompts/master_btc_trend_v2_6_current.md":
    fail("BTC canonical source path drift")
if btc_reg.get("contract_path") != "state/master_btc_trend_v2_6_contract.json":
    fail("BTC machine contract path drift")

manifest = load_json("money_master_os/masters/btc_trend/manifest.json")
if manifest.get("status") != "READY":
    fail("BTC manifest status must be READY")
if manifest.get("canonical_source") != "master_prompts/master_btc_trend_v2_6_current.md":
    fail("BTC manifest canonical path drift")
if manifest.get("ui_canonical_source") != "master_prompts/master_btc_trend_v2_6_ui_final.md":
    fail("BTC manifest UI path drift")
if manifest.get("machine_contract") != "state/master_btc_trend_v2_6_contract.json":
    fail("BTC manifest machine contract drift")
if manifest.get("btc_leading_signal_schedule_contract") != "state/master_btc_trend_r26_schedule_link_contract.json":
    fail("BTC manifest schedule-link contract drift")
ble = manifest.get("btc_leading_signal_engine", {})
if ble.get("mode") != "READ_ONLY_ZERO_WEIGHT":
    fail("BTC leading-signal mode must be READ_ONLY_ZERO_WEIGHT")
if ble.get("strict_forward_only") is not True:
    fail("BTC leading-signal must be STRICT_FORWARD only")
if ble.get("must_not_change_v26_production_decision") is not True:
    fail("BTC leading-signal must not change V2.6 production decision")
if ble.get("must_not_modify_r26_frozen_forward_assets") is not True:
    fail("BTC leading-signal must not modify R2.6 frozen assets")

canonical = text("master_prompts/master_btc_trend_v2_6_current.md")
for sig in [
    "BASIC OFFICIAL KST 05:00 / 08:00 / 13:00 / 17:00 / 21:00",
    "BTC 선행신호 엔진 READ ONLY — MANDATORY",
    "STRICT_FORWARD만 현재 사용자 신호로 인정",
    "0점/0가중치",
]:
    if sig not in canonical:
        fail(f"BTC canonical signature missing: {sig}")

ui = text("master_prompts/master_btc_trend_v2_6_ui_final.md")
for sig in [
    "BTC 선행신호 엔진 READ ONLY — MANDATORY",
    "E) `BTC 선행신호 엔진` — SCREEN 1 필수 고정 블록",
    "신호 없음 → 조기신호 → 집중관찰 → 진입준비 → 1차 진입 → 방향확인 → 본진입 → 청산",
    "STRICT 신호가 0건이어도 이 블록을 생략하지 않고",
]:
    if sig not in ui:
        fail(f"BTC UI signature missing: {sig}")

contract = load_json("state/master_btc_trend_v2_6_contract.json")
if contract.get("schedule", {}).get("official_kst") != ["05:00", "08:00", "13:00", "17:00", "21:00"]:
    fail("BTC machine-contract schedule drift")
out = contract.get("output", {})
if out.get("basic_official_semantic_screens") != 3:
    fail("BTC BASIC OFFICIAL must remain 3 screens")
if out.get("btc_leading_signal_block_mandatory") is not True:
    fail("BTC leading-signal block must remain mandatory")
if out.get("precision_uses_separate_override") is not True:
    fail("BTC Precision UI override drift")

bridge = load_json("official_state/btc_trend_bridge_contract.json")
if bridge.get("run_id_policy", {}).get("official_slots_kst") != ["05:00", "08:00", "13:00", "17:00", "21:00"]:
    fail("BTC official-state bridge schedule drift")

# 2) MASTER ↔ R2.6 linkage contract.
link = load_json("state/master_btc_trend_r26_schedule_link_contract.json")
if link.get("status") != "FINAL_LOCK":
    fail("BTC/R2.6 link contract must be FINAL_LOCK")
if link.get("master", {}).get("official_kst") != ["05:00", "08:00", "13:00", "17:00", "21:00"]:
    fail("BTC/R2.6 master slots drift")
fe = link.get("forward_engine", {})
if fe.get("scheduled_kst") != ["05:20", "08:20", "13:20", "17:20", "21:20"]:
    fail("R2.6 Forward KST slots drift")
if fe.get("cron_utc") != "20 4,8,12,20,23 * * *":
    fail("R2.6 Forward UTC cron drift")
expected_map = {
    "05:00": "PREVIOUS_DAY_21:20",
    "08:00": "05:20",
    "13:00": "08:20",
    "17:00": "13:20",
    "21:00": "17:20",
}
if link.get("official_to_forward_mapping_kst") != expected_map:
    fail("MASTER ↔ R2.6 slot mapping drift")
sel = link.get("selection_rule", {})
if sel.get("primary") != "USE_EXPECTED_PRECEDING_SLOT_IF_WORKFLOW_RUN_CONCLUSION_SUCCESS_AND_COMPLETED_BEFORE_OFFICIAL_REPORT_START":
    fail("primary preceding-slot selection rule drift")
if sel.get("fallback") != "IF_EXPECTED_SLOT_IS_MISSING_RUNNING_FAILED_OR_UNVERIFIABLE_USE_IMMEDIATELY_PREVIOUS_VERIFIABLE_SUCCESSFUL_FORWARD_OOS_RUN_COMPLETED_BEFORE_OFFICIAL_REPORT_START":
    fail("previous-success fallback drift")
for k in ["future_run_forbidden", "in_progress_run_forbidden", "failed_run_forbidden", "historical_or_bridge_as_current_forbidden", "strict_forward_only", "completed_candle_only"]:
    if sel.get(k) is not True:
        fail(f"link safety flag drift: {k}")
prov = link.get("provenance_and_integrity", {})
if prov.get("workflow_run_success_required") is not True:
    fail("successful workflow run must be required")
if prov.get("ledger_integrity_pass_required") is not True:
    fail("ledger integrity PASS must be required")
if prov.get("frozen_identity_drift_forbidden") is not True:
    fail("Frozen identity drift must remain forbidden")
if prov.get("zero_detection_seed_position_is_failure") is not False:
    fail("zero STRICT signal must not be failure")
auth = link.get("authority_lock", {})
for k in ["changes_v26_scores", "changes_long_short_ratio", "changes_entry_gate", "changes_live_plan", "changes_fractal_or_sr", "changes_r26_frozen_engine"]:
    if auth.get(k) is not False:
        fail(f"READ-ONLY authority drift: {k}")
if auth.get("execution_capital_authority") != "FROZEN_R2.5_ONLY":
    fail("execution authority drift")

# 3) R2.6 scheduler + frozen identities.
main_wf = text(".github/workflows/btc_trend_v30_r26_forward_oos.yml")
if "cron: '20 4,8,12,20,23 * * *'" not in main_wf:
    fail("main R2.6 cron drift")
if "ref: btc-trend-v30-r26-final-integration" not in main_wf:
    fail("main R2.6 workflow must checkout integration branch")

integration_wf = git_show(INTEGRATION_REF, ".github/workflows/btc_trend_v30_r26_forward_oos.yml").decode("utf-8", "replace")
if integration_wf and "cron: '20 4,8,12,20,23 * * *'" not in integration_wf:
    fail("integration R2.6 cron mirror drift")

expected_blobs = {
    "btc_trend_v30/r26/r26_engine.py": "c511b99205314d351bacb441cc88d70d9a4947ee",
    "btc_trend_v30/r26/r26_frozen_config.json": "09d6d6689f46c16f3a40b16c58495a999e4f9e75",
    "btc_trend_v30/r26/test_r26_contract.py": "ae6e8bd3cbafdc8b8a2c9672db5ee16e324850b3",
    "btc_trend_v30/r26/forward_oos/forward_tracker.py": "2cb6e6221a14e85309f66058f89e97fe05482e8e",
    "btc_trend_v30/r26/forward_oos/R26_FORWARD_LEDGER_GUARD_FREEZE_MANIFEST_V1.json": "aaa9fda112cede84a3331e80d8b21252bcef611f",
    "btc_trend_v30/r26/forward_oos/R26_FORWARD_LEDGER_INTEGRITY_SPEC_V1.json": "49efdab5da40d88f03a3dcf9ede3830ef31f71e3",
    "btc_trend_v30/r26/forward_oos/ledger_integrity_guard.py": "191884d064bf29f09c6567ce2978e51f2af65022",
    "btc_trend_v30/r26/forward_oos/R26_FORWARD_SCORECARD_FREEZE_MANIFEST_V1.json": "325a18ded37cef92be8b4054813c9d59ccfccb60",
    "btc_trend_v30/r26/forward_oos/scorecard_freeze_guard.py": "bdde48842e5851f6b58b22d7d3b4893e0459d70d",
    "btc_trend_v30/r26/forward_oos/R26_FORWARD_PROMOTION_SPEC_V1.json": "a01ed85bda4512326d42e1d3eccc0b9ac8ee3dce",
    "btc_trend_v30/r26/forward_oos/forward_scorecard.py": "92224684016e1205152cdfdd317e40e1224920c0",
}
for path, expected in expected_blobs.items():
    actual = git_blob(INTEGRATION_REF, path)
    if actual and actual != expected:
        fail(f"Frozen/locked blob drift: {path} {actual} != {expected}")

frozen_config = git_show(INTEGRATION_REF, "btc_trend_v30/r26/r26_frozen_config.json")
if frozen_config:
    sha256 = hashlib.sha256(frozen_config).hexdigest()
    if sha256 != "bbdbbe173e8f16bb6d57d6ed5bdff52d616c42fead5b59b39c5b3d7188a64a61":
        fail(f"R2.6 frozen config SHA256 drift: {sha256}")

ledger_bytes = git_show(INTEGRATION_REF, "btc_trend_v30/r26/forward_oos/ledger_integrity_report.json")
if ledger_bytes:
    try:
        ledger = json.loads(ledger_bytes)
        if ledger.get("pass") is not True:
            fail("latest R2.6 ledger integrity is not PASS")
        if ledger.get("rules", {}).get("strict_only") is not True:
            fail("latest R2.6 ledger is not STRICT-only")
    except Exception as e:
        fail(f"cannot parse latest R2.6 ledger report: {e}")

if errors:
    print("BTC_R26_LINKAGE_GUARD=FAIL")
    for e in errors:
        print(f"- ERROR: {e}")
    sys.exit(1)

print("BTC_R26_LINKAGE_GUARD=PASS")
print("- GitHub main source chain aligned")
print("- MASTER BTC TREND schedule/UI/contract aligned")
print("- MASTER↔R2.6 mapping + fallback locked")
print("- R2.6 main/integration cron aligned")
print("- Frozen R2.6/ledger/scorecard identities unchanged")
print("- Latest R2.6 ledger integrity PASS")
