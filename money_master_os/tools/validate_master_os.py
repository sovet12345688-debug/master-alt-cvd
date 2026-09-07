#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "money_master_os/registry/MASTER_REGISTRY.json"

errors = []
notes = []


def fail(msg):
    errors.append(msg)


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        fail(f"cannot parse {path.relative_to(ROOT)}: {e}")
        return {}

required_files = [
    "money_master_os/README.md",
    "money_master_os/registry/MASTER_REGISTRY.json",
    "money_master_os/registry/MASTER_REGISTRY_V1_LEGACY.json",
    "money_master_os/shared/COMMON_RULES.md",
    "money_master_os/shared/DATA_POLICY.md",
    "money_master_os/shared/RISK_GATE.md",
    "money_master_os/masters/market/manifest.json",
    "money_master_os/masters/btc_trend/manifest.json",
    "money_master_os/masters/alt_top100/manifest.json",
    "money_master_os/masters/alt_final20/manifest.json",
    "money_master_os/masters/trading/manifest.json",
    "money_master_os/bootstrap/BOOTSTRAP_LATEST.md",
    "money_master_os/handoff/HANDOFF_TEMPLATE.json"
]
for rel in required_files:
    if not (ROOT / rel).exists():
        fail(f"required file missing: {rel}")

registry = load_json(REGISTRY) if REGISTRY.exists() else {}
if registry.get("schema_version") != "2.0":
    fail("registry schema_version must be 2.0")

policy = registry.get("global_policy", {})
for key in [
    "github_is_source_of_truth",
    "chat_is_execution_surface",
    "silent_reconstruction_forbidden",
    "version_drift_blocks_bootstrap",
    "missing_source_blocks_bootstrap",
    "destructive_migration_forbidden",
    "five_master_identity_locked",
    "shared_facts_do_not_imply_shared_conclusions"
]:
    if policy.get(key) is not True:
        fail(f"global safety policy not locked true: {key}")

architecture = registry.get("architecture", {})
expected_master_keys = {"market", "btc_trend", "alt_top100", "alt_final20", "trading"}
if architecture.get("master_count") != 5:
    fail("architecture master_count must be 5")
if architecture.get("cross_master_blocking_dependency") is not False:
    fail("cross_master_blocking_dependency must be false")
if architecture.get("production_research_separation_required") is not True:
    fail("production_research_separation_required must be true")

masters = registry.get("masters", {})
if set(masters) != expected_master_keys:
    fail(f"registry masters mismatch: expected {sorted(expected_master_keys)}, got {sorted(masters)}")

for key, entry in masters.items():
    manifest_rel = entry.get("manifest_path")
    if not manifest_rel:
        fail(f"{key}: manifest_path missing")
        continue
    manifest_path = ROOT / manifest_rel
    if not manifest_path.exists():
        fail(f"{key}: manifest missing at {manifest_rel}")
        continue
    manifest = load_json(manifest_path)

    for field in ["expected_version", "repo_version", "status"]:
        if manifest.get(field) != entry.get(field):
            fail(f"{key}: registry/manifest mismatch for {field}")

    status = entry.get("status")
    expected = entry.get("expected_version")
    repo_version = entry.get("repo_version")
    source_rel = entry.get("source_path")
    allowed = entry.get("bootstrap_allowed")

    if status == "READY":
        if not allowed:
            fail(f"{key}: READY but bootstrap_allowed=false")
        if not source_rel or not (ROOT / source_rel).exists():
            fail(f"{key}: READY but canonical source missing")
        if expected != repo_version:
            fail(f"{key}: READY but expected/repo versions differ")
        notes.append(f"{key}: READY with exact canonical source")
    elif status == "VERSION_DRIFT":
        if allowed:
            fail(f"{key}: VERSION_DRIFT must block bootstrap")
        if not source_rel or not (ROOT / source_rel).exists():
            fail(f"{key}: VERSION_DRIFT should identify the existing stale source")
        if expected == repo_version:
            fail(f"{key}: VERSION_DRIFT but versions are equal")
        notes.append(f"{key}: expected drift safely blocked ({repo_version} -> {expected})")
    elif status == "SOURCE_MISSING":
        if allowed:
            fail(f"{key}: SOURCE_MISSING must block bootstrap")
        if source_rel is not None:
            fail(f"{key}: SOURCE_MISSING must not point to a guessed canonical source")
        notes.append(f"{key}: missing canonical source safely blocked")
    else:
        fail(f"{key}: unsupported status {status}")

# Exact identity guards.
if "alt" in masters:
    fail("legacy ambiguous registry key 'alt' is forbidden in schema 2.0")
if "alt_top100" not in masters or "alt_final20" not in masters:
    fail("ALT TOP100 and ALT FINAL20 must be separate masters")

btc = masters.get("btc_trend", {})
if btc.get("production_version") != "V2.6":
    fail("btc_trend production_version must remain V2.6 during Phase 1")
if btc.get("research_version") != "V3.0":
    fail("btc_trend research_version must remain V3.0 during Phase 1")
if btc.get("research_policy") != "V3.0_RESEARCH_ONLY_UNTIL_ACCEPTANCE_AND_CANONICAL_PROMOTION":
    fail("btc_trend research policy must block silent V3.0 promotion")

# Canonical-source signature guards for sources that currently exist.
market_source = ROOT / "master_prompts/master_market_v1_2_current.md"
if market_source.exists():
    text = market_source.read_text(encoding="utf-8")
    if "MASTER MARKET V1.2 FINAL" not in text:
        fail("MARKET canonical signature missing")
    if "ABSOLUTE ANTI-OMISSION LOCK" not in text:
        fail("MARKET anti-omission lock missing")

alt_final20_source = ROOT / "master_prompts/master_alt_final20_current.md"
if alt_final20_source.exists():
    text = alt_final20_source.read_text(encoding="utf-8")
    if "MASTER ALT V2.2.1" not in text:
        fail("ALT FINAL20 stored-source signature changed")

# Privacy safety: public repository must not claim to store personal trading state.
trading = masters.get("trading", {})
if trading.get("privacy_policy") != "PERSONAL_POSITION_BALANCE_AND_ACCOUNT_DATA_MUST_NOT_BE_STORED_IN_PUBLIC_REPOSITORY":
    fail("trading privacy policy missing")

if errors:
    print("MONEY MASTER OS V2 PHASE 1 VALIDATION: FAIL")
    for e in errors:
        print(f"- ERROR: {e}")
    sys.exit(1)

print("MONEY MASTER OS V2 PHASE 1 VALIDATION: PASS")
print("- Exactly five independent MASTER identities are registered.")
print("- ALT TOP100 and ALT FINAL20 are separated.")
print("- BTC TREND V2.6 production and V3.0 research are separated.")
print("- SOURCE_MISSING masters remain blocked from unsafe bootstrap.")
for n in notes:
    print(f"- {n}")
print("- Shared facts may be reused, but MASTER conclusions remain independent.")
