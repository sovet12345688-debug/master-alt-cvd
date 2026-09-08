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

## Automatic Forward Scorecard
Every official run performs this chain:

`Frozen/PIT contract -> Forward tracker -> Frozen evaluator guard -> Automatic scorecard -> Ledger commit -> Artifact`

The scorecard is evaluation-only. It never changes R2.6 and never promotes Production automatically.

Predeclared Production rules are frozen in `R26_FORWARD_PROMOTION_SPEC_V1.json`. The evaluator implementation and every reused metric-formula source are locked by `R26_FORWARD_SCORECARD_FREEZE_MANIFEST_V1.json` and checked by `scorecard_freeze_guard.py` before each scorecard run.

The automatic scorecard tracks:
- strict Forward calendar/sample eligibility,
- LONG / SHORT expectancy,
- Capture-to-Loss,
- MCR90 / MCR365 only after the corresponding truth windows mature,
- episode-order MDD,
- Seed -> Confirmed -> Core state monotonicity,
- direction x regime cycle safety,
- MATURE_BEAR SHORT safety,
- EARLY lead/conversion/noise diagnostics,
- Frozen/PIT/closed-candle/phase-firewall/execution-authority integrity.

Insufficient or censored evidence remains `N/A_NOT_MATURE`; it is never converted to zero or treated as failure.

Automated assessment may be `HOLD_COLLECTING`, `EXTEND_CANDIDATE`, `FAIL_CANDIDATE_FOR_FORMAL_REVIEW`, or `PASS_READY_FOR_FORMAL_REVIEW`. The actual `production_decision` remains `HOLD` until a separate formal promotion review is performed under the predeclared specification.

## Files
- `detections.csv` — all R2.6 awareness rows since policy OOS start.
- `events.csv` — awareness streak starts and state changes.
- `seeds.csv` — exact R2.5/R2.6 execution seeds plus Detection Lead.
- `positions.csv` — reconstructed Frozen R2.5 risk/state episodes as-of each run.
- `transactions.csv` — episode leg closes.
- `state.json` — latest tracker audit state and strict-forward metrics.
- `latest.md` — mobile-readable latest tracker snapshot.
- `runs.jsonl` — append-only tracker run summary history.
- `R26_FORWARD_PROMOTION_SPEC_V1.json` — predeclared Production-promotion rules.
- `forward_scorecard.py` — automatic Forward evaluator.
- `R26_FORWARD_SCORECARD_FREEZE_MANIFEST_V1.json` — frozen evaluator/formula identities.
- `scorecard_freeze_guard.py` — evaluator/formula identity guard.
- `scorecard.json` — latest machine-readable scorecard.
- `scorecard.md` — latest human-readable scorecard.
- `scorecard_runs.jsonl` — append-only scorecard run summaries.
- `forward_truth_episodes.csv` — strict prospective truth episodes when available.
- `forward_truth_mcr.csv` — matured truth-window Capture/MCR diagnostics.
- `state_monotonicity.csv` — state cohort/comparison detail.
- `cycle_buckets.csv` — direction x regime cycle detail.

## Governance
1. `EARLY_DETECT` and `PRIORITY_WATCH` have zero order authority.
2. `EXECUTION_READY` exists only when the exact Frozen R2.5 Seed exists.
3. Tracker and Scorecard outputs may not modify Frozen R2.6.
4. No threshold tuning from Forward outcomes.
5. No automatic Production promotion. Tracker/Scorecard remain `COLLECTING / HOLD` until a separately predeclared formal promotion review is completed.
6. Bridge-heldout data and historical diagnostics are never mixed into strict-forward metrics.
7. Scorecard formulas may not drift silently. Any prospective evaluator/formula change requires a new versioned freeze manifest and may not retroactively reinterpret prior strict-forward evidence.
