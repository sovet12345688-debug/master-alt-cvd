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
    "money_master_os/shared/COMMON_RULES.md",
    "money_master_os/shared/DATA_POLICY.md",
    "money_master_os/shared/RISK_GATE.md",
    "money_master_os/masters/market/manifest.json",
    "money_master_os/masters/alt/manifest.json",
    "money_master_os/masters/btc_trend/manifest.json",
    "money_master_os/bootstrap/BOOTSTRAP_LATEST.md",
    "money_master_os/handoff/HANDOFF_TEMPLATE.json",
    "money_master_os/handoff/MARKET_LATEST.json",
    "money_master_os/handoff/ALT_LATEST.json",
    "money_master_os/handoff/BTC_TREND_LATEST.json"
]
for rel in required_files:
    if not (ROOT / rel).exists():
        fail(f"required file missing: {rel}")

registry = load_json(REGISTRY) if REGISTRY.exists() else {}
if registry.get("schema_version") != "1.0":
    fail("registry schema_version must be 1.0")

policy = registry.get("global_policy", {})
for key in [
    "github_is_source_of_truth",
    "silent_reconstruction_forbidden",
    "version_drift_blocks_bootstrap",
    "missing_source_blocks_bootstrap",
    "destructive_migration_forbidden"
]:
    if policy.get(key) is not True:
        fail(f"global safety policy not locked true: {key}")

masters = registry.get("masters", {})
expected_master_keys = {"market", "alt", "btc_trend"}
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

# Canonical-source signature guards for sources that currently exist.
market_source = ROOT / "master_prompts/master_market_v1_2_current.md"
if market_source.exists():
    text = market_source.read_text(encoding="utf-8")
    if "MASTER MARKET V1.2 FINAL" not in text:
        fail("MARKET canonical signature missing")
    if "ABSOLUTE ANTI-OMISSION LOCK" not in text:
        fail("MARKET anti-omission lock missing")

alt_source = ROOT / "master_prompts/master_alt_final20_current.md"
if alt_source.exists():
    text = alt_source.read_text(encoding="utf-8")
    if "MASTER ALT V2.2.1" not in text:
        fail("ALT stored-source signature changed; registry drift classification must be reviewed")

# Latest handoff must mirror safe bootstrap disposition.
handoff_map = {
    "market": "money_master_os/handoff/MARKET_LATEST.json",
    "alt": "money_master_os/handoff/ALT_LATEST.json",
    "btc_trend": "money_master_os/handoff/BTC_TREND_LATEST.json"
}
for key, rel in handoff_map.items():
    path = ROOT / rel
    if not path.exists():
        continue
    handoff = load_json(path)
    entry = masters.get(key, {})
    if handoff.get("registry_status") != entry.get("status"):
        fail(f"{key}: handoff registry_status mismatch")
    h_allowed = handoff.get("migration", {}).get("bootstrap_allowed")
    if h_allowed != entry.get("bootstrap_allowed"):
        fail(f"{key}: handoff bootstrap_allowed mismatch")

if errors:
    print("MONEY MASTER OS VALIDATION: FAIL")
    for e in errors:
        print(f"- ERROR: {e}")
    sys.exit(1)

print("MONEY MASTER OS VALIDATION: PASS")
print("- READY masters may bootstrap only from exact canonical source.")
for n in notes:
    print(f"- {n}")
print("- No silent reconstruction or downgrade is permitted.")
