#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "money_master_os/registry/MASTER_REGISTRY.json"
FACT_REGISTRY_PATH = ROOT / "shared_fact_vault/registry.json"
SOURCE_HEALTH_REGISTRY_PATH = ROOT / "source_health/registry.json"
BOOTSTRAP_PATH = ROOT / "money_master_os/bootstrap/BOOTSTRAP_LATEST.md"

errors = []


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        errors.append(f"cannot parse {path.relative_to(ROOT)}: {e}")
        return {}


required_files = [
    "shared_fact_vault/README.md",
    "shared_fact_vault/registry.json",
    "shared_fact_vault/schema.json",
    "shared_fact_vault/build_shared_facts.py",
    "shared_fact_vault/output/latest.json",
    ".github/workflows/shared_fact_vault_hourly.yml"
]
for rel in required_files:
    if not (ROOT / rel).exists():
        errors.append(f"required file missing: {rel}")

os_registry = load(REGISTRY_PATH)
fact_registry = load(FACT_REGISTRY_PATH)
health_registry = load(SOURCE_HEALTH_REGISTRY_PATH)

architecture = os_registry.get("architecture", {})
expected_paths = {
    "shared_fact_vault_root": "shared_fact_vault/",
    "shared_fact_vault_registry": "shared_fact_vault/registry.json",
    "shared_fact_vault_latest": "shared_fact_vault/output/latest.json",
    "shared_fact_vault_policy": "FACTS_ONLY_SOURCE_QUALIFIED_NO_CROSS_SOURCE_RECONCILIATION"
}
for key, value in expected_paths.items():
    if architecture.get(key) != value:
        errors.append(f"OS Registry Shared Fact Vault pointer mismatch: {key}")

policy = os_registry.get("global_policy", {})
for key in [
    "shared_facts_do_not_imply_shared_conclusions",
    "shared_fact_vault_is_facts_only",
    "shared_fact_vault_cross_source_reconciliation_forbidden",
    "shared_fact_vault_stale_failed_not_current",
    "shared_fact_vault_does_not_replace_master_revalidation",
    "shared_fact_vault_coverage_is_not_master_coverage"
]:
    if policy.get(key) is not True:
        errors.append(f"OS Registry policy not locked true: {key}")

fact_policy = fact_registry.get("policy", {})
for key in [
    "facts_only", "master_decisions_forbidden", "cross_source_reconciliation_forbidden",
    "stale_or_failed_not_current", "last_good_substitution_forbidden", "missing_is_not_zero",
    "chat_memory_backfill_forbidden", "raw_high_volume_duplication_forbidden",
    "vault_coverage_is_not_master_coverage"
]:
    if fact_policy.get(key) is not True:
        errors.append(f"Fact Vault policy not locked true: {key}")

master_ids = set(os_registry.get("masters", {}))
health_sources = set(health_registry.get("sources", {}))
active = fact_registry.get("active_adapters", {})
deferred = fact_registry.get("deferred_sources", {})

if set(active).intersection(deferred):
    errors.append("a source cannot be both ACTIVE and DEFERRED")
if len(active) < 4:
    errors.append("unexpectedly low active adapter count")

for source_id, cfg in active.items():
    health_id = cfg.get("source_health_id")
    if health_id not in health_sources:
        errors.append(f"{source_id}: source_health_id not registered: {health_id}")
    data_path = cfg.get("data_path")
    if not data_path or not (ROOT / data_path).exists():
        errors.append(f"{source_id}: data_path missing: {data_path}")
    consumers = set(cfg.get("allowed_consumers", []))
    if not consumers:
        errors.append(f"{source_id}: allowed_consumers empty")
    unknown = consumers - master_ids
    if unknown:
        errors.append(f"{source_id}: unknown consumers {sorted(unknown)}")

bootstrap = BOOTSTRAP_PATH.read_text(encoding="utf-8") if BOOTSTRAP_PATH.exists() else ""
for token in [
    "shared_fact_vault/output/latest.json",
    "current_usable=false",
    "Never reconcile conflicting sources silently",
    "Vault fact-source coverage is not MASTER Coverage"
]:
    if token not in bootstrap:
        errors.append(f"bootstrap Shared Fact Vault rule missing: {token}")

if errors:
    print("SHARED FACT VAULT CONTRACT: FAIL")
    for e in errors:
        print(f"- ERROR: {e}")
    sys.exit(1)

print("SHARED FACT VAULT CONTRACT: PASS")
print(f"- active_adapters={len(active)}")
print(f"- deferred_sources={len(deferred)}")
print("- Registry, Source Health, data paths, consumers and bootstrap boundaries are aligned.")
print("- Facts are shared; MASTER decisions remain independent.")
