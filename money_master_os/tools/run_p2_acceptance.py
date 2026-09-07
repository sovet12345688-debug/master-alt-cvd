#!/usr/bin/env python3
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CRITERIA = ROOT / "money_master_os/acceptance/ACCEPTANCE_CRITERIA_V1.json"
REGISTRY = ROOT / "money_master_os/registry/MASTER_REGISTRY.json"
SOURCE_HEALTH_REGISTRY = ROOT / "source_health/registry.json"
SOURCE_HEALTH_LATEST = ROOT / "source_health/output/latest.json"
FACT_VAULT_LATEST = ROOT / "shared_fact_vault/output/latest.json"
OFFICIAL_INDEX = ROOT / "official_state/latest/index.json"
WATCH_INDEX = ROOT / "watch_events/latest/index.json"

EXPECTED_MASTERS = ["market", "btc_trend", "alt_top100", "alt_final20", "trading"]
BAD_CORE = {"FAILED", "STALE", "MISSING", "UNKNOWN"}

fails = []
warnings = []
receipts = {}


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        fails.append(f"cannot parse {path.relative_to(ROOT)}: {e}")
        return {}


def run_check(name, rel_script, args=None):
    cmd = [sys.executable, str(ROOT / rel_script)] + (args or [])
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    receipts[name] = {
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout.strip().splitlines()[-12:],
        "stderr_tail": proc.stderr.strip().splitlines()[-8:],
    }
    if proc.returncode != 0:
        fails.append(f"{name} failed")
    return proc


criteria = load_json(CRITERIA)
if criteria.get("acceptance_id") != "MONEY_MASTER_OS_V2_P2_ACCEPTANCE_V1":
    fails.append("Acceptance criteria identity mismatch")

# A1 identity/canonical integrity
registry = load_json(REGISTRY)
masters = registry.get("masters", {})
if list(masters.keys()) != EXPECTED_MASTERS:
    fails.append(f"A1 master identity mismatch: {list(masters.keys())}")
for mid in EXPECTED_MASTERS:
    entry = masters.get(mid, {})
    if entry.get("status") != "READY" or entry.get("bootstrap_allowed") is not True:
        fails.append(f"A1 {mid} not READY/bootstrapable")
    if entry.get("expected_version") != entry.get("repo_version"):
        fails.append(f"A1 {mid} version drift")
    for field in ["manifest_path", "source_path", "official_state_path"]:
        rel = entry.get(field)
        if not rel or not (ROOT / rel).exists():
            fails.append(f"A1 {mid} missing {field}: {rel}")
    contract = entry.get("contract_path")
    if contract and not (ROOT / contract).exists():
        fails.append(f"A1 {mid} missing contract: {contract}")

# A2 restore is executed via existing canonical bootstrap test.
run_check("A2_NEW_ROOM_RESTORE", "money_master_os/tools/test_bootstrap_all_masters.py")

# A3 CORE data health. Overall DEGRADED is allowed only if degraded sources do not have CORE impact.
shr = load_json(SOURCE_HEALTH_REGISTRY)
shl = load_json(SOURCE_HEALTH_LATEST)
health_sources = shl.get("sources", {})
for sid, spec in (shr.get("sources") or {}).items():
    state = health_sources.get(sid, {})
    status = state.get("status", "MISSING")
    impacts = spec.get("impact", {})
    is_core = any(v == "CORE" for v in impacts.values())
    if is_core and status in BAD_CORE:
        fails.append(f"A3 CORE source unsafe: {sid}={status}")
    elif (not is_core) and status in BAD_CORE:
        warnings.append(f"OPTIONAL/CONTEXT source degraded: {sid}={status}")
if shl.get("overall") not in {"HEALTHY", "DEGRADED"}:
    fails.append(f"A3 Source Health overall unacceptable: {shl.get('overall')}")
run_check("A3_SOURCE_HEALTH_CONTRACT", "money_master_os/tools/validate_source_health_contract.py")

# A4 Shared Fact boundary.
fv = load_json(FACT_VAULT_LATEST)
policy = fv.get("policy", {})
if fv.get("vault_status") != "HEALTHY":
    fails.append(f"A4 Fact Vault not HEALTHY: {fv.get('vault_status')}")
if fv.get("registered_fact_source_coverage_pct") != 100.0:
    fails.append(f"A4 Fact Vault registered source coverage != 100: {fv.get('registered_fact_source_coverage_pct')}")
if not fv.get("facts"):
    fails.append("A4 Fact Vault contains no facts")
for key in [
    "facts_only",
    "master_decisions_forbidden",
    "cross_source_reconciliation_forbidden",
    "stale_or_failed_not_current",
    "last_good_substitution_forbidden",
    "missing_is_not_zero",
    "chat_memory_backfill_forbidden",
    "vault_coverage_is_not_master_coverage",
]:
    if policy.get(key) is not True:
        fails.append(f"A4 Fact Vault policy missing: {key}")
for sid, s in (fv.get("sources") or {}).items():
    if s.get("source_status") != "HEALTHY":
        fails.append(f"A4 active fact source not HEALTHY: {sid}={s.get('source_status')}")
run_check("A4_FACT_VAULT_CONTRACT", "money_master_os/tools/validate_shared_fact_vault_contract.py")
run_check("A4_FACT_ONLY_SELF_TEST", "shared_fact_vault/build_shared_facts.py", ["--mode", "self-test"])

# A5 OFFICIAL State safety.
oidx = load_json(OFFICIAL_INDEX)
if set((oidx.get("masters") or {}).keys()) != set(EXPECTED_MASTERS):
    fails.append("A5 OFFICIAL index does not contain exact five MASTERs")
run_check("A5_OFFICIAL_VALIDATE", "official_state/publish_official_state.py", ["--mode", "validate-latest"])
run_check("A5_OFFICIAL_SELF_TEST", "official_state/publish_official_state.py", ["--mode", "self-test"])

# A6 WATCH Event safety.
widx = load_json(WATCH_INDEX)
wm = widx.get("masters", {})
expected_watch = {"market": True, "btc_trend": False, "alt_top100": True, "alt_final20": True, "trading": False}
for mid, enabled in expected_watch.items():
    if (wm.get(mid) or {}).get("watch_enabled") is not enabled:
        fails.append(f"A6 WATCH policy mismatch: {mid}")
run_check("A6_WATCH_CONTRACT", "money_master_os/tools/validate_watch_event_contract.py")
run_check("A6_WATCH_VALIDATE", "watch_events/publish_watch_event.py", ["--mode", "validate-latest"])
run_check("A6_WATCH_SELF_TEST", "watch_events/publish_watch_event.py", ["--mode", "self-test"])

# A7 full five-MASTER operating E2E in isolated copy.
e2e = run_check("A7_FIVE_MASTER_E2E", "money_master_os/tools/test_operating_integration_all_masters.py")
if "P1_OPERATING_INTEGRATION_TEST=PASS" not in e2e.stdout:
    fails.append("A7 E2E PASS marker missing")

# A8 privacy + BTC research separation.
btc = masters.get("btc_trend", {})
if btc.get("production_version") != "V2.6" or btc.get("research_version") != "V3.0":
    fails.append("A8 BTC V2.6 production / V3.0 research separation broken")
btc_manifest = load_json(ROOT / btc.get("manifest_path", "__missing__")) if btc.get("manifest_path") else {}
research = btc_manifest.get("research", {})
if research.get("status") != "RESEARCH_ONLY" or research.get("official_state_promotion_forbidden") is not True:
    fails.append("A8 BTC research promotion guard missing")
trading = masters.get("trading", {})
trading_manifest = load_json(ROOT / trading.get("manifest_path", "__missing__")) if trading.get("manifest_path") else {}
privacy = trading_manifest.get("privacy", {})
if privacy.get("public_repository_allowed_for_personal_execution_state") is not False:
    fails.append("A8 TRADING public-repo privacy guard missing")
if trading.get("automation_policy") != "MANUAL_ONLY_UNTIL_EXPLICIT_USER_APPROVAL":
    fails.append("A8 TRADING manual-only policy drifted")

print("MONEY MASTER OS P2 ACCEPTANCE V1")
print(f"- required_gates=8")
print(f"- warnings={len(warnings)}")
for w in warnings:
    print(f"- WARNING: {w}")

if fails:
    print("P2_BASELINE_ACCEPTANCE=FAIL")
    for f in fails:
        print(f"- ERROR: {f}")
    sys.exit(1)

print("P2_BASELINE_ACCEPTANCE=PASS")
print("P2_FINAL_STATUS=LONG_RUN_PENDING")
print("- All immediate control-plane acceptance gates passed.")
print("- P2 is NOT final until the separate 24-hour long-run stability gate passes.")
