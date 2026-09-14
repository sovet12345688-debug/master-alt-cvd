#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REG = ROOT / "money_os_work_v3/registry/SYSTEM_REGISTRY.json"
ISO = ROOT / "money_os_work_v3/registry/ISOLATION_POLICY.json"

errors = []

def fail(msg):
    errors.append(msg)

def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        fail(f"cannot load {path}: {e}")
        return {}

reg = load(REG)
iso = load(ISO)

expected = {"alt_top100", "alt_final20", "market", "btc_trend", "youtuber_view", "trading"}

if reg.get("schema_version") != "3.0":
    fail("registry schema_version must be 3.0")
arch = reg.get("architecture", {})
if arch.get("system_count") != 6:
    fail("system_count must be 6")
if set(arch.get("system_ids", [])) != expected:
    fail("system_ids mismatch")
for k in [
    "cross_system_read",
    "cross_system_write",
    "cross_system_score_dependency",
    "cross_system_state_dependency",
    "cross_system_history_dependency",
    "shared_fact_store",
    "shared_source_health",
    "shared_official_state",
    "shared_watch_store",
    "shared_ui_renderer",
    "shared_market_snapshot",
]:
    if arch.get(k) is not False:
        fail(f"architecture isolation flag must be false: {k}")
if arch.get("parent_data_plane_access") != "NONE":
    fail("parent_data_plane_access must be NONE")

systems = reg.get("systems", {})
if set(systems) != expected:
    fail("registry systems mismatch")

roots = {}
for sid, entry in systems.items():
    root = entry.get("system_root")
    runtime = entry.get("runtime_root")
    if not root or not runtime:
        fail(f"{sid}: missing local root")
        continue
    if not runtime.startswith(root + "/"):
        fail(f"{sid}: runtime root must be inside system root")
    if root in roots:
        fail(f"duplicate system root: {root}")
    roots[root] = sid
    if entry.get("ui_policy") is None:
        fail(f"{sid}: UI freeze policy missing")
    c = entry.get("canonical_source")
    if not c or not (ROOT / c).exists():
        fail(f"{sid}: canonical source missing: {c}")
    mc = entry.get("machine_contract")
    if mc and not (ROOT / mc).exists():
        fail(f"{sid}: machine contract missing: {mc}")

if iso.get("policy") != "ABSOLUTE_RUNTIME_ISOLATION":
    fail("isolation policy name mismatch")
iso_systems = iso.get("systems", {})
if set(iso_systems) != expected:
    fail("isolation systems mismatch")

legacy_forbidden = {"shared_fact_vault/**", "source_health/**", "official_state/**", "watch_events/**"}
for sid, p in iso_systems.items():
    writes = p.get("allowed_runtime_write", [])
    expected_prefix = f"money_os_work_v3/systems/{sid}/runtime/"
    if not writes or any(not w.startswith(expected_prefix) for w in writes):
        fail(f"{sid}: write allowlist escapes local runtime")
    reads = set(p.get("forbidden_runtime_read", []))
    if not legacy_forbidden.issubset(reads):
        fail(f"{sid}: legacy global runtime stores must all be forbidden")
    for other in expected - {sid}:
        other_glob = f"money_os_work_v3/systems/{other}/**"
        if other_glob not in reads:
            fail(f"{sid}: missing forbidden cross-system read {other_glob}")

policy = reg.get("global_policy", {})
for key in [
    "ui_frozen_during_migration",
    "chat_memory_is_not_source_of_truth",
    "prospective_history_only",
    "historical_reconstruction_forbidden",
    "na_to_zero_forbidden",
    "silent_backfill_forbidden",
    "system_failure_propagation_forbidden",
    "legacy_global_runtime_read_after_cutover_forbidden",
    "main_branch_untouched_until_final_approval",
]:
    if policy.get(key) is not True:
        fail(f"global policy must be true: {key}")

if errors:
    print("MONEY OS WORK V3 ISOLATION = FAIL")
    for e in errors:
        print("-", e)
    sys.exit(1)

print("MONEY OS WORK V3 ISOLATION = PASS")
print("6 independent system namespaces validated")
print("No shared runtime data-plane policy enabled")
