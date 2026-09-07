# MONEY MASTER OS V2 — Shared Fact Vault

## Purpose
Shared Fact Vault is the common read-only fact layer for the five independent MASTERs.

It stores compact normalized facts from existing repository collectors so the same observation does not need to be re-collected or silently reinterpreted by each MASTER.

## Authority boundary
Shared Fact Vault owns **facts only**.

Allowed:
- observed/derived metric value
- unit / asset / venue
- source name and source data path
- source observation timestamp
- Source Health status/reason codes
- neutral same-source comparison metadata
- data-quality metadata

Forbidden:
- LONG / SHORT direction
- Bullish / Bearish interpretation
- MASTER score / Permission / Action
- Entry / SL / TP / R:R
- candidate ranking / PRE-RUNNER / ENTER
- another MASTER's conclusion

## Core rules
1. Same metric + different source is never silently reconciled. Keep both source-qualified facts.
2. Same-source/same-definition comparisons may be preserved; cross-source deltas require an explicit approved bridge.
3. `STALE / FAILED / MISSING` source facts may remain visible for lineage, but `current_usable=false`.
4. `last_good` is never substituted as a current fact.
5. Missing is not zero.
6. No chat-memory backfill.
7. No raw high-volume duplication. Existing engines remain raw/history owners; Vault stores only compact shared facts.
8. Vault availability/coverage is not MASTER Coverage and never implies trading permission.

## Files
- `registry.json` — approved adapters and data paths.
- `schema.json` — Shared Fact Vault output contract.
- `build_shared_facts.py` — normalize, validate and self-test.
- `output/latest.json` — latest compact shared fact snapshot.

## Active V1 adapters
- MARKET global metrics
- macro/liquidity metrics
- BTC/ETH ETF flows
- venue-specific derivatives OI/Funding/price-change metrics
- ALT2 live large-trade flow metrics
- FINAL20 long-horizon order-size CVD metrics

Other collectors stay deferred until their data schema is explicitly reviewed. Deferred does not mean failed.
