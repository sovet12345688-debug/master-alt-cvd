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

## Missing-data rules
- Missing != 0.
- Stale != current.
- Optional N/A alone does not invalidate a MASTER.
- Execution-critical unknowns must block ENTER where the relevant MASTER requires them.
- Source Health reason codes should be preserved when practical so N/A can be explained as e.g. `STATE_STALE`, `STATE_FILE_MISSING`, `LATEST_WORKFLOW_FAILED`, or `QUALITY_CHECK_FAILED` rather than a generic unknown.

## Persistence
- System logic belongs in canonical prompt/contract files.
- Current operational state belongs in state/handoff files.
- Raw high-volume market data stays in existing data engines; MONEY MASTER OS stores only the continuity-critical pointers/state contracts.
- Source Health stores only compact latest status, last-good pointers and status-change events; it does not duplicate raw market data.
