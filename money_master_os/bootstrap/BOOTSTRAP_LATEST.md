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
5. Verify `status`, `expected_version`, `repo_version`, `source_path/canonical_source`, `contract_path/machine_contract`, `official_state_path`, and `bootstrap_allowed`.
6. If status is `VERSION_DRIFT` or `SOURCE_MISSING`, STOP automatic restoration. Report the block; never reconstruct from memory.
7. If status is `READY`, read the exact canonical source and machine contract/state pointers.
8. If `source_health/output/latest.json` exists, read it as a non-blocking data-availability diagnostic. Do not interpret `registered_source_health_pct` as MASTER Coverage, confidence, direction, Permission, or an execution gate. If Source Health is missing, continue bootstrap and report diagnostic N/A.
9. Read `official_state/latest/index.json`, then read the selected MASTER's exact `official_state_path`.
10. If OFFICIAL State=`STORED`, treat it as the latest persisted OFFICIAL decision snapshot. Check `validity.freshness_status` and `valid_until_kst`. If freshness is `UNKNOWN` or `EXPIRED`, use the state as historical official context only and revalidate current market facts before treating it as current.
11. If OFFICIAL State=`NO_STORED_OFFICIAL_RUN`, explicitly report `저장된 공식 실행 없음`. Do not reconstruct direction, score, candidates, Entry/SL/TP/R:R, Coverage, confidence or history from chat memory, raw collectors, research files or legacy handoffs.
12. Legacy files under `money_master_os/handoff/*_LATEST.json` are migration artifacts only. They may help explain old migration lineage but must never override `official_state/latest/*` or be promoted to an OFFICIAL decision.
13. Verify MONEY MASTER OS validation status.
14. Before execution, print a compact bootstrap receipt: MASTER ID / display name / expected version / repository version / source path / source SHA or commit when available / contract path / Source Health status when available / OFFICIAL State status / latest OFFICIAL run id / OFFICIAL freshness / validation PASS or BLOCKED.

## Authority order
`Registry > MASTER Manifest/Canonical Source > OFFICIAL State > Source Health diagnostic > legacy handoff > Chat context`

Source Health is operational diagnostics, not identity authority and not a MASTER conclusion.
Legacy handoff is migration context only once OFFICIAL State V1 exists.
Chat context may add current user instructions but must not silently replace canonical identity or invent missing persisted history.

## Current safe bootstrap state — 2026-09-07 P1 OFFICIAL STATE
- `market` — MASTER MARKET V1.2 FINAL: READY. Canonical source/contract valid. Latest persisted OFFICIAL State is stored at `official_state/latest/market.json` from an actually persisted MARKET OFFICIAL history row.
- `btc_trend` — MASTER BTC TREND V2.6 PRODUCTION: READY. V3.0 remains research-only. OFFICIAL State starts `NO_STORED_OFFICIAL_RUN` until an actual V2.6 OFFICIAL result is published.
- `alt_top100` — MASTER ALT 1 V4.8 FINAL: READY. OFFICIAL State starts `NO_STORED_OFFICIAL_RUN` until an actual V4.8 OFFICIAL result is published.
- `alt_final20` — MASTER ALT 2 V2.2.1 FINAL20 DEEP FINAL: READY. Raw CVD/derivatives/live-flow files are data engines, not MASTER OFFICIAL conclusions. OFFICIAL State starts `NO_STORED_OFFICIAL_RUN` until an actual MASTER OFFICIAL result is published.
- `trading` — MASTER TRADING CURRENT + TIME VALIDITY V2.1 OVERLAY: READY and manual-only. OFFICIAL State starts `NO_STORED_OFFICIAL_RUN` until an actual TRADING OFFICIAL result is published. Public repository privacy rules remain mandatory.

## OFFICIAL State rule
- Index: `official_state/latest/index.json`.
- Per-MASTER latest: `official_state/latest/<master_id>.json`.
- `STORED` means an actual OFFICIAL run was persisted.
- `NO_STORED_OFFICIAL_RUN` means exactly that; it is not an error and must not be filled from memory.
- WATCH / provisional / draft / research-only output cannot become OFFICIAL State.
- `valid_until_kst` and freshness must come from the MASTER's actual OFFICIAL result or explicit validity rule. Never infer validity solely from the next schedule.
- Another MASTER's direction, score, permission or action cannot be copied as this MASTER's owned OFFICIAL decision.

## Source Health rule
- Latest central diagnostic path: `source_health/output/latest.json`.
- `HEALTHY / DEGRADED / STALE / FAILED / MISSING / UNKNOWN` describes source availability only.
- `registered_source_health_pct` summarizes only currently registered shared/repository sources. It is NOT the MASTER's own Coverage.
- Optional/Context source failure does not automatically block a MASTER.
- `last_good` is diagnostic only and must never be silently treated as a current fact.
- MASTER-specific freshness and execution gates still control actual analysis/trading decisions.

## BTC production/research rule
- New-room production bootstrap always loads V2.6 while the registry says production=`V2.6`.
- V3.0 research assets must not override, patch, replace, or populate V2.6 OFFICIAL State automatically.
- V3.0 promotion requires explicit acceptance completion, exact V3.0 canonical source/contract, and an explicit registry promotion.
- Historical/bootstrap values inside the V2.6 prompt are state history only; revalidate against current market data before treating them as current.

## Cross-MASTER rule
Shared facts may be read from common GitHub data layers, but no MASTER may bootstrap from another MASTER's score, direction, permission, READY state, or final conclusion.

## MASTER TRADING privacy rule
The public repo may contain market/public research and anonymized system state only. Never write personal balance, private position size, account identifiers, private execution details or API credentials to this public repository.

## UI refinement rule
A READY MASTER may later receive user-approved output/UI refinement. Such a UI change must be versioned explicitly and must not silently rebuild or alter analytical logic, score formulas, data policy, risk gates, OFFICIAL history, or persisted state.

## Migration command examples
- `새 방이야. MASTER MARKET 복원.`
- `새 방이야. MASTER BTC TREND 복원.`
- `새 방이야. MASTER ALT 1 TOP100 복원.`
- `새 방이야. MASTER ALT 2 FINAL20 복원.`
- `새 방이야. MASTER TRADING 복원.`

If a selected MASTER becomes blocked later, or has no stored OFFICIAL run, explain the exact missing state instead of recreating it from memory.
