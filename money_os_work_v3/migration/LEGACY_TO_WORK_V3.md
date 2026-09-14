# LEGACY → MONEY OS WORK V3 MIGRATION

Mode: NON-DESTRUCTIVE SHADOW MIGRATION

Execution snapshot (2026-09-14): Phases 1–3 are complete at the Worker-package/contract-smoke level. Six system-local prompts and runtime contracts pass, all six SOURCE CHAT parity audits are complete, and the seven surface specs remain inactive. No live schedule, notification, Production write, legacy disablement, merge, or cutover has occurred. MARKET remains current-main blocked pending approval of its label-only Shadow candidate.

## Phase 0 — completed in this branch
- Preserve existing production main branch.
- Preserve current Chat automations.
- Preserve current six user-visible output/UI contracts.
- Create six-system V3 registry with zero cross-system runtime sharing.
- Create Work operating contract.
- Canonicalize YouTuber scorecard/persistence rules without redesigning its UI.

## Phase 1 — system-local runtime foundations
For each system separately, create its own local runtime folders and producer/validator path:
- source_health
- latest
- history/official
- history/watch when applicable
- snapshots
- inputs when applicable
- artifacts
- outcome when applicable

No writer may target another system's path.
No validator may require another system to be healthy.

## Phase 2 — detach legacy shared runtime dependencies
Perform one system at a time in shadow mode.

### MARKET
Replace runtime use of global source-health/shared fact/official-state/watch stores with MARKET-local equivalents. Keep exact 5-screen UI and N/A block behavior.

### ALT 1
Keep exact TOP100 canonical and 4-screen UI. Persist only ALT1 official/watch history and snapshots locally.

### ALT 2
Move global development-status/outcome shadow plumbing into ALT2-local runtime. Keep its current visible 4-screen output and approved outcome semantics unchanged.

### BTC TREND
Preserve V2.6 production and V3.0 research separation. Move official production persistence/bridge state into BTC-local runtime while retaining existing external research lineage only where the BTC canonical itself owns that evidence. No other MONEY system output may be read.

### YouTuber View
Start prospective durable logging of new Forecast IDs and later outcomes. Do not fabricate older forecasts that were not already recorded. Preserve current room UI and manual chart-input behavior.

### TRADING
Remove runtime reliance on shared MONEY facts/invariants by copying required invariant text into TRADING-owned contract or directly revalidating sources. Remain MANUAL ONLY. Preserve current 4 semantic sections.

## Phase 3 — Work shadow runs
Run Work side-by-side with existing Chat production without user-visible duplicate notifications.
Validate for each system independently:
- canonical/version match;
- exact visible structure match;
- current-data freshness;
- correct OFFICIAL/WATCH/manual mode;
- local persistence succeeded;
- history delta uses actual stored prior runs only;
- no cross-system path read/write;
- no invented values;
- same analytical verdict under equivalent input, allowing only legitimate fresh-data timing differences.

A failure in one system does not block shadow validation of another.

## Phase 4 — final cutover approval
Only after user approval:
1. Enable/activate the corresponding Work scheduled tasks.
2. Confirm at least one real successful Work run per scheduled system and one manual run for YouTuber/TRADING.
3. Disable the replaced legacy Chat automation only after its Work replacement is confirmed healthy.
4. Do not delete legacy history or code.
5. Mark legacy runtime READ-ONLY/ARCHIVED only after stable cutover.

## Rollback
Rollback is simple because no legacy production file is deleted:
- disable the Work task;
- re-enable the prior Chat automation if needed;
- retain V3 branch/runtime for diagnosis;
- do not copy failed V3 state into legacy history.

## Acceptance gates
Cutover is blocked unless all are true for the target system:
- `CANONICAL_MATCH=PASS`
- `UI_PARITY=PASS`
- `LOCAL_PERSISTENCE=PASS`
- `CROSS_SYSTEM_READS=0`
- `CROSS_SYSTEM_WRITES=0`
- `HISTORY_RECONSTRUCTION=0`
- `N/A_TO_ZERO=0`
- `SCHEDULE_OR_MANUAL_MODE_MATCH=PASS`

Global all-six PASS is required only for declaring the overall MONEY OS migration complete. It is not a runtime dependency between systems.
