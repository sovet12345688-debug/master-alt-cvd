#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "money_master_os/registry/MASTER_REGISTRY.json"
WATCH_REGISTRY = ROOT / "watch_events/registry.json"
WATCH_SCHEMA = ROOT / "watch_events/schema.json"
WATCH_INDEX = ROOT / "watch_events/latest/index.json"
MASTER_IDS = {"market", "btc_trend", "alt_top100", "alt_final20", "trading"}
errors = []


def fail(msg):
    errors.append(msg)


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"cannot parse {path.relative_to(ROOT)}: {exc}")
        return {}

for rel in [
    "watch_events/README.md",
    "watch_events/schema.json",
    "watch_events/registry.json",
    "watch_events/latest/index.json",
    "watch_events/publish_watch_event.py",
]:
    if not (ROOT / rel).exists():
        fail(f"required WATCH event file missing: {rel}")

osr = load(REGISTRY) if REGISTRY.exists() else {}
wr = load(WATCH_REGISTRY) if WATCH_REGISTRY.exists() else {}
schema = load(WATCH_SCHEMA) if WATCH_SCHEMA.exists() else {}
idx = load(WATCH_INDEX) if WATCH_INDEX.exists() else {}

arch = osr.get("architecture") or {}
expected_paths = {
    "watch_event_root": "watch_events/",
    "watch_event_registry": "watch_events/registry.json",
    "watch_event_schema": "watch_events/schema.json",
    "watch_event_index": "watch_events/latest/index.json",
    "watch_event_policy": "MEANINGFUL_CHANGE_ONLY_MASTER_AUTHORED_NO_CENTRAL_RECALCULATION",
}
for key, value in expected_paths.items():
    if arch.get(key) != value:
        fail(f"Registry architecture {key} mismatch")

policy = osr.get("global_policy") or {}
for key in [
    "watch_event_no_change_no_write",
    "watch_event_never_official_state",
    "watch_event_central_recalculation_forbidden",
    "watch_event_historical_backfill_forbidden",
    "watch_event_execution_order_forbidden",
]:
    if policy.get(key) is not True:
        fail(f"Registry WATCH policy not locked true: {key}")

masters = osr.get("masters") or {}
if set(masters) != MASTER_IDS:
    fail("Registry must contain exactly five MASTERs")
producers = wr.get("master_producers") or {}
if set(producers) != MASTER_IDS:
    fail("WATCH producer registry must contain exactly five MASTERs")

expected_enabled = {"market": True, "btc_trend": False, "alt_top100": True, "alt_final20": True, "trading": False}
for mid, enabled in expected_enabled.items():
    if (producers.get(mid) or {}).get("enabled") is not enabled:
        fail(f"{mid}: WATCH enabled state mismatch")
    if (idx.get("masters") or {}).get(mid, {}).get("watch_enabled") is not enabled:
        fail(f"{mid}: WATCH latest index state mismatch")
    entry_policy = (masters.get(mid) or {}).get("watch_event_policy")
    if enabled and entry_policy != "CANONICAL_MASTER_WATCH_PUBLISH_ALLOWED":
        fail(f"{mid}: Registry watch_event_policy must allow canonical MASTER WATCH")
    if mid == "btc_trend" and entry_policy != "DISABLED_NO_HOURLY_WATCH":
        fail("btc_trend must preserve NO hourly WATCH")
    if mid == "trading" and entry_policy != "DISABLED_MANUAL_ONLY":
        fail("trading must preserve manual-only policy")

schema_policy = schema.get("policy") or {}
for key in [
    "no_change_no_event", "unchanged_repeat_forbidden", "append_only_history",
    "same_event_id_duplicate_forbidden", "master_authored_thresholds_only",
    "central_recalculation_forbidden", "watch_cannot_be_official_state",
    "watch_cannot_create_execution_order", "enter_add_small_enter_forbidden",
    "private_trading_state_forbidden", "chat_memory_backfill_forbidden",
    "provisional_or_reconstructed_event_forbidden",
]:
    if schema_policy.get(key) is not True:
        fail(f"WATCH schema policy not locked true: {key}")

# Canonical-rule ownership guards: the event store must reflect, not replace, these rules.
canonical_checks = {
    "market": ["## URGENT WATCH", "LEVEL1:", "LEVEL2:"],
    "alt_top100": ["HUNTER WATCH ALERT", "단순1~2% 가격변화 금지"],
    "alt_final20": ["WATCH\n독립축>=3 + CoreCoverage>=70", "단순 1~2% 가격변화 금지"],
    "btc_trend": ["NO hourly WATCH"],
    "trading": ["MASTER TRADING recurring automation is OFF"],
}
for mid, signatures in canonical_checks.items():
    source = ROOT / str((masters.get(mid) or {}).get("source_path"))
    if not source.exists():
        fail(f"{mid}: canonical source missing")
        continue
    text = source.read_text(encoding="utf-8")
    for sig in signatures:
        if sig not in text:
            fail(f"{mid}: WATCH canonical signature missing: {sig}")

idx_policy = idx.get("policy") or {}
for key in ["empty_index_is_valid", "no_change_is_not_persisted", "watch_is_not_official", "no_historical_backfill"]:
    if idx_policy.get(key) is not True:
        fail(f"WATCH index policy not locked true: {key}")
if not isinstance(idx.get("event_count_total"), int) or idx.get("event_count_total") < 0:
    fail("WATCH index event_count_total invalid")

if errors:
    print("WATCH EVENT CONTRACT VALIDATION: FAIL")
    for err in errors:
        print("- ERROR:", err)
    sys.exit(1)

print("WATCH EVENT CONTRACT VALIDATION: PASS")
print("- MARKET/ALT1/ALT2 canonical WATCH producers are enabled.")
print("- BTC V2.6 hourly WATCH and TRADING recurring WATCH remain disabled.")
print("- Central event layer persists/deduplicates only; no central score/direction recalculation.")
print("- NO CHANGE produces no event; WATCH never becomes OFFICIAL State or an execution order.")
