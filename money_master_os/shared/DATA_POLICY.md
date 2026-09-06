# MONEY MASTER OS — DATA POLICY

## Evidence labels
Use `CONFIRMED / INTERPRETATION / INFERENCE / N/A` where applicable.

## Freshness and lineage
- Store or expose source, timestamp, run identifier, and freshness whenever available.
- Compare only same-source/same-definition values unless an approved bridge explicitly allows otherwise.
- Completed/closed observations and provisional/current observations must remain separate.
- Historical values must come from actual stored observations; no backfill from chat memory.

## Missing-data rules
- Missing != 0.
- Stale != current.
- Optional N/A alone does not invalidate a MASTER.
- Execution-critical unknowns must block ENTER where the relevant MASTER requires them.

## Persistence
- System logic belongs in canonical prompt/contract files.
- Current operational state belongs in state/handoff files.
- Raw high-volume market data stays in existing data engines; MONEY MASTER OS stores only the continuity-critical pointers/state contracts.
