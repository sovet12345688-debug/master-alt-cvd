# MONEY GITHUB DATA READY POLICY V2

Canonical policy for the single existing `GitHub Data READY` watcher. No new ChatGPT automation may be created for these goals.

## Absolute safety
- Inspect each Goal independently. No user message when no state change.
- READY/DEV95/DEV100 never auto-modifies or auto-connects any MASTER prompt, score, Gate, schedule, or live plan.
- Manual verification first. Integration requires explicit user approval and an actually verified read-only clause in the target MASTER.
- MASTER BTC TREND is never automatically read, modified, patched, rolled back, merged into, or integrated by this watcher, regardless of version.
- No backfill/reconstructed signals, no N/A=0, no cross-venue/source delta repair, no silent connection.
- A GitHub data failure affects only that GitHub data layer, never the MASTER itself.

## Persistent state
Registry: `state/github_data_ready_registry.json`.
Update registry only on a material state transition, not every hourly heartbeat.
Dashboard fields: `goal|engine|latest_compatible_version|completion_pct|status|final_qa|master_link|expected_value|next_action|regression`.
Maintain integration_history, regression_history, compatibility_events.

## Version-agnostic aliases
- `ALT_FINAL20_CURRENT`: latest compatible FINAL20 MASTER line; never confuse with TOP100 ALT.
- `MARKET_CURRENT`: latest compatible MASTER MARKET line.
- `BTC_TREND_CURRENT`: latest official BTC TREND line, but watcher may only alert about BTC GitHub data; no automatic BTC MASTER reads/writes.
- GitHub engines track semantic contract first: engine/schema/source/change_basis/outcome/leakage/acceptance architecture.
- Compatible patch/minor successors auto-follow silently if quality remains intact.
- Breaking semantic change => `BREAKING_CHANGE_REVIEW`, keep last compatible contract, alert once, no MASTER changes.

## Completion pipeline
- 0~89: DEVELOPMENT/ACCUMULATING.
- 90~94: most core conditions met, history/quality still accumulating.
- 95: DEV95_FINAL_QA.
- 100: DEV100_FINAL_QA_PASS.
Do not invent linear percentages.

Common FINAL QA for data collectors:
1. Freshness PASS.
2. Coverage PASS.
3. Actual 24H continuity/window completeness PASS when applicable.
4. Duplicate and abnormal missing PASS.
5. No recent repeated workflow/production failure.
6. Source/venue/change-basis lock PASS; no cross-source/venue delta.
7. No N/A=0, forbidden backfill/interpolation, or in-progress-window contamination.
8. Schema/required-field consistency PASS.
9. No structural anomaly such as row-count collapse or unexplained universe shrink.

DEV100 first alert exact first line: `✅ [데이터명] 개발완료 100% · 수동 검증 필요`.
BTC Fractal exact first line: `✅ BTC 프렉탈 [VERSION] 개발완료 100% · 수동 검증 필요`.
DEV100 alert ends exactly: `👉 지금 저에게 “수동 검증해줘”라고 말씀해 주세요. 검증 전에는 MASTER에 연결하지 않습니다.`
Compatible patch/minor that remains DEV100 does not re-alert. Regression recovery may alert once.

## Integration history
Only append actual state transitions: MANUAL_VERIFIED, APPROVED, INTEGRATED, INTEGRATION_NOT_NEEDED, RETIRED.
INTEGRATED requires `clause_verified=true`. Never infer user approval.

## Regression
After a Goal has reached DEV100, compare latest compatible candidate to last DEV100 contract. Freshness/coverage/continuity/duplicate/workflow/source/schema/N/A guard/statistical acceptance failure => REGRESSION.
Alert once: `⚠️ [데이터명] 품질 회귀 감지 · 최신 후보 보류`.
Do not auto-roll back or edit MASTER. Same continuing failure does not re-alert. Recovery + FINAL QA PASS may alert once.

# GOAL A — DERIVATIVES OI/FUNDING
Repo files: `derivatives/output/latest_summary.json`, `derivatives/state/collector_state.json`, derivatives history.
Contract: same-venue derivatives OI/Funding; missing=N/A; no venue mixing.
DEV95: freshness<=90m; BTC+ALT FINAL20 coverage>=90%; valid same-venue OI delta 1H/4H/24H; Funding exists; no repeated collection failure; no abnormal 24H duplicate/missing.
DEV100: DEV95 + Common FINAL QA.
Alert name: `OI/Funding 24H`.
Target: ALT_FINAL20_CURRENT + BTC_TREND_CURRENT. Integration, if separately approved, is read-only confirmation; no automatic score/ENTER Gate changes.

# GOAL B — HYPERLIQUID WHALE SIZE-BASED
Files: market_whales latest_summary/latest_events/recorder_state/positions_history.
Legacy USD-value action history is forbidden as READY evidence.
Compatible contract: MASTER_MARKET_WHALE_TIME_SERIES family, schema 1.1-compatible+, `size_engine=PASS`, `change_basis=POSITION_SIZE_SZI`.
DEV95: freshness<=90m; baseline_guard PASS; wallet query success>=90%; ~26H actual size-engine hourly timestamps>=20; no duplicate(time,address,coin); no abnormal missing breaking 4H/24H; first size observation baseline-only; query failure != closed; NEW/INCREASE/REDUCE/CLOSED/FLIP from signed szi only; action_notional=abs(delta_szi)*reference_mark; legacy value history forbidden for action delta; previously observed active whale 1H/4H populated and 24H only with real 24H history; no repeated production failure.
DEV100: DEV95 + Common FINAL QA.
Alert name: `WHALE 수량기반 24H`.
Target: MARKET_CURRENT; only SCREEN4 read-only evidence after approval; no automatic MARKET score changes.

# GOAL C — ALT LIVE LARGE FLOW
Files: live_flow summary/state/hourly bins.
Contract: Binance Spot aggTrades only; Large=W+MW(100K+); Retail=R1+R2; CCUSDT/HYPEUSDT unsupported=N/A; no cross-fill.
DEV95: freshness<=90m; supported18/18 OK, failures0; quality_guard PASS; min_large_trade_count_for_direction=3; ~28H completed-hour timestamps>=24; no duplicate(hour,symbol,bin); no abnormal missing; 1H/4H/24H continuity; if Large trades<3 then directional_eligible=false and signal=null; no current in-progress hour; no repeated production failure.
DEV100: DEV95 + Common FINAL QA.
Alert name: `LIVE LARGE FLOW 24H`.
Target: ALT_FINAL20_CURRENT; approved integration is read-only `24H|4H|1H|가속|활동품질`; no automatic long-term CVD/score/ENTER Gate changes.

# GOAL D — MARKET DATA VAULT
Files: market_vault latest_summary/vault_state/market_history.
Compatible contract: MASTER_MARKET_DATA_VAULT family, schema1.1-compatible+, official-keyless collector family.
Mandatory: US2Y, US10Y, US30Y, US10Y_REAL, EFFR, TGA_CLOSING_BALANCE. Secondary SOFR. Optional DefiLlama stablecoins/CoinGecko global. FRED failed/test adapter forbidden for production comparison.
DEV95: freshness<=90m; mandatory coverage>=5/6 and Treasury 2Y/10Y/30Y/real10Y + EFFR current success; ~26H production timestamps>=20; no duplicate(snapshot_hour,metric,source); no abnormal missing; same metric+same source lock; no cross-source delta; at least five mandatory actual 24H comparisons; 7D may remain N/A until real history; repeated source_observation_time is not new movement; no repeated production failure.
DEV100: DEV95 + Common FINAL QA.
Alert name: `MARKET DATA VAULT 24H`.
Target: MARKET_CURRENT; approved integration historical-cache read-only, direct current verification remains primary, no automatic score changes.

# GOAL E — BTC FRACTAL LATEST COMPATIBLE
Discover newest compatible BTC fractal/historical-regime research head and candidate; do not permanently pin a version string. PR/branch anchors may change.
Compatible patch/minor can auto-follow. Changes to outcome, leakage, episode, acceptance architecture, or new major data-layer architecture require BREAKING_CHANGE_REVIEW.
BTC TREND auto-integration is absolutely forbidden.
DEV95 technical: validation + PR validation completed/success; compile/build/summary/artifact success; actual artifact; direct candidate vs price-only/old-core comparison; real confidence distribution/accuracy; independent OOS episode acceptance; no duplicate episode; no future leakage; no N/A=0; fully-known historical outcomes only.
DEV100 statistical: all predeclared candidate-core acceptance gates PASS; confidence gate PASS when present; non-price confirmation incremental OOS utility gate PASS when present; final master_readiness or equivalent PASS.
If official candidate validation is complete but statistical result FAIL/PARTIAL, one first-time alert may be `✅ BTC 프렉탈 [VERSION] 검증 완료 · 수동 판정 필요` and must state `통계 판정=FAIL/PARTIAL`. It is not DEV100.
No PR merge/main/BTC TREND change before separate manual verification and explicit approval.

# GOAL F — SIGNAL OUTCOME VAULT LATEST COMPATIBLE
Purpose: store actual finalized MASTER signal snapshots and measure forward outcomes to learn which signals/combinations truly worked. It is a read-only calibration memory and may never auto-tune MASTER weights.
Current development anchor: Draft PR #8 / branch `signal-outcome-vault-v01`; auto-discover compatible successors by engine/schema/semantic contract.
Contract: engine=`SIGNAL_OUTCOME_VAULT`; append-only observation-time signal registry; no historical signal backfill; immutable accepted payload; N/A=null; direction-aware forward outcome; no future data in snapshot; no automatic MASTER tuning.
Required horizons: +4H,+24H,+72H,+7D. Price evaluation uses documented public market data at fixed resolution, with no pre-signal candle leakage. MFE/MAE must be entry-price-normalized price excursions. Unsupported symbols/data failures remain unavailable/N/A; no cross-source fill unless a future contract is separately reviewed.

F DEV95 technical conditions:
- Draft/pre-integration PR exists and remains unmerged.
- Compile and unit tests PASS.
- Validation artifact exists.
- Required signal schema validation PASS.
- duplicate `signal_id` protection PASS.
- future-timestamp guard PASS.
- historical backfill guard PASS.
- invalid input quarantine or equivalent non-repeating failure handling PASS.
- +4H/+24H/+72H/+7D maturity guard PASS.
- LONG/SHORT direction-aware return math PASS.
- LONG/SHORT MFE/MAE math PASS.
- deterministic signals/outcomes storage PASS.
- grouped summary exposes explicit sample count `n` and does not turn missing into zero.
- MASTER publish contract is read-only and explicitly forbids MASTER score/Gate/schedule changes.

F DEV100 FINAL QA additionally requires:
1. All F DEV95 conditions PASS on latest compatible head.
2. End-to-end synthetic test: ingest a current-time synthetic signal -> mature only eligible horizon(s) using mocked price data -> deterministic outcome and summary -> second identical run creates no duplicate result.
3. Public-market evaluation pagination/resolution logic is validated against boundary cases without pre-signal leakage.
4. One bad/unsupported signal cannot abort evaluation of unrelated valid signals; unavailable output remains null/N/A and no cross-fill occurs.
5. Production/shadow runner is idempotent and avoids meaningless heartbeat commits when no material signal/outcome state changed.
6. Shadow workflow/publish path is defined but must not become active on main or modify any MASTER before manual verification and user approval.
7. Calibration auto-tuning remains disabled; output is observation/read-only only.
8. No recent repeated validation workflow failure; schema/source contract consistent.

F DEV100 alert name: `SIGNAL OUTCOME VAULT`.
Target MASTER aliases after separate approval: BTC_TREND_CURRENT, ALT_FINAL20_CURRENT, MARKET_CURRENT and other explicitly approved MASTER families only as snapshot publishers. Publishing is logging only and must not alter their decision logic.
After F DEV100 + manual verification, user may approve SHADOW phase. Real sample accumulation milestones are separate from code completion: n<10=too small; n>=10 early observation; n>=30 initial review; n>=50 calibration proposal may be considered. Even at n>=50 no automatic MASTER change.

# Watcher lifecycle
Goals now include A/B/C/D/E/F. Do not auto-disable merely because all are DEV100. Disable only after each goal is explicitly INTEGRATED or INTEGRATION_NOT_NEEDED and the user explicitly chooses to stop the watcher.
