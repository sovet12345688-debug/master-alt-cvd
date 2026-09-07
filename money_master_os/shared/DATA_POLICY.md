# MONEY MASTER OS — DATA POLICY

## Evidence labels
Use `CONFIRMED / INTERPRETATION / INFERENCE / N/A` where applicable.

## Freshness and lineage
- Store or expose source, timestamp, run identifier, and freshness whenever available.
- Compare only same-source/same-definition values unless an approved bridge explicitly allows otherwise.
- Completed/closed observations and provisional/current observations must remain separate.
- Historical values must come from actual stored observations; no backfill from chat memory.
- Central collector availability/freshness metadata is published at `source_health/output/latest.json`.
- A MASTER may use Source Health to understand whether a source is healthy, delayed, stale, failed or missing, but must still apply its own domain-specific freshness and execution rules.

## Source Health authority boundary
- Source Health is DATA AVAILABILITY metadata only.
- It does not publish price direction, MASTER score, Permission, Entry, SL, TP, R:R, or trading Action.
- `CORE / OPTIONAL / CONTEXT` labels in Source Health describe operational importance, not score weights.
- `last_good` is a diagnostic pointer only. A stale source cannot be silently replaced with a `last_good` value and called current.
- Another MASTER's health or conclusion is never a substitute for direct execution-critical revalidation.

## Shared Fact Vault authority boundary
- Common normalized facts live at `shared_fact_vault/output/latest.json`.
- Shared Fact Vault contains facts only: value, unit, asset/venue/window, observation timestamp, source lineage, Source Health status and neutral data-quality/comparison metadata.
- It must not publish LONG/SHORT, bullish/bearish interpretation, MASTER score, Permission, Action, candidate rank, Entry, SL, TP, R:R or ENTER.
- Same semantic metric from different sources must remain source-qualified. The Vault must not silently reconcile, average or choose a winner across sources.
- Same-source/same-definition comparison metadata may be reused. Cross-source deltas require a separately approved bridge.
- A fact from `STALE / FAILED / MISSING` source may remain visible for lineage but must have `current_usable=false`.
- `last_good` must never be substituted into the Vault as a current fact.
- Shared Fact Vault Coverage is registered fact-source coverage only. It is never MASTER Coverage/confidence and never an execution gate.
- Existing collectors remain raw/history owners. The Vault stores only compact normalized facts and must not duplicate high-volume history.
- MASTERs may consume the same Vault facts, but each MASTER independently decides weighting, interpretation, permission and execution.
- Current/Entry/Trigger/SL/TP/R:R and other execution-critical values must still be revalidated by the responsible MASTER when required.

## WATCH / Event-only authority boundary
- Meaningful WATCH events live under `watch_events/` and are append-only event records, not a sixth MASTER.
- `NO CHANGE = NO EVENT`. Routine heartbeat, scheduled no-op, unchanged repeats and reconstructed historical WATCH rows are forbidden.
- The owning MASTER alone decides whether its canonical WATCH threshold is met. The central Event Store must not recalculate Hunter, Stealth, CVD, score, LEVEL1/LEVEL2, Risk Veto or any other MASTER-specific threshold.
- MASTER MARKET V1.2, MASTER ALT TOP100 V4.8 and MASTER ALT FINAL20 V2.2.1 may publish only after their own canonical WATCH logic produces a meaningful change.
- MASTER BTC TREND V2.6 keeps `NO hourly WATCH`; the Event Store must reject BTC MASTER-authored hourly WATCH events.
- MASTER TRADING remains manual-only; recurring WATCH events are disabled unless the user explicitly changes the canonical policy.
- A lifecycle-linked repeat is permitted only as `ESCALATED / DEESCALATED / CONFIRMED / REVERSED / CLEARED`; unchanged replay is forbidden.
- Same `event_id` may never be written twice. `correlation_key` links later lifecycle changes without rewriting history.
- WATCH events never overwrite `official_state/latest/*`, never update OFFICIAL score/history and never fill a missing OFFICIAL run.
- WATCH events may preserve MASTER-authored score/delta/context evidence, but the Event Store itself cannot derive or alter those values.
- WATCH events cannot contain execution-order fields such as Entry, SL, TP, R:R, leverage, order/position size, or actions `ENTER / SMALL ENTER / ADD`.
- Public WATCH history must contain no personal balance, account identifier, private position size, credentials or private execution details.
- System producers such as Source Health / Shared Fact Vault / OS Guard may emit operational status-transition events only. In V1 these are non-notifying diagnostics and cannot publish market direction.
- Event persistence failure is non-blocking to the MASTER analysis and must not change score, direction, Risk Veto, Gate or OFFICIAL State.

## OFFICIAL State authority boundary
- The latest persisted MASTER decision state lives under `official_state/latest/`.
- Only an ACTUAL OFFICIAL MASTER run may be stored with `state_status=STORED`.
- WATCH, provisional/current-candle, draft, dry-run, research-only, inferred, reconstructed or manually guessed values cannot be promoted to OFFICIAL State.
- `NO_STORED_OFFICIAL_RUN` is a valid state and must remain empty until an actual OFFICIAL run is persisted.
- Missing OFFICIAL history must not be reconstructed from chat memory, canonical prompt examples, raw collector outputs, another MASTER, Source Health, Shared Fact Vault, WATCH Events, or legacy handoff files.
- Source Health and Shared Fact Vault may be referenced as factual/data-availability inputs inside an OFFICIAL State, but they can never generate the MASTER's direction, score, Permission, Action, Entry, SL, TP, R:R, Coverage or confidence.
- MASTER Coverage and confidence must come from that actual MASTER OFFICIAL run. `registered_source_health_pct` and Shared Fact Vault coverage are never MASTER Coverage.
- `valid_until_kst`, next-run validity and freshness must be stored only when actually produced or explicitly defined by the MASTER. Unknown validity remains `UNKNOWN`; schedule cadence alone must not be used to manufacture an expiry.
- A stored OFFICIAL State with freshness `UNKNOWN` or `EXPIRED` remains a historical official snapshot, not proof that its old decision is current.
- Each MASTER owns its own OFFICIAL decision. Cross-MASTER facts may be shared, but another MASTER's score/direction/permission/action cannot be copied as this MASTER's owned conclusion.
- Public-repository OFFICIAL State must contain no personal balance, private position size, account identifiers, credentials or private execution details.

## Missing-data rules
- Missing != 0.
- Stale != current.
- Optional N/A alone does not invalidate a MASTER.
- Execution-critical unknowns must block ENTER where the relevant MASTER requires them.
- Source Health reason codes should be preserved when practical so N/A can be explained as e.g. `STATE_STALE`, `STATE_FILE_MISSING`, `LATEST_WORKFLOW_FAILED`, or `QUALITY_CHECK_FAILED` rather than a generic unknown.

## Persistence
- System logic belongs in canonical prompt/contract files.
- Latest normalized shared facts belong in `shared_fact_vault/output/latest.json`; raw/history data remains in the owning collectors.
- Latest actual MASTER decision continuity belongs in `official_state/latest/<master_id>.json`.
- Actual OFFICIAL history belongs in append-only `official_state/history/<master_id>/YYYY-MM.jsonl`.
- Meaningful WATCH history belongs in append-only `watch_events/history/<producer_id>/YYYY-MM.jsonl`; no history file is created for no-change runs.
- Latest WATCH continuity index belongs in `watch_events/latest/index.json` and cannot override OFFICIAL State.
- Legacy `money_master_os/handoff/*_LATEST.json` files are migration artifacts once OFFICIAL State V1 exists and must not override it.
- Raw high-volume market data stays in existing data engines; MONEY MASTER OS stores only continuity-critical pointers/state contracts.
- Source Health stores only compact latest status, last-good pointers and status-change events; it does not duplicate raw market data.
