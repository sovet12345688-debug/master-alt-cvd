# MONEY OS WORK V3 — 6-SYSTEM ABSOLUTE ISOLATION

Status: SIX WORKER SHADOW PACKAGES BUILT AND VALIDATED / PRODUCTION CUTOVER HOLD
Created: 2026-09-14 KST
Target runtime: ChatGPT Work
Production cutover: NOT APPROVED / NOT ACTIVE

## 1. PURPOSE
MONEY OS WORK V3 is a top-level operating shell for six existing MONEY systems:

1. `alt_top100` — ALT 1
2. `alt_final20` — ALT 2
3. `market` — MASTER MARKET
4. `btc_trend` — MASTER BTC TREND
5. `youtuber_view` — YOUTUBER VIEW INTELLIGENCE & FORECAST TRACKER V2
6. `trading` — MASTER TRADING

The top-level MONEY OS is **control-plane only**. It may route a request to one system and report whether that system finished, but it must never become a shared market-data, shared-score, shared-history, shared-state, shared-source-health, or shared-conclusion layer.

## 2. ABSOLUTE ISOLATION — HIGHEST PRIORITY
Each of the six systems is an independent data plane.

Hard rules:
- Cross-system read = FORBIDDEN.
- Cross-system write = FORBIDDEN.
- Shared market-fact vault = FORBIDDEN.
- Shared official-state store = FORBIDDEN.
- Shared history = FORBIDDEN.
- Shared WATCH event store = FORBIDDEN.
- Shared Source Health = FORBIDDEN.
- Shared score/permission/direction/READY/RUN_ID = FORBIDDEN.
- One system failure/staleness/N/A must have zero effect on another system.
- The same public source may be independently queried by multiple systems, but one system may not reuse another system's stored result.

The parent MONEY OS registry may contain only identity, routing, schedule, canonical-source pointers, and system-local path declarations. It may not contain market values or analytical outputs.

## 3. DURABLE SOURCE OF TRUTH
Chat/Work conversation memory is not the canonical archive.

For every system:
- analytical/output rules come from its frozen canonical source;
- current state, source health, official history, WATCH history, snapshots, and derived artifacts live only inside that system's own namespace;
- no historical value is reconstructed when it was not actually stored;
- N/A is never converted to zero or silently backfilled;
- a persistence failure does not authorize invented history.

During shadow validation, V3 control/policy files come from the shadow branch, while the five existing production canonicals are resolved from the current `main` head before each run. This prevents scheduled data updates on `main` from making the shadow worker stale. `youtuber_view` is the only exception because it had no prior durable main canonical; its V3 canonical must pass a manual parity run before cutover.

## 4. UI FREEZE
The six existing user-visible output/UI contracts are immutable during this migration.

Migration may change only the backend operating/persistence path. It must not change:
- screen count or order;
- table/section order;
- labels;
- score formulas;
- thresholds;
- trigger rules;
- footer/follow-up rules;
- visible N/A policy defined by that system;
- existing manual/automatic execution semantics.

Any future UI change requires a separate explicit user instruction.

## 5. WORK OPERATING MODEL
Recommended user surface inside the single MONEY project:
- one top-level `MONEY OS · CONTROL` Work conversation for routing/status only;
- six persistent Work conversations/tasks, one per system;
- scheduled Work tasks only for systems that are already scheduled;
- manual Work conversations for `youtuber_view` and `trading`;
- BTC precision, YouTuber viewpoint analysis, and Trading execution analysis continue to accept user-uploaded chart images manually.

The six Work conversations are independent workers, not sub-agents that share conclusions.

## 6. CURRENT SCHEDULE PRESERVATION
Migration does not alter current operating cadence:
- MARKET: hourly `HH:00`; OFFICIAL `01:00/05:00/09:00/13:00/17:00/21:00 KST`, otherwise WATCH.
- BTC TREND: OFFICIAL `05:00/08:00/13:00/17:00/21:00 KST`; no hourly WATCH.
- ALT 1 TOP100: hourly `HH:30`; DAILY OFFICIAL `10:30 KST`, otherwise WATCH.
- ALT 2 FINAL20: hourly `HH:45`; OFFICIAL `01:45/05:45/09:45/13:45/17:45/21:45 KST`, otherwise WATCH.
- YouTuber View: manual/event-driven.
- TRADING: manual only.

## 7. LEGACY V2 BOUNDARY
The existing `money_master_os/`, `shared_fact_vault/`, global `source_health/`, global `official_state/`, and global `watch_events/` remain untouched during shadow migration.

MONEY OS WORK V3 must not consume those shared/global stores after cutover. Existing production remains active until final user approval.

## 8. STORAGE NAMESPACE CONTRACT
Each system owns exactly one root:
`money_os_work_v3/systems/<system_id>/runtime/`

Logical subpaths:
- `source_health/`
- `latest/`
- `history/official/`
- `history/watch/`
- `snapshots/`
- `inputs/`
- `artifacts/`
- `outcome/` when that system already tracks prospective outcomes

A system may read only its own namespace plus its own canonical/contract files and direct external public sources.

## 9. SOURCE PARITY AND WORKER SHADOWS
Full six-chat verification is recorded in `audit/SOURCE_CHAT_PARITY_20260914.md` and its machine-readable JSON. Every source was inspected in full against the ten mandatory axes.

Each system now has:
- a system-local `WORKER_SHADOW_PROMPT.md`;
- a system-local `runtime/RUNTIME_CONTRACT.json`;
- manifest-pinned canonical, prompt, runtime-contract and source-chat evidence;
- fail-closed bootstrap validation and temporary local-persistence smoke coverage.

ALT1, ALT2, BTC, YouTuber and TRADING pass. MARKET passes with the reversible Shadow candidate but remains blocked against current `main` because its latest approved five easy-Korean SCREEN4 labels are not yet merged. No formula, threshold, source identifier, or score weight changed.

Validation commands:
`python money_os_work_v3/tools/validate_v3_isolation.py`
`python money_os_work_v3/tools/validate_worker_shadows.py`
`python -m unittest discover -s money_os_work_v3/tests -v`

## 10. CUTOVER SAFETY
This V3 branch is additive and reversible. It does not disable current automations, activate Work schedules, or modify `main`.
Final cutover requires explicit user approval only after shadow validation confirms:
- all six systems load the correct canonical;
- UI is unchanged;
- history writes are system-local;
- no cross-system read/write remains;
- scheduled/manual behavior matches current operation;
- real N/A remains honest while persistence/context-caused N/A is reduced.
