#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "money_master_os/registry/MASTER_REGISTRY.json"
BOOTSTRAP_PATH = ROOT / "money_master_os/bootstrap/BOOTSTRAP_LATEST.md"
OFFICIAL_INDEX_PATH = ROOT / "official_state/latest/index.json"

EXPECTED_IDS = ["market", "btc_trend", "alt_top100", "alt_final20", "trading"]
SOURCE_SIGNATURES = {
    "market": ["MASTER MARKET V1.2 FINAL"],
    "btc_trend": ["MASTER BTC TREND V2.6", "3-SCREEN", "NO WATCH"],
    "alt_top100": ["MASTER ALT V4.8 REAL-DATA CORE FINAL", "TOP100 DISCOVERY"],
    "alt_final20": ["MASTER ALT V2.2.1", "FINAL20"],
    "trading": ["MASTER TRADING", "TIME VALIDITY V2.1", "FINAL EXECUTION GATE"],
}

errors = []
receipts = []


def fail(master_id, msg):
    errors.append(f"{master_id}: {msg}")


def load_json(path, master_id="GLOBAL"):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        fail(master_id, f"cannot parse JSON {path.relative_to(ROOT)}: {e}")
        return {}


if not REGISTRY_PATH.exists():
    print("P0/P1 NEW-ROOM BOOTSTRAP TEST: FAIL")
    print("- GLOBAL: Registry missing")
    sys.exit(1)

registry = load_json(REGISTRY_PATH)
masters = registry.get("masters", {})
if list(masters.keys()) != EXPECTED_IDS:
    fail("GLOBAL", f"master identity/order mismatch: {list(masters.keys())}")

architecture = registry.get("architecture", {})
policy = registry.get("global_policy", {})
if architecture.get("master_count") != 5:
    fail("GLOBAL", "master_count must be 5")
if architecture.get("cross_master_blocking_dependency") is not False:
    fail("GLOBAL", "cross-master blocking dependency must remain false")
if architecture.get("official_state_index") != "official_state/latest/index.json":
    fail("GLOBAL", "official_state_index not registered")
for key in [
    "github_is_source_of_truth",
    "silent_reconstruction_forbidden",
    "official_state_requires_actual_official_run",
    "watch_and_provisional_cannot_be_official_state",
    "missing_official_state_is_not_reconstructed",
    "official_state_validity_is_not_inferred",
    "public_repo_private_trading_state_forbidden",
]:
    if policy.get(key) is not True:
        fail("GLOBAL", f"global policy not locked true: {key}")

bootstrap_text = BOOTSTRAP_PATH.read_text(encoding="utf-8") if BOOTSTRAP_PATH.exists() else ""
if not bootstrap_text:
    fail("GLOBAL", "bootstrap loader contract missing")
for required_text in [
    "official_state/latest/index.json",
    "NO_STORED_OFFICIAL_RUN",
    "legacy handoff",
    "UNKNOWN",
]:
    if required_text not in bootstrap_text:
        fail("GLOBAL", f"bootstrap OFFICIAL State rule missing: {required_text}")

official_index = load_json(OFFICIAL_INDEX_PATH) if OFFICIAL_INDEX_PATH.exists() else {}
if set((official_index.get("masters") or {}).keys()) != set(EXPECTED_IDS):
    fail("GLOBAL", "OFFICIAL State index must contain five exact MASTER IDs")

for master_id in EXPECTED_IDS:
    entry = masters.get(master_id)
    if not entry:
        fail(master_id, "registry entry missing")
        continue

    if entry.get("status") != "READY":
        fail(master_id, f"status is not READY: {entry.get('status')}")
    if entry.get("bootstrap_allowed") is not True:
        fail(master_id, "bootstrap_allowed must be true")
    if entry.get("expected_version") != entry.get("repo_version"):
        fail(master_id, "expected_version and repo_version differ")
    if entry.get("execution_policy") != "LOAD_EXACT_SOURCE_AND_VALIDATE":
        fail(master_id, "execution policy is not exact-source load+validate")

    manifest_rel = entry.get("manifest_path")
    source_rel = entry.get("source_path")
    contract_rel = entry.get("contract_path")
    official_rel = entry.get("official_state_path")

    if not manifest_rel:
        fail(master_id, "manifest_path missing")
        continue
    manifest_path = ROOT / manifest_rel
    if not manifest_path.exists():
        fail(master_id, f"manifest missing: {manifest_rel}")
        continue
    manifest = load_json(manifest_path, master_id)

    for field in ["expected_version", "repo_version", "status"]:
        if manifest.get(field) != entry.get(field):
            fail(master_id, f"registry/manifest mismatch: {field}")

    bootstrap = manifest.get("bootstrap", {})
    if bootstrap.get("allowed") is not True:
        fail(master_id, "manifest bootstrap.allowed must be true")
    if bootstrap.get("require_exact_version_match") is not True:
        fail(master_id, "manifest must require exact version match")
    if bootstrap.get("require_source_present") is not True:
        fail(master_id, "manifest must require source presence")
    if bootstrap.get("require_validator_pass") is not True:
        fail(master_id, "manifest must require validator PASS")
    if bootstrap.get("official_state_missing_behavior") != "DO_NOT_RECONSTRUCT":
        fail(master_id, "OFFICIAL State missing behavior must be DO_NOT_RECONSTRUCT")

    deps = manifest.get("shared_dependencies", [])
    if not deps:
        fail(master_id, "shared dependencies missing")
    for dep_rel in deps:
        if not (ROOT / dep_rel).exists():
            fail(master_id, f"shared dependency missing: {dep_rel}")

    if not source_rel:
        fail(master_id, "source_path missing")
        continue
    source_path = ROOT / source_rel
    if not source_path.exists():
        fail(master_id, f"canonical source missing: {source_rel}")
        continue
    if manifest.get("canonical_source") != source_rel:
        fail(master_id, "manifest canonical_source does not match registry source_path")
    source_text = source_path.read_text(encoding="utf-8")
    if len(source_text.strip()) < 100:
        fail(master_id, "canonical source is unexpectedly short")
    for signature in SOURCE_SIGNATURES[master_id]:
        if signature not in source_text:
            fail(master_id, f"canonical signature missing: {signature}")

    manifest_contract = manifest.get("machine_contract")
    if contract_rel or manifest_contract:
        if contract_rel != manifest_contract:
            fail(master_id, "registry contract_path and manifest machine_contract differ")
        elif not (ROOT / contract_rel).exists():
            fail(master_id, f"machine contract missing: {contract_rel}")
        elif not load_json(ROOT / contract_rel, master_id):
            fail(master_id, "machine contract empty/unparseable")

    if not official_rel:
        fail(master_id, "official_state_path missing")
    elif manifest.get("official_state") != official_rel:
        fail(master_id, "manifest official_state does not match Registry")
    elif not (ROOT / official_rel).exists():
        fail(master_id, f"OFFICIAL State file missing: {official_rel}")
    else:
        official = load_json(ROOT / official_rel, master_id)
        idx = (official_index.get("masters") or {}).get(master_id, {})
        if official.get("master_id") != master_id:
            fail(master_id, "OFFICIAL State master_id mismatch")
        if official.get("master_version") != entry.get("expected_version"):
            fail(master_id, "OFFICIAL State version mismatch")
        if official.get("source_path") != source_rel:
            fail(master_id, "OFFICIAL State source_path mismatch")
        if (official.get("lineage") or {}).get("cross_master_decision_dependency") is not False:
            fail(master_id, "OFFICIAL State cross-master decision dependency must be false")
        if idx.get("state_path") != official_rel:
            fail(master_id, "OFFICIAL index state_path mismatch")
        if idx.get("state_status") != official.get("state_status"):
            fail(master_id, "OFFICIAL index state_status mismatch")
        if official.get("state_status") == "NO_STORED_OFFICIAL_RUN":
            run = official.get("run") or {}
            decision = official.get("decision") or {}
            if run.get("run_id") is not None:
                fail(master_id, "placeholder must not invent run_id")
            if any(decision.get(k) is not None for k in ["direction", "permission_or_environment", "action"]):
                fail(master_id, "placeholder must not invent decision")

    if f"`{master_id}`" not in bootstrap_text:
        fail(master_id, "bootstrap loader does not list master id")
    if source_rel not in bootstrap_text:
        fail(master_id, "bootstrap loader does not identify canonical source path")

    if master_id == "btc_trend":
        if entry.get("production_version") != "V2.6" or entry.get("research_version") != "V3.0":
            fail(master_id, "V2.6 production / V3.0 research separation broken")
        research = manifest.get("research", {})
        if research.get("version") != "V3.0" or research.get("status") != "RESEARCH_ONLY":
            fail(master_id, "manifest research separation broken")
        if research.get("official_state_promotion_forbidden") is not True:
            fail(master_id, "V3 research -> production OFFICIAL State guard missing")

    if master_id == "trading":
        privacy = manifest.get("privacy", {})
        if privacy.get("public_repository_allowed_for_personal_execution_state") is not False:
            fail(master_id, "public-repo personal execution privacy guard missing")
        if entry.get("automation_policy") != "MANUAL_ONLY_UNTIL_EXPLICIT_USER_APPROVAL":
            fail(master_id, "TRADING manual-only policy drifted")

    master_failed = any(e.startswith(master_id + ":") for e in errors)
    state_status = ((official_index.get("masters") or {}).get(master_id) or {}).get("state_status")
    receipts.append({
        "master_id": master_id,
        "version": entry.get("repo_version"),
        "canonical_source": source_rel,
        "contract": contract_rel,
        "official_state": official_rel,
        "official_status": state_status,
        "bootstrap": "FAIL" if master_failed else "PASS",
    })

print("P0/P1 NEW-ROOM BOOTSTRAP TEST RECEIPTS")
for r in receipts:
    print(
        f"- {r['master_id']} | {r['version']} | source={r['canonical_source']} | "
        f"official={r['official_status']} | bootstrap={r['bootstrap']}"
    )

if errors:
    print("P0/P1 NEW-ROOM BOOTSTRAP TEST: FAIL")
    for e in errors:
        print(f"- ERROR: {e}")
    sys.exit(1)

print("P0/P1 NEW-ROOM BOOTSTRAP TEST: PASS")
print("- All five MASTER identities/sources and exact OFFICIAL State pointers can be restored without chat-memory reconstruction.")
print("- NO_STORED_OFFICIAL_RUN remains explicit rather than being synthetically backfilled.")
