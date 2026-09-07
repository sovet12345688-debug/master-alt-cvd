#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REG = ROOT / "source_health/registry.json"
COLLECTOR = ROOT / "source_health/collector.py"
README = ROOT / "source_health/README.md"
WORKFLOW = ROOT / ".github/workflows/source_health_hourly.yml"
MASTER_REGISTRY = ROOT / "money_master_os/registry/MASTER_REGISTRY.json"
DATA_POLICY = ROOT / "money_master_os/shared/DATA_POLICY.md"
BOOTSTRAP = ROOT / "money_master_os/bootstrap/BOOTSTRAP_LATEST.md"

errors = []

def fail(msg):
    errors.append(msg)

for p in [REG, COLLECTOR, README, WORKFLOW, MASTER_REGISTRY, DATA_POLICY, BOOTSTRAP]:
    if not p.exists():
        fail(f"missing required Source Health file: {p.relative_to(ROOT)}")

try:
    reg = json.loads(REG.read_text(encoding="utf-8")) if REG.exists() else {}
except Exception as exc:
    reg = {}
    fail(f"source_health registry invalid JSON: {exc}")

if reg.get("schema_version") != "1.0":
    fail("Source Health registry schema_version must be 1.0")

policy = reg.get("policy") or {}
for key in [
    "health_is_data_availability_not_trade_permission",
    "missing_is_not_zero",
    "stale_is_not_current",
    "optional_source_failure_does_not_auto_block_master",
    "last_good_is_pointer_only_not_current_fact",
    "master_decisions_remain_independent",
    "registered_source_health_is_not_master_coverage",
]:
    if policy.get(key) is not True:
        fail(f"Source Health policy must be true: {key}")

sources = reg.get("sources") or {}
if len(sources) < 8:
    fail("Source Health must monitor at least 8 operational sources")

valid_impact = {"CORE", "OPTIONAL", "CONTEXT"}
for sid, src in sources.items():
    for key in ["workflow_file", "state_path", "timestamp_field", "warn_after_minutes", "stale_after_minutes", "impact"]:
        if key not in src:
            fail(f"{sid}: missing {key}")
    if src.get("warn_after_minutes", 0) >= src.get("stale_after_minutes", 0):
        fail(f"{sid}: warn threshold must be below stale threshold")
    for master, impact in (src.get("impact") or {}).items():
        if impact not in valid_impact:
            fail(f"{sid}: invalid impact {master}={impact}")

try:
    mreg = json.loads(MASTER_REGISTRY.read_text(encoding="utf-8")) if MASTER_REGISTRY.exists() else {}
except Exception as exc:
    mreg = {}
    fail(f"MASTER registry invalid JSON: {exc}")
arch = mreg.get("architecture") or {}
gp = mreg.get("global_policy") or {}
if arch.get("source_health_registry") != "source_health/registry.json":
    fail("MASTER registry Source Health registry pointer mismatch")
if arch.get("source_health_latest") != "source_health/output/latest.json":
    fail("MASTER registry Source Health latest pointer mismatch")
if arch.get("source_health_last_good") != "source_health/state/last_good.json":
    fail("MASTER registry Source Health last_good pointer mismatch")
if arch.get("source_health_policy") != "DATA_AVAILABILITY_ONLY_NOT_MASTER_DECISION":
    fail("MASTER registry Source Health authority boundary missing")
if gp.get("source_health_does_not_imply_trade_permission") is not True:
    fail("Source Health trade-permission separation missing")
if gp.get("last_good_does_not_become_current_fact") is not True:
    fail("last_good current-fact guard missing")

collector_text = COLLECTOR.read_text(encoding="utf-8") if COLLECTOR.exists() else ""
for signature in [
    '"registered_source_health_pct"',
    '"registered_source_availability"',
    '"scope_note"',
    'This is not MASTER Coverage',
]:
    if signature not in collector_text:
        fail(f"collector scope signature missing: {signature}")
for forbidden in ['"health_pct": pct', '"data_availability": availability']:
    if forbidden in collector_text:
        fail(f"ambiguous legacy Source Health field forbidden: {forbidden}")

policy_text = DATA_POLICY.read_text(encoding="utf-8") if DATA_POLICY.exists() else ""
for signature in [
    "DATA AVAILABILITY metadata only",
    "last_good",
    "Source Health reason codes",
]:
    if signature not in policy_text:
        fail(f"DATA_POLICY Source Health signature missing: {signature}")

bootstrap_text = BOOTSTRAP.read_text(encoding="utf-8") if BOOTSTRAP.exists() else ""
for signature in [
    "source_health/output/latest.json",
    "registered_source_health_pct",
    "NOT the MASTER's own Coverage",
]:
    if signature not in bootstrap_text:
        fail(f"bootstrap Source Health signature missing: {signature}")

workflow_text = WORKFLOW.read_text(encoding="utf-8") if WORKFLOW.exists() else ""
for signature in [
    "cron: '5 * * * *'",
    "actions: read",
    "contents: write",
    "--mode dry-run",
    "--mode collect",
    "--mode validate",
]:
    if signature not in workflow_text:
        fail(f"Source Health workflow signature missing: {signature}")

if errors:
    print("SOURCE HEALTH CONTRACT VALIDATION: FAIL")
    for e in errors:
        print(f"- ERROR: {e}")
    sys.exit(1)

print("SOURCE HEALTH CONTRACT VALIDATION: PASS")
print(f"- registered_sources={len(sources)}")
print("- Source Health remains data-availability only.")
print("- registered_source_health_pct is separated from MASTER Coverage.")
print("- optional source failure cannot silently become a trade veto.")
print("- last_good cannot silently become a current fact.")
