# MONEY MASTER OS V2 — P1 OFFICIAL STATE

Purpose: persist the latest ACTUAL OFFICIAL result for each of the five MASTERs in one common machine-readable format without reconstructing missing history.

## Authority
`Registry > MASTER Manifest/Canonical Source > OFFICIAL State > legacy handoff > chat memory`

OFFICIAL State is the canonical persisted decision snapshot for an actual MASTER OFFICIAL run. It is not a market-data collector, not Source Health, and not a cross-MASTER decision layer.

## Five exact MASTER IDs
- `market`
- `btc_trend`
- `alt_top100`
- `alt_final20`
- `trading`

## Files
- `schema.json` — common OFFICIAL State contract.
- `OFFICIAL_STATE_TEMPLATE.json` — safe write template.
- `latest/index.json` — five-MASTER latest-state index.
- `latest/<master_id>.json` — latest persisted OFFICIAL state or explicit `NO_STORED_OFFICIAL_RUN` placeholder.
- `history/<master_id>/YYYY-MM.jsonl` — append-only ACTUAL OFFICIAL run history after publication.
- `publish_official_state.py` — validates and publishes a supplied ACTUAL OFFICIAL state.

## Hard rules
1. Only an actual OFFICIAL run may be stored as `STORED`.
2. WATCH, provisional, draft, dry-run, research-only and inferred states are forbidden as OFFICIAL.
3. Missing historical values remain missing. Never backfill from chat memory.
4. `NO_STORED_OFFICIAL_RUN` is a valid and preferred state when no persisted OFFICIAL run exists.
5. Source Health can be referenced only as data-availability metadata. It cannot create direction, score, permission, action, Entry, SL, TP or R:R.
6. Another MASTER's direction/score/permission must not be copied as this MASTER's owned conclusion.
7. `valid_until_kst` must be stored only when the MASTER itself produced or explicitly defined it. Do not infer validity from a schedule.
8. If validity is unknown or expired, the stored state is historical context only until current revalidation.
9. Coverage/confidence must come from the actual MASTER OFFICIAL run. Registered Source Health percentage is never MASTER Coverage.
10. Public-repository OFFICIAL State must never contain personal account balance, private position size, account identifiers, API credentials or other private execution details.

## Current bootstrap initialization policy
Existing persisted OFFICIAL history may be migrated only when the exact source record exists in GitHub. At P1 initialization, MARKET has a verifiable OFFICIAL history row and may be migrated. Other MASTERs start as `NO_STORED_OFFICIAL_RUN` unless an exact persisted OFFICIAL record is found.

## Legacy handoff policy
Files under `money_master_os/handoff/*_LATEST.json` predate the five-MASTER V2 identity split and may contain obsolete identities or versions. They are migration artifacts only. New-room bootstrap must prefer `official_state/latest/<master_id>.json` and must not use legacy handoff content to reconstruct an OFFICIAL state.
