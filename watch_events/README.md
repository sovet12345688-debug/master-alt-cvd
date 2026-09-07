# MONEY MASTER OS — WATCH / Event-only Store V1

## Purpose
Persist only meaningful WATCH changes already decided by the owning MASTER or an operational status transition produced by the OS. This layer is persistence/dedup/lineage only; it is not a sixth MASTER and never calculates market direction.

## Authority
- MASTER MARKET V1.2 owns its canonical `URGENT WATCH` thresholds and LEVEL1/LEVEL2 meaning.
- MASTER ALT 1 TOP100 V4.8 owns its canonical `HUNTER WATCH ALERT` gates.
- MASTER ALT 2 FINAL20 V2.2.1 owns its canonical `WATCH` gates.
- MASTER BTC TREND V2.6 keeps `NO hourly WATCH`.
- MASTER TRADING remains manual-only with no recurring WATCH.
- Source Health / Shared Fact Vault / OS Guard may emit operational status-transition events only; they cannot emit market direction or trade alerts.

## Storage
- Schema: `watch_events/schema.json`
- Producer registry: `watch_events/registry.json`
- Latest compact index: `watch_events/latest/index.json`
- Append-only history after first real event: `watch_events/history/<producer_id>/YYYY-MM.jsonl`
- Publisher/validator: `watch_events/publish_watch_event.py`

## Event-only rule
`NO CHANGE = NO EVENT`.

Do not write heartbeat, routine scheduled checks, repeated unchanged conditions, price-only noise, or old-event replays. A lifecycle-linked repeat is allowed only when the producing MASTER declares one of: `ESCALATED / DEESCALATED / CONFIRMED / REVERSED / CLEARED` under its own canonical rules.

## Separation from OFFICIAL State
WATCH events:
- never write `official_state/latest/*`;
- never update OFFICIAL score/history;
- never become a missing OFFICIAL run;
- never reconstruct prior state;
- never create Entry/SL/TP/R:R or execution orders.

OFFICIAL State remains the latest persisted formal MASTER decision. WATCH Events are only meaningful-change records between/around formal runs.

## Notification
The store records whether a MASTER-authored event is `notification.eligible=true`; it does not send a notification itself. User-visible notification remains controlled by the owning MASTER/runtime. System health events default to `notification.eligible=false` in V1.

## Privacy
Public history must not contain account balance, actual position size, account identifiers, credentials, private execution details, or personal trading state.

## Failure behavior
Event-persistence failure is non-blocking to the MASTER analysis. Report/log the persistence failure, but do not change score, direction, Gate, Risk Veto, or OFFICIAL State because the event store failed.

## No backfill
The initial index intentionally starts at zero events. Past WATCH messages are not reconstructed from chat history, old handoffs, screenshots, or inferred market moves.
