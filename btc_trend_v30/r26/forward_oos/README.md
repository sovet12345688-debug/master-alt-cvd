# MASTER BTC TREND V3 R2.6 — Official Forward OOS Tracker

## Purpose
Track Frozen R2.6 on new BTCUSDT Spot data without changing thresholds, entry logic, risk, stop, state transitions, or exits.

## Time firewall
- Historical diagnostic end: `2026-09-04T23:59:59Z`
- Policy OOS start: `2026-09-05T00:00:00Z`
- R2.6 FINAL Freeze completed: `2026-09-08T02:40:15Z`
- Strict prospective start: `2026-09-08T04:00:00Z` (first completed 4H decision bar after the final freeze)

Rows from policy start through strict start are labeled `BRIDGE_HELDOUT_PRE_FREEZE`. They are retained for continuity and audit, but **must not count as strict production-promotion evidence**. Only `STRICT_FORWARD` rows may be used as prospective evidence.

## Frozen identity
- R2.6 Frozen config SHA256: `bbdbbe173e8f16bb6d57d6ed5bdff52d616c42fead5b59b39c5b3d7188a64a61`
- R2.6 Frozen config blob: `09d6d6689f46c16f3a40b16c58495a999e4f9e75`
- R2.6 Engine blob: `c511b99205314d351bacb441cc88d70d9a4947ee`
- R2.6 Contract blob: `ae6e8bd3cbafdc8b8a2c9672db5ee16e324850b3`
- Execution parent R2.5 Engine blob: `b5efe7378de839b338057826e0a7cb29926ec3a6`

Every tracker run verifies these identities before reading market data.

## Data
- BTCUSDT Spot only.
- Public Binance klines.
- Primary endpoint: `data-api.binance.vision`, fallback: `api.binance.com`.
- 1H / 4H / 1D closed candles only.
- Weekly context is causally rebuilt from completed Daily candles by the Frozen engine.
- Warm-up data before OOS is allowed only to form causal indicators; outcomes before OOS are not Forward evidence.

## Files
- `detections.csv` — all R2.6 awareness rows since policy OOS start.
- `events.csv` — awareness streak starts and state changes.
- `seeds.csv` — exact R2.5/R2.6 execution seeds plus Detection Lead.
- `positions.csv` — reconstructed Frozen R2.5 risk/state episodes as-of each run.
- `transactions.csv` — episode leg closes.
- `state.json` — latest audit state and strict-forward metrics.
- `latest.md` — mobile-readable latest snapshot.
- `runs.jsonl` — append-only run summary history.

## Governance
1. `EARLY_DETECT` and `PRIORITY_WATCH` have zero order authority.
2. `EXECUTION_READY` exists only when the exact Frozen R2.5 Seed exists.
3. Tracker output may not modify Frozen R2.6.
4. No threshold tuning from Forward outcomes.
5. No automatic Production promotion. Tracker status remains `COLLECTING / HOLD` until a separately predeclared production-promotion review is completed.
6. Bridge-heldout data and historical diagnostics are never mixed into strict-forward metrics.
