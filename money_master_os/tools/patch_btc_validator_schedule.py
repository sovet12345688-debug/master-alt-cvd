#!/usr/bin/env python3
from pathlib import Path

p = Path('money_master_os/tools/validate_master_os.py')
text = p.read_text(encoding='utf-8')

replacements = [
    (
        '    "state/master_btc_trend_v2_6_contract.json",\n',
        '    "state/master_btc_trend_v2_6_contract.json",\n    "master_prompts/master_btc_trend_v2_6_ui_final.md",\n    "state/master_btc_trend_r26_schedule_link_contract.json",\n    ".github/workflows/btc_trend_v30_r26_forward_oos.yml",\n    "official_state/btc_trend_bridge_contract.json",\n'
    ),
    (
        '        "BASIC OFFICIAL KST 09:10 / 17:10 / 21:10",',
        '        "BASIC OFFICIAL KST 05:00 / 08:00 / 13:00 / 17:00 / 21:00",'
    ),
    (
        '        "OUTPUT은 BASIC/Precision 모두 ONLY 3 SCREENS",',
        '        "BTC 선행신호 엔진 READ ONLY — MANDATORY",'
    ),
    (
        '    if schedule.get("official_kst") != ["09:10", "17:10", "21:10"]:\n        fail("BTC V2.6 official schedule mismatch")',
        '    if schedule.get("official_kst") != ["05:00", "08:00", "13:00", "17:00", "21:00"]:\n        fail("BTC V2.6 official schedule mismatch")'
    ),
    (
        '    if output.get("semantic_screens") != 3:\n        fail("BTC V2.6 must preserve current 3 semantic screens")\n    if output.get("later_user_refinement_allowed") is not True:\n        fail("BTC UI later user refinement must remain allowed")',
        '    if output.get("basic_official_semantic_screens") != 3:\n        fail("BTC V2.6 BASIC OFFICIAL must preserve 3 semantic screens")\n    if output.get("precision_uses_separate_override") is not True:\n        fail("BTC V2.6 Precision must use the separate UI override")\n    if output.get("btc_leading_signal_block_mandatory") is not True:\n        fail("BTC leading-signal block must remain mandatory")\n    if output.get("later_user_refinement_allowed") is not True:\n        fail("BTC UI later user refinement must remain allowed")'
    ),
]

for old, new in replacements:
    if old not in text:
        raise SystemExit(f'PATCH_TARGET_MISSING: {old[:100]}')
    text = text.replace(old, new, 1)

anchor = 'alt_top100_source = ROOT / "master_prompts/master_alt_top100_v4_8_current.md"\n'
if anchor not in text:
    raise SystemExit('PATCH_ANCHOR_MISSING')

block = r'''# BTC TREND <-> R2.6 schedule-linkage regression guard.
btc_manifest_path = ROOT / "money_master_os/masters/btc_trend/manifest.json"
btc_manifest = load_json(btc_manifest_path) if btc_manifest_path.exists() else {}
link_rel = btc_manifest.get("btc_leading_signal_schedule_contract")
expected_link_rel = "state/master_btc_trend_r26_schedule_link_contract.json"
if link_rel != expected_link_rel:
    fail("BTC manifest schedule-link contract path mismatch")
link_path = ROOT / expected_link_rel
link = load_json(link_path) if link_path.exists() else {}
if link:
    if link.get("status") != "FINAL_LOCK":
        fail("BTC/R2.6 schedule-link contract must be FINAL_LOCK")
    if link.get("master", {}).get("official_kst") != ["05:00", "08:00", "13:00", "17:00", "21:00"]:
        fail("BTC/R2.6 linkage MASTER slots mismatch")
    fe = link.get("forward_engine", {})
    if fe.get("scheduled_kst") != ["05:20", "08:20", "13:20", "17:20", "21:20"]:
        fail("BTC/R2.6 linkage Forward slots mismatch")
    if fe.get("cron_utc") != "20 4,8,12,20,23 * * *":
        fail("BTC/R2.6 linkage UTC cron mismatch")
    if fe.get("mode") != "READ_ONLY_ZERO_WEIGHT":
        fail("BTC leading-signal linkage must remain READ_ONLY_ZERO_WEIGHT")
    expected_mapping = {
        "05:00": "PREVIOUS_DAY_21:20",
        "08:00": "05:20",
        "13:00": "08:20",
        "17:00": "13:20",
        "21:00": "17:20",
    }
    if link.get("official_to_forward_mapping_kst") != expected_mapping:
        fail("BTC/R2.6 official-to-forward mapping mismatch")
    sel = link.get("selection_rule", {})
    if sel.get("fallback") != "IF_EXPECTED_SLOT_IS_MISSING_RUNNING_FAILED_OR_UNVERIFIABLE_USE_IMMEDIATELY_PREVIOUS_VERIFIABLE_SUCCESSFUL_FORWARD_OOS_RUN_COMPLETED_BEFORE_OFFICIAL_REPORT_START":
        fail("BTC/R2.6 previous-success fallback rule drift")
    for key in ["future_run_forbidden", "in_progress_run_forbidden", "failed_run_forbidden", "historical_or_bridge_as_current_forbidden", "strict_forward_only", "completed_candle_only"]:
        if sel.get(key) is not True:
            fail(f"BTC/R2.6 schedule-link safety rule not locked: {key}")
    prov = link.get("provenance_and_integrity", {})
    if prov.get("workflow_run_success_required") is not True or prov.get("ledger_integrity_pass_required") is not True:
        fail("BTC/R2.6 linkage provenance/integrity gate missing")
    if prov.get("frozen_identity_drift_forbidden") is not True:
        fail("BTC/R2.6 Frozen identity drift must remain forbidden")
    if prov.get("zero_detection_seed_position_is_failure") is not False:
        fail("BTC/R2.6 zero signal must not be treated as failure")
    auth = link.get("authority_lock", {})
    for key in ["changes_v26_scores", "changes_long_short_ratio", "changes_entry_gate", "changes_live_plan", "changes_fractal_or_sr", "changes_r26_frozen_engine"]:
        if auth.get(key) is not False:
            fail(f"BTC/R2.6 READ-ONLY authority drift: {key}")
    if auth.get("execution_capital_authority") != "FROZEN_R2.5_ONLY":
        fail("BTC/R2.6 execution authority must remain Frozen R2.5 only")

ble = btc_manifest.get("btc_leading_signal_engine", {})
if ble.get("schedule_link_contract") != expected_link_rel:
    fail("BTC manifest leading-signal schedule contract reference mismatch")
if ble.get("mode") != "READ_ONLY_ZERO_WEIGHT" or ble.get("strict_forward_only") is not True:
    fail("BTC manifest leading-signal mode/STRICT policy drift")
if ble.get("must_not_change_v26_production_decision") is not True or ble.get("must_not_modify_r26_frozen_forward_assets") is not True:
    fail("BTC manifest leading-signal isolation lock drift")

ui_path = ROOT / "master_prompts/master_btc_trend_v2_6_ui_final.md"
if ui_path.exists():
    ui = ui_path.read_text(encoding="utf-8")
    for sig in [
        "BTC 선행신호 엔진 READ ONLY — MANDATORY",
        "E) `BTC 선행신호 엔진` — SCREEN 1 필수 고정 블록",
        "신호 없음 → 조기신호 → 집중관찰 → 진입준비 → 1차 진입 → 방향확인 → 본진입 → 청산",
        "STRICT 신호가 0건이어도 이 블록을 생략하지 않고",
    ]:
        if sig not in ui:
            fail(f"BTC leading-signal UI signature missing: {sig}")

workflow_path = ROOT / ".github/workflows/btc_trend_v30_r26_forward_oos.yml"
if workflow_path.exists():
    wf = workflow_path.read_text(encoding="utf-8")
    if "cron: '20 4,8,12,20,23 * * *'" not in wf:
        fail("R2.6 Forward OOS main workflow cron mismatch")
    if "ref: btc-trend-v30-r26-final-integration" not in wf:
        fail("R2.6 Forward OOS workflow integration-branch checkout drift")

bridge_path = ROOT / "official_state/btc_trend_bridge_contract.json"
bridge = load_json(bridge_path) if bridge_path.exists() else {}
if bridge and bridge.get("run_id_policy", {}).get("official_slots_kst") != ["05:00", "08:00", "13:00", "17:00", "21:00"]:
    fail("BTC official-state bridge schedule mismatch")

'''

text = text.replace(anchor, block + anchor, 1)
p.write_text(text, encoding='utf-8')
print('PATCH_BTC_VALIDATOR_SCHEDULE_LINK=PASS')
