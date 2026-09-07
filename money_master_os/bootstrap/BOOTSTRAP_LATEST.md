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
8. Read `source_health/output/latest.json` when available as data-availability metadata only. Do not interpret Source Health as MASTER Coverage, confidence, direction, Permission or an execution gate.
9. Read `shared_fact_vault/output/latest.json` when available. Consume only facts allowed for the selected MASTER and only with their exact source/venue/window/timestamp lineage. If `current_usable=false`, do not treat the fact as current. Never reconcile conflicting sources silently.
10. Shared Fact Vault is optional current fact context, not a conclusion layer. It must not replace the selected MASTER's own scoring, interpretation, direction, permission, Current/Trigger/SL/TP/R:R validation or risk gate.
11. Read `official_state/latest/index.json`, then read the selected MASTER's exact `official_state_path`.
12. If OFFICIAL State=`STORED`, treat it as the latest persisted OFFICIAL decision snapshot. Check `validity.freshness_status` and `valid_until_kst`. If freshness is `UNKNOWN` or `EXPIRED`, use the state as historical official context only and revalidate current market facts before treating it as current.
13. If OFFICIAL State=`NO_STORED_OFFICIAL_RUN`, explicitly report `저장된 공식 실행 없음`. Do not reconstruct direction, score, candidates, Entry/SL/TP/R:R, Coverage, confidence or history from chat memory, raw collectors, research files, Shared Fact Vault or legacy handoffs.
14. Legacy files under `money_master_os/handoff/*_LATEST.json` are migration artifacts only. They may help explain old migration lineage but must never override `official_state/latest/*` or be promoted to an OFFICIAL decision.
15. Verify MONEY MASTER OS validation status.
16. Before execution, print a compact bootstrap receipt: MASTER ID / display name / expected version / repository version / source path / source SHA or commit when available / contract path / Source Health status / Shared Fact Vault status + generated time + usable fact count for the selected MASTER when available / OFFICIAL State status / latest OFFICIAL run id / OFFICIAL freshness / validation PASS or BLOCKED.

## Identity authority order
`Registry > MASTER Manifest/Canonical Source > OFFICIAL State > legacy handoff > Chat context`

Source Health and Shared Fact Vault are data layers, not MASTER identity or conclusion authority.
Chat context may add current user instructions but must not silently replace canonical identity or invent missing persisted history.

## Shared Fact Vault rule
- Registry: `shared_fact_vault/registry.json`.
- Latest compact facts: `shared_fact_vault/output/latest.json`.
- Same semantic metric from different sources remains separate and source-qualified.
- `STALE / FAILED / MISSING` facts may remain for lineage but are `current_usable=false`.
- `last_good` is not copied in as current.
- Shared Fact Vault stores no LONG/SHORT, bullish/bearish interpretation, MASTER score, Permission, Action, Entry, SL, TP, R:R, ranking or ENTER.
- Vault fact-source coverage is not MASTER Coverage.
- Current execution-critical values still require responsible MASTER revalidation.

## Current safe bootstrap state — 2026-09-07 P1
- `market` — MASTER MARKET V1.2 FINAL: READY. Load `master_prompts/master_market_v1_2_current.md` + `state/master_market_v1_2_contract.json` + shared fact context allowed for MARKET + `official_state/latest/market.json`.
- `btc_trend` — MASTER BTC TREND V2.6 PRODUCTION: READY. Load `master_prompts/master_btc_trend_v2_6_current.md` + `state/master_btc_trend_v2_6_contract.json` + shared fact context allowed for BTC TREND + `official_state/latest/btc_trend.json`. V3.0 remains research-only.
- `alt_top100` — MASTER ALT 1 V4.8 FINAL: READY. Load `master_prompts/master_alt_top100_v4_8_current.md` + shared fact context allowed for ALT TOP100 + `official_state/latest/alt_top100.json`.
- `alt_final20` — MASTER ALT 2 V2.2.1 FINAL20 DEEP FINAL: READY. Load `master_prompts/master_alt_final20_current.md` + shared fact context allowed for ALT FINAL20 + `official_state/latest/alt_final20.json`. Raw CVD/derivatives/live-flow remain data engines, not MASTER conclusions.
- `trading` — MASTER TRADING CURRENT + TIME VALIDITY V2.1 OVERLAY: READY and manual-only. Load `master_prompts/master_trading_current.md` + `state/master_trading_current_contract.json` + shared fact context allowed for TRADING + `official_state/latest/trading.json`. Public repository privacy rules remain mandatory.

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
- `registered_source_health_pct` summarizes only currently registered sources. It is NOT the MASTER's own Coverage.
- Optional/Context source failure does not automatically block a MASTER.
- `last_good` is diagnostic only and must never be silently treated as a current fact.
- MASTER-specific freshness and execution gates still control actual analysis/trading decisions.

## BTC production/research rule
- New-room production bootstrap always loads V2.6 while the registry says production=`V2.6`.
- V3.0 research assets must not override, patch, replace, or populate V2.6 OFFICIAL State automatically.
- V3.0 promotion requires explicit acceptance completion, exact V3.0 canonical source/contract, and an explicit registry promotion.
- Historical/bootstrap values inside the V2.6 prompt are state history only; revalidate against current market data before treating them as current.

## Cross-MASTER rule
All five MASTERs may read approved common facts, but no MASTER may bootstrap from another MASTER's score, direction, permission, READY state, ranking, action or final conclusion.

## MASTER TRADING privacy rule
The public repo may contain market/public research and anonymized system state only. Never write personal balance, private position size, account identifiers, private execution details or API credentials to this public repository.

## UI refinement rule
A READY MASTER may later receive user-approved output/UI refinement. Such a UI change must be versioned explicitly and must not silently rebuild or alter analytical logic, score formulas, data policy, risk gates, OFFICIAL history, Shared Fact Vault facts, or persisted state.

## Migration command examples
- `새 방이야. MASTER MARKET 복원.`
- `새 방이야. MASTER BTC TREND 복원.`
- `새 방이야. MASTER ALT 1 TOP100 복원.`
- `새 방이야. MASTER ALT 2 FINAL20 복원.`
- `새 방이야. MASTER TRADING 복원.`

If a selected MASTER becomes blocked later, or has no stored OFFICIAL run, explain the exact missing state instead of recreating it from memory.
