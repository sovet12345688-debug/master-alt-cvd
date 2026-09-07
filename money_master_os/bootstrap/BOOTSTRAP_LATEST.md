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
5. Verify `status`, `expected_version`, `repo_version`, `source_path/canonical_source`, and `bootstrap_allowed`.
6. If status is `VERSION_DRIFT` or `SOURCE_MISSING`, STOP automatic restoration. Report the block; never reconstruct from memory.
7. If status is `READY`, read the exact canonical source and machine contract/state pointers.
8. Read the latest valid handoff/state snapshot if one exists. Missing historical state must remain missing; do not backfill from conversation memory.
9. Verify MONEY MASTER OS validation status.
10. Before execution, print a compact bootstrap receipt: MASTER ID / display name / expected version / repository version / source path / source SHA or commit when available / state status / validation PASS or BLOCKED.

## Authority order for migration identity
`Registry > MASTER Manifest > Canonical Source/Contract > Persisted State/Handoff > Chat context`

Chat context may add current user instructions but must not silently replace the canonical identity or invent missing persisted history.

## Current safe bootstrap state — 2026-09-07 11:00 KST
- `market` — MASTER MARKET V1.2 FINAL: READY.
- `btc_trend` — production V2.6: BLOCKED by SOURCE_MISSING. V3.0 is research-only.
- `alt_top100` — MASTER ALT 1 V4.8 FINAL: READY. Load `master_prompts/master_alt_top100_v4_8_current.md` exactly.
- `alt_final20` — MASTER ALT 2 V2.2.1 FINAL20 DEEP FINAL: READY.
- `trading` — MASTER TRADING current + TIME VALIDITY V2.1 overlay: BLOCKED by SOURCE_MISSING.

## Cross-MASTER rule
Shared facts may be read from common GitHub data layers, but no MASTER may bootstrap from another MASTER's score, direction, permission, READY state, or final conclusion.

## UI refinement rule
A READY MASTER may later receive user-approved output/UI refinement. Such a UI change must be versioned explicitly and must not silently rebuild or alter analytical logic, score formulas, data policy, risk gates, or historical state.

## Migration command examples
- `새 방이야. MASTER MARKET 복원.`
- `새 방이야. MASTER ALT 1 TOP100 복원.`
- `새 방이야. MASTER ALT 2 FINAL20 복원.`

If the selected MASTER is blocked, the assistant must explain which exact canonical source/contract is missing instead of recreating it from memory.
