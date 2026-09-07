# MONEY MASTER OS V2 — Source of Truth Layer

Purpose: make GitHub the durable source-of-truth for five independent MASTER systems while keeping ChatGPT rooms as execution/analysis surfaces.

## Five MASTER identities — Phase 1 lock
1. `market` — MASTER MARKET
2. `btc_trend` — MASTER BTC TREND
3. `alt_top100` — MASTER ALT 1 · TOP100 discovery
4. `alt_final20` — MASTER ALT 2 · FINAL20 deep tracking
5. `trading` — MASTER TRADING

The legacy ambiguous registry key `alt` is no longer valid for V2 bootstrap.

## Core architecture rule
- Shared market facts may be reused through GitHub data layers.
- MASTER conclusions remain independent.
- No MASTER may require another MASTER's score, direction, READY state, permission, or conclusion as a blocking dependency.
- A stale or failed shared fact source degrades only the dependent data axis; it must not silently propagate another MASTER's conclusion.

## Operating rule
- GitHub stores canonical version identity, source pointers, state/handoff contracts, bootstrap rules, and validation.
- Chat rooms are execution/analysis surfaces, not the canonical archive.
- Existing collectors, market data, histories, and current prompt files remain untouched unless explicitly changed.
- A MASTER with `VERSION_DRIFT` or `SOURCE_MISSING` is blocked from automatic bootstrap until its exact approved canonical source is committed.

## Current status — 2026-09-07 11:34 KST
- MASTER MARKET: `READY` — repository canonical source matches V1.2 FINAL.
- MASTER BTC TREND: `SOURCE_MISSING` — production identity is V2.6; V3.0 remains research-only. Exact V2.6 production canonical source is not yet committed.
- MASTER ALT 1 · TOP100: `READY` — canonical source `master_prompts/master_alt_top100_v4_8_current.md`.
- MASTER ALT 2 · FINAL20: `READY` — repository canonical source matches V2.2.1 FINAL20 DEEP FINAL.
- MASTER TRADING: `READY` — canonical source `master_prompts/master_trading_current.md` with machine contract `state/master_trading_current_contract.json` and non-destructive TIME VALIDITY V2.1 overlay.

## MASTER TRADING canonicalization
- The prior operating rules were distributed across user-approved MASTER TRADING decisions rather than one active automation prompt.
- The new canonical explicitly consolidates only those approved operating rules plus the validated GitHub TIME VALIDITY research layer; it does not claim that one identical historical prompt previously existed.
- MASTER TRADING remains manual-only. No recurring automation is created.
- Current 4 semantic sections are preserved, but later user-approved UI refinement is allowed without rebuilding analytical logic.
- Personal balance, private position size, account identifiers and private execution details must not be stored in this public repository.

## ALT TOP100 V4.8 source provenance
- Source origin: active automation `MASTER ALT V4.8 [TOP100 · INDEPENDENT · MAX REASONING · FINAL]`.
- Canonical path: `master_prompts/master_alt_top100_v4_8_current.md`.
- The current visible layout is preserved for continuity, but later user-approved UI refinement is allowed without requiring the analytical logic to be rebuilt.
- Future automated execution should load the canonical source rather than reconstructing the prompt from chat memory.

## BTC production/research separation
- Production: MASTER BTC TREND V2.6.
- Research: MASTER BTC TREND V3.0.
- V3.0 must never be silently promoted to production.
- Promotion requires explicit acceptance completion plus exact canonical source/contract commit and registry update.

## Public repository privacy invariant
This repository is public. Market data and public research artifacts may be stored here, but personal trading/account state must not be committed, including account balances, account identifiers, private position sizes, private execution details, or API credentials.

## Layout
- `registry/MASTER_REGISTRY.json`: V2 machine-readable five-MASTER registry.
- `registry/MASTER_REGISTRY_V1_LEGACY.json`: preserved pre-V2 registry backup.
- `shared/`: common non-negotiable policies.
- `masters/*/manifest.json`: per-MASTER identity, source, state and bootstrap contract.
- `bootstrap/BOOTSTRAP_LATEST.md`: room-migration loader contract.
- `handoff/HANDOFF_TEMPLATE.json`: handoff schema; V2 per-MASTER handoff normalization is a later phase.
- `tools/validate_master_os.py`: five-MASTER structural/anti-drift validator.
- `.github/workflows/money_master_os_guard.yml`: automatic validation workflow.

## Safety invariant
Never silently reconstruct a missing MASTER from chat memory and call it canonical. When a historical single-file canonical does not exist, canonicalization must explicitly identify the approved component sources and must not claim an identical historical prompt existed.
