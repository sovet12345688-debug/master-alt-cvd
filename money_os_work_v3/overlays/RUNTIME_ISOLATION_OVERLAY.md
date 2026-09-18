# MONEY OS WORK V3 — RUNTIME ISOLATION OVERLAY

Priority: migration/backend overlay only.
User-visible UI/analysis formulas: NO CHANGE.
Approved purpose: satisfy the user's 2026-09-14 requirement that all six systems operate with zero cross-system data/state dependencies under one MONEY OS.

This overlay modifies only runtime data access, persistence location, bootstrap and health-check wiring. It must never alter a system's analytical meaning, score formula, gate, schedule, or visible output contract.

## MARKET
Preserve `master_prompts/master_market_v1_2_current.md` analytical and 5-screen contract exactly.
Backend override after cutover:
- global `source_health/`, `shared_fact_vault/`, `official_state/`, `watch_events/` are not runtime inputs;
- collect/validate MARKET data directly or from MARKET-owned collectors/artifacts;
- write all state/history/health/watch only under `money_os_work_v3/systems/market/runtime/`.

## ALT 1 TOP100
Preserve `master_prompts/master_alt_top100_v4_8_current.md` exactly.
It is already analytically independent. V3 only relocates all durable run/history/source-health storage to `systems/alt_top100/runtime/`.

## ALT 2 FINAL20
Preserve `master_prompts/master_alt_final20_current.md` analysis, CVD rules, score rules, WATCH/OFFICIAL semantics and visible UI exactly.
Backend override after cutover:
- any global GitHub data-ready/development-status or shared outcome/state store is replaced by ALT2-owned equivalent under `systems/alt_final20/runtime/`;
- existing visible block content, if required by canonical, is populated from ALT2-local status only and is not allowed to read another MONEY system.

## BTC TREND
Preserve V2.6 production logic, V3.0 research separation, 3-screen BASIC output, precision-report UI and schedule exactly.
Backend override after cutover:
- replace global MONEY `official_state` inbox/bridge/index/history with BTC-owned equivalents under `systems/btc_trend/runtime/`;
- do not use the global MONEY registry as live analytical state;
- BTC-owned research lineage/artifacts explicitly approved by BTC's own canonical may remain BTC dependencies, because they are not outputs of another MONEY system;
- no MARKET/ALT/TRADING/YouTuber data may be read.

## YOUTUBER VIEW
Use `systems/youtuber_view/CANONICAL_RULES.md` for the V2 intelligence/forecast wrapper and retained SCORECARD V1 formulas while keeping current room-visible UI frozen.
All forecast/outcome/input records remain under `systems/youtuber_view/runtime/`.
No other MONEY system may be read for counter-analysis; current counterevidence must be collected directly inside this system.

## TRADING
Preserve `master_prompts/master_trading_current.md` analytical gates, its 4 semantic sections, and the exact 6-screen presentation in `money_master_os/masters/trading/MASTER-TRADING-UI-V2-FINAL.md`.
Backend isolation override supersedes only its older shared-fact permission:
- another MASTER's stored public fact is not reusable;
- all Current/Entry/Trigger/SL/TP/R:R and optional context must be independently revalidated by TRADING from direct sources or TRADING-owned artifacts;
- shared `money_master_os/shared/*` files are not runtime dependencies after cutover; required invariant text is carried by the TRADING canonical/contract plus this isolation overlay;
- write only under `systems/trading/runtime/`.

## Precedence
For analytical/UI behavior: each system's existing canonical wins.
For runtime storage/access isolation: this V3 overlay wins after final Work cutover.
If these conflict in a way that would change a user-visible analytical rule, fail closed and require explicit user approval rather than silently changing behavior.
