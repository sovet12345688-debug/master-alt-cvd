#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "money_master_os/registry/MASTER_REGISTRY.json"
BOOTSTRAP_PATH = ROOT / "money_master_os/bootstrap/BOOTSTRAP_LATEST.md"

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
    print("P0 NEW-ROOM BOOTSTRAP TEST: FAIL")
    print("- GLOBAL: Registry missing")
    sys.exit(1)

registry = load_json(REGISTRY_PATH)
masters = registry.get("masters", {})
if list(masters.keys()) != EXPECTED_IDS:
    fail("GLOBAL", f"master identity/order mismatch: {list(masters.keys())}")

if registry.get("architecture", {}).get("master_count") != 5:
    fail("GLOBAL", "master_count must be 5")
if registry.get("architecture", {}).get("cross_master_blocking_dependency") is not False:
    fail("GLOBAL", "cross-master blocking dependency must remain false")
if registry.get("global_policy", {}).get("github_is_source_of_truth") is not True:
    fail("GLOBAL", "GitHub source-of-truth policy missing")
if registry.get("global_policy", {}).get("silent_reconstruction_forbidden") is not True:
    fail("GLOBAL", "silent reconstruction must be forbidden")

bootstrap_text = BOOTSTRAP_PATH.read_text(encoding="utf-8") if BOOTSTRAP_PATH.exists() else ""
if not bootstrap_text:
    fail("GLOBAL", "bootstrap loader contract missing")

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
        else:
            contract = load_json(ROOT / contract_rel, master_id)
            if not contract:
                fail(master_id, "machine contract empty/unparseable")

    # New-room loader must explicitly know this master id and canonical source.
    if f"`{master_id}`" not in bootstrap_text:
        fail(master_id, "bootstrap loader does not list master id")
    if source_rel not in bootstrap_text:
        fail(master_id, "bootstrap loader does not identify canonical source path")

    # MASTER-specific non-drift invariants.
    if master_id == "btc_trend":
        if entry.get("production_version") != "V2.6" or entry.get("research_version") != "V3.0":
            fail(master_id, "V2.6 production / V3.0 research separation broken")
        if entry.get("research_policy") != "V3.0_RESEARCH_ONLY_UNTIL_ACCEPTANCE_AND_CANONICAL_PROMOTION":
            fail(master_id, "V3.0 silent-promotion guard missing")
        research = manifest.get("research", {})
        if research.get("version") != "V3.0" or research.get("status") != "RESEARCH_ONLY":
            fail(master_id, "manifest research separation broken")

    if master_id == "trading":
        privacy = manifest.get("privacy", {})
        if privacy.get("public_repository_allowed_for_personal_execution_state") is not False:
            fail(master_id, "public-repo personal execution privacy guard missing")
        if entry.get("automation_policy") != "MANUAL_ONLY_UNTIL_EXPLICIT_USER_APPROVAL":
            fail(master_id, "TRADING manual-only policy drifted")

    master_failed = any(e.startswith(master_id + ":") for e in errors)
    receipts.append({
        "master_id": master_id,
        "display_name": entry.get("display_name"),
        "version": entry.get("repo_version"),
        "manifest": manifest_rel,
        "canonical_source": source_rel,
        "contract": contract_rel,
        "bootstrap": "FAIL" if master_failed else "PASS",
    })

print("P0 NEW-ROOM BOOTSTRAP TEST RECEIPTS")
for r in receipts:
    print(
        f"- {r['master_id']} | {r['version']} | source={r['canonical_source']} | "
        f"contract={r['contract'] or 'N/A'} | bootstrap={r['bootstrap']}"
    )

if errors:
    print("P0 NEW-ROOM BOOTSTRAP TEST: FAIL")
    for e in errors:
        print(f"- ERROR: {e}")
    sys.exit(1)

print("P0 NEW-ROOM BOOTSTRAP TEST: PASS")
print("- All five MASTERs can be reconstructed from GitHub identity/source contracts without chat-memory reconstruction.")
print("- P0 identity, canonical-source, bootstrap, production/research separation, and privacy checks passed.")
