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
    "money_master_os/handoff/HANDOFF_TEMPLATE.json",
    "master_prompts/master_btc_trend_v2_6_current.md",
    "state/master_btc_trend_v2_6_contract.json",
    "master_prompts/master_trading_current.md",
    "state/master_trading_current_contract.json"
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
        if manifest.get("canonical_source") != source_rel:
            fail(f"{key}: registry source_path / manifest canonical_source mismatch")
        contract_rel = entry.get("contract_path")
        manifest_contract = manifest.get("machine_contract")
        if contract_rel or manifest_contract:
            if contract_rel != manifest_contract:
                fail(f"{key}: registry contract_path / manifest machine_contract mismatch")
            elif not (ROOT / contract_rel).exists():
                fail(f"{key}: READY but machine contract missing")
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

# All five must be READY after Phase 2 source recovery.
if set(masters) == expected_master_keys:
    non_ready = {k: v.get("status") for k, v in masters.items() if v.get("status") != "READY"}
    if non_ready:
        fail(f"Phase 2 requires all five masters READY, non-ready={non_ready}")

btc = masters.get("btc_trend", {})
if btc.get("production_version") != "V2.6":
    fail("btc_trend production_version must remain V2.6")
if btc.get("research_version") != "V3.0":
    fail("btc_trend research_version must remain V3.0")
if btc.get("research_policy") != "V3.0_RESEARCH_ONLY_UNTIL_ACCEPTANCE_AND_CANONICAL_PROMOTION":
    fail("btc_trend research policy must block silent V3.0 promotion")
if btc.get("expected_version") != "V2.6 PRODUCTION" or btc.get("repo_version") != "V2.6 PRODUCTION":
    fail("btc_trend production registry version must be V2.6 PRODUCTION")
if btc.get("source_path") != "master_prompts/master_btc_trend_v2_6_current.md":
    fail("btc_trend canonical source path mismatch")
if btc.get("contract_path") != "state/master_btc_trend_v2_6_contract.json":
    fail("btc_trend machine contract path mismatch")

# Canonical-source signature guards.
market_source = ROOT / "master_prompts/master_market_v1_2_current.md"
if market_source.exists():
    text = market_source.read_text(encoding="utf-8")
    if "MASTER MARKET V1.2 FINAL" not in text:
        fail("MARKET canonical signature missing")
    if "ABSOLUTE ANTI-OMISSION LOCK" not in text:
        fail("MARKET anti-omission lock missing")

btc_source = ROOT / "master_prompts/master_btc_trend_v2_6_current.md"
if btc_source.exists():
    text = btc_source.read_text(encoding="utf-8")
    btc_signatures = [
        "MASTER BTC TREND V2.6",
        "6-OBJECTIVE · 3-SCREEN",
        "ExternalMasterDependency=NONE",
        "BASIC OFFICIAL KST 09:10 / 17:10 / 21:10",
        "NO hourly WATCH",
        "Coverage<70이면 strong confirmation 금지",
        "LIVE LONG PLAN 보존: BTC-SWING-20260902-03",
        "R:R>=3",
        "BTC FRACTAL V2.6 READ ONLY",
        "LONG A+ PROSPECTIVE",
        "S/R strength engine=BTC_SR_STRENGTH_V0_2",
        "OUTPUT은 BASIC/Precision 모두 ONLY 3 SCREENS",
        "TODAY LONG/SHORT ABSOLUTE",
        "FINAL LOCK"
    ]
    for signature in btc_signatures:
        if signature not in text:
            fail(f"BTC TREND V2.6 canonical signature missing: {signature}")

btc_contract_path = ROOT / "state/master_btc_trend_v2_6_contract.json"
btc_contract = load_json(btc_contract_path) if btc_contract_path.exists() else {}
if btc_contract:
    if btc_contract.get("production_version") != "V2.6":
        fail("BTC TREND contract production_version mismatch")
    if btc_contract.get("research_version") != "V3.0":
        fail("BTC TREND contract research_version mismatch")
    pr = btc_contract.get("production_research_separation", {})
    if pr.get("v3_research_only") is not True:
        fail("BTC V3.0 must remain research-only")
    if pr.get("silent_promotion_forbidden") is not True:
        fail("BTC V3.0 silent promotion must remain forbidden")
    if pr.get("promotion_requires_acceptance") is not True:
        fail("BTC V3.0 promotion must require acceptance")
    schedule = btc_contract.get("schedule", {})
    if schedule.get("official_kst") != ["09:10", "17:10", "21:10"]:
        fail("BTC V2.6 official schedule mismatch")
    if any(schedule.get(k) is not False for k in ["hourly_watch", "four_hour_watch", "plan_watch", "background_polling"]):
        fail("BTC V2.6 WATCH/background polling must remain off")
    execution = btc_contract.get("execution", {})
    if execution.get("minimum_rr") != 3.0:
        fail("BTC V2.6 minimum R:R must remain 3.0")
    if execution.get("non_chasing_required") is not True:
        fail("BTC V2.6 Non-Chasing gate missing")
    if execution.get("structural_sl_required") is not True:
        fail("BTC V2.6 structural SL gate missing")
    if execution.get("severe_risk_veto_blocks") is not True:
        fail("BTC V2.6 Severe Risk Veto must block")
    fractal = btc_contract.get("fractal", {})
    if fractal.get("mode") != "READ_ONLY_AUXILIARY":
        fail("BTC fractal must remain read-only auxiliary")
    if fractal.get("top_level_score_weight") != 0 or fractal.get("entry_gate_weight") != 0:
        fail("BTC fractal must not alter top-level score or entry gate")
    sr = btc_contract.get("sr_strength", {})
    if sr.get("score_is_future_probability") is not False:
        fail("BTC S/R strength must not be represented as future probability")
    output = btc_contract.get("output", {})
    if output.get("semantic_screens") != 3:
        fail("BTC V2.6 must preserve current 3 semantic screens")
    if output.get("later_user_refinement_allowed") is not True:
        fail("BTC UI later user refinement must remain allowed")

alt_top100_source = ROOT / "master_prompts/master_alt_top100_v4_8_current.md"
if alt_top100_source.exists():
    text = alt_top100_source.read_text(encoding="utf-8")
    required_signatures = [
        "MASTER ALT V4.8 REAL-DATA CORE FINAL",
        "TOP100 DISCOVERY",
        "INDEPENDENCE HARD LOCK",
        "DAILY OFFICIAL — EXACT 4 SCREEN",
        "ENTER HARD GATE"
    ]
    for signature in required_signatures:
        if signature not in text:
            fail(f"ALT TOP100 V4.8 canonical signature missing: {signature}")

alt_final20_source = ROOT / "master_prompts/master_alt_final20_current.md"
if alt_final20_source.exists():
    text = alt_final20_source.read_text(encoding="utf-8")
    if "MASTER ALT V2.2.1" not in text:
        fail("ALT FINAL20 stored-source signature changed")

trading_source = ROOT / "master_prompts/master_trading_current.md"
if trading_source.exists():
    text = trading_source.read_text(encoding="utf-8")
    trading_signatures = [
        "MASTER TRADING — CURRENT + TIME VALIDITY V2.1 OVERLAY",
        "2-STAGE ENTRY",
        "Trigger PASS != market-price ADD",
        "TIME VALIDITY V2.1 — NON-DESTRUCTIVE OVERLAY",
        "No universal fixed `4H / 8H / 12H` setup TTL",
        "Wave Energy is context-only",
        "Fibonacci Time = OFF",
        "MASTER TRADING recurring automation is OFF",
        "PRIVACY — PUBLIC REPOSITORY HARD LOCK",
        "FINAL EXECUTION GATE"
    ]
    for signature in trading_signatures:
        if signature not in text:
            fail(f"MASTER TRADING canonical signature missing: {signature}")

trading_contract_path = ROOT / "state/master_trading_current_contract.json"
trading_contract = load_json(trading_contract_path) if trading_contract_path.exists() else {}
if trading_contract:
    if trading_contract.get("version") != "CURRENT + TIME VALIDITY V2.1 OVERLAY":
        fail("MASTER TRADING contract version mismatch")
    if trading_contract.get("execution_mode") != "MANUAL_ONLY":
        fail("MASTER TRADING must remain manual-only until explicit user approval")
    if trading_contract.get("automation_enabled") is not False:
        fail("MASTER TRADING recurring automation must remain off")
    entry = trading_contract.get("entry_model", {})
    if entry.get("minimum_rr") != 3.0:
        fail("MASTER TRADING minimum R:R must remain 3.0")
    if entry.get("trigger_pass_is_market_add") is not False:
        fail("MASTER TRADING Trigger PASS must not equal immediate market ADD")
    if entry.get("add_requires_retest") is not True:
        fail("MASTER TRADING ADD must require retest")
    tv = trading_contract.get("time_validity_v2_1", {})
    if tv.get("non_destructive_overlay") is not True:
        fail("TIME VALIDITY V2.1 must remain non-destructive")
    if tv.get("fixed_universal_ttl") is not False:
        fail("TIME VALIDITY V2.1 must not impose a universal fixed TTL")
    if tv.get("price_invalidation_separate_from_time_weakness") is not True:
        fail("price invalidation must remain separate from time weakness")
    if tv.get("wave_energy") != "CONTEXT_ONLY":
        fail("Wave Energy must remain context-only")
    if tv.get("fibonacci_time") != "OFF":
        fail("Fibonacci Time must remain OFF")
    privacy = trading_contract.get("privacy", {})
    if privacy.get("public_repo_sensitive_state_forbidden") is not True:
        fail("MASTER TRADING public-repo privacy guard missing")

# Privacy safety: public repository must not claim to store personal trading state.
trading = masters.get("trading", {})
if trading.get("privacy_policy") != "PERSONAL_POSITION_BALANCE_AND_ACCOUNT_DATA_MUST_NOT_BE_STORED_IN_PUBLIC_REPOSITORY":
    fail("trading privacy policy missing")

if errors:
    print("MONEY MASTER OS V2 VALIDATION: FAIL")
    for e in errors:
        print(f"- ERROR: {e}")
    sys.exit(1)

print("MONEY MASTER OS V2 VALIDATION: PASS")
print("- Exactly five independent MASTER identities are registered and READY.")
print("- ALT TOP100 and ALT FINAL20 are separated.")
print("- BTC TREND V2.6 production canonical is locked and V3.0 remains research-only.")
print("- READY masters require exact canonical sources and declared contracts.")
print("- MASTER TRADING execution, TIME VALIDITY V2.1, manual-only and privacy invariants are locked.")
for n in notes:
    print(f"- {n}")
print("- Shared facts may be reused, but MASTER conclusions remain independent.")
