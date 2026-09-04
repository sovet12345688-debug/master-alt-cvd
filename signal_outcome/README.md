# SIGNAL OUTCOME VAULT V0.1

Purpose: persist MASTER signal snapshots and measure what actually happened afterwards without changing any MASTER score, Gate, schedule, or live plan.

## Core principle
This is a read-only calibration / outcome-memory engine. It records a signal exactly as it existed at observation time, then evaluates future market outcomes only after each horizon has matured.

No MASTER integration is implied by this branch.

## V0.1 scope
- Append-only signal registry.
- No historical backfill of signals.
- Unique `signal_id`; duplicate ingestion rejected.
- Required observation timestamp and observed price.
- Outcome horizons: +4H, +24H, +72H, +7D.
- Direction-aware return.
- MFE / MAE from Binance Spot 1H klines.
- No future data may enter the stored signal snapshot.
- N/A stays null; never converted to zero.
- Aggregated calibration summaries by source MASTER, symbol, direction, signal label, score bands, and optional evidence tags.

## Signal contract
Each input signal is a JSON object placed in `signal_outcome/inbox/` and consumed once.

Required fields:
- `signal_id`: globally unique string
- `observed_at_utc`: timezone-aware UTC timestamp
- `source_master`: stable family name such as `MASTER_ALT_FINAL20` or `MASTER_BTC_TREND`
- `source_version`: version observed at signal time
- `symbol`: Binance-compatible symbol such as `BTCUSDT`, `TAOUSDT`
- `direction`: `LONG`, `SHORT`, or `NEUTRAL`
- `observed_price`: positive numeric price
- `signal_label`: human-readable classification

Optional fields:
- `scores`: numeric score snapshot
- `evidence`: read-only evidence snapshot
- `tags`: string array
- `run_id`
- `notes`

## Anti-leakage / anti-bias rules
1. A signal is accepted only around its actual observation time.
2. Retroactive reconstruction of missing MASTER signals is prohibited.
3. Accepted signal payload is immutable.
4. Future outcomes are populated only after each horizon matures.
5. Binance price data is evaluation data only and cannot modify the original signal.
6. N/A is never converted to zero.
7. Aggregated statistics always expose sample count.
8. This engine never auto-tunes or rewrites MASTER weights.

## Outcome definitions
For observed price `P0`:
- raw return = `(future_close / P0 - 1) * 100`
- direction-aware return: LONG=raw return, SHORT=-raw return, NEUTRAL=null
- LONG MFE = max high vs P0; LONG MAE = min low vs P0
- SHORT MFE = P0 vs min low; SHORT MAE = adverse max high vs P0

## Persistent files
- `data/signals.jsonl`: immutable accepted signals
- `data/outcomes.jsonl`: deterministic latest outcome record per signal
- `output/latest_summary.json`: sample-counted calibration output
- `state/vault_state.json`: health and maturity counters

## V0.1 acceptance
Technical validation must prove schema guards, duplicate protection, no-backfill/future-timestamp guards, horizon maturity, direction-aware return/MFE/MAE math, deterministic output, explicit sample counts, and artifact generation.

Statistical usefulness is not claimed in V0.1. Calibration recommendations stay disabled until enough real forward observations accumulate.
