# MONEY MASTER OS V2 — NEW ROOM BOOTSTRAP

Use this contract whenever one of the five MASTER systems is moved to a new ChatGPT room.

## Valid MASTER IDs
- `market`
- `btc_trend`
- `alt_top100`
- `alt_final20`
- `trading`

Legacy ambiguous ID `alt` is forbidden for V2 bootstrap.

## Required load order
1. Read `money_master_os/registry/MASTER_REGISTRY.json`.
2. Select exactly one requested MASTER entry from the five valid IDs.
3. Read its `manifest_path`.
4. Read all listed `shared_dependencies`.
5. Verify `status`, `expected_version`, `repo_version`, `source_path/canonical_source`, `contract_path/machine_contract`, and `bootstrap_allowed`.
6. If status is `VERSION_DRIFT` or `SOURCE_MISSING`, STOP automatic restoration. Report the block; never reconstruct from memory.
7. If status is `READY`, read the exact canonical source and machine contract/state pointers.
8. If `source_health/output/latest.json` exists, read it as a non-blocking data-availability diagnostic. Do not interpret `registered_source_health_pct` as MASTER Coverage, confidence, direction, Permission, or an execution gate. If Source Health is missing, continue bootstrap and report diagnostic N/A.
9. Read the latest valid handoff/state snapshot if one exists. Missing historical state must remain missing; do not backfill from conversation memory.
10. Verify MONEY MASTER OS validation status.
11. Before execution, print a compact bootstrap receipt: MASTER ID / display name / expected version / repository version / source path / source SHA or commit when available / contract path / Source Health status when available / state status / validation PASS or BLOCKED.

## Authority order for migration identity
`Registry > MASTER Manifest > Canonical Source/Contract > Persisted State/Handoff > Chat context`

Source Health is operational diagnostics, not identity authority and not a MASTER conclusion.

Chat context may add current user instructions but must not silently replace the canonical identity or invent missing persisted history.

## Current safe bootstrap state — 2026-09-07 12:14 KST — ALL 5 READY
- `market` — MASTER MARKET V1.2 FINAL: READY. Load `master_prompts/master_market_v1_2_current.md` and `state/master_market_v1_2_contract.json`.
- `btc_trend` — MASTER BTC TREND V2.6 PRODUCTION: READY. Load `master_prompts/master_btc_trend_v2_6_current.md` and `state/master_btc_trend_v2_6_contract.json`. V3.0 remains research-only.
- `alt_top100` — MASTER ALT 1 V4.8 FINAL: READY. Load `master_prompts/master_alt_top100_v4_8_current.md` exactly.
- `alt_final20` — MASTER ALT 2 V2.2.1 FINAL20 DEEP FINAL: READY. Load `master_prompts/master_alt_final20_current.md` exactly.
- `trading` — MASTER TRADING CURRENT + TIME VALIDITY V2.1 OVERLAY: READY. Load `master_prompts/master_trading_current.md` and `state/master_trading_current_contract.json`. Execution mode is manual-only.

## Source Health rule
- Latest central diagnostic path: `source_health/output/latest.json`.
- `HEALTHY / DEGRADED / STALE / FAILED / MISSING / UNKNOWN` describes source availability only.
- `registered_source_health_pct` summarizes only currently registered shared/repository sources. It is NOT the MASTER's own Coverage.
- Optional/Context source failure does not automatically block a MASTER.
- `last_good` is diagnostic only and must never be silently treated as a current fact.
- MASTER-specific freshness and execution gates still control actual analysis/trading decisions.

## BTC production/research rule
- New-room production bootstrap always loads V2.6 while the registry says production=`V2.6`.
- V3.0 research assets must not override, patch, or replace V2.6 production automatically.
- V3.0 promotion requires explicit acceptance completion, exact V3.0 canonical source/contract, and an explicit registry promotion.
- Historical/bootstrap values inside the V2.6 prompt are state history only; revalidate against current market data before treating them as current.

## Cross-MASTER rule
Shared facts may be read from common GitHub data layers, but no MASTER may bootstrap from another MASTER's score, direction, permission, READY state, or final conclusion.

## MASTER TRADING privacy rule
The public repo may contain market/public research and anonymized system state only. Never write personal balance, private position size, account identifiers, private execution details or API credentials to this public repository.

## UI refinement rule
A READY MASTER may later receive user-approved output/UI refinement. Such a UI change must be versioned explicitly and must not silently rebuild or alter analytical logic, score formulas, data policy, risk gates, or historical state.

## Migration command examples
- `새 방이야. MASTER MARKET 복원.`
- `새 방이야. MASTER BTC TREND 복원.`
- `새 방이야. MASTER ALT 1 TOP100 복원.`
- `새 방이야. MASTER ALT 2 FINAL20 복원.`
- `새 방이야. MASTER TRADING 복원.`

If a selected MASTER becomes blocked later, the assistant must explain which exact canonical source/contract is missing instead of recreating it from memory.
