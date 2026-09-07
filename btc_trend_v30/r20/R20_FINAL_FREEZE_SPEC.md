# MASTER BTC TREND V3 R2.0 — FINAL FREEZE

## Status
- Model: `MASTER_BTC_TREND_V3_R2_0`
- Branch: `btc-trend-v30-r20-design`
- Status: `FINAL_FROZEN_NO_REPLAY`
- Parent R1.4 commit: `451e1a70812e0cdce341aa11b1ef3cb7d9fb7f31`
- Historical performance replay before freeze: **0**
- Same-period 2021-01-01 through 2026-09-04 replay after freeze is **DIAGNOSTIC ONLY** and cannot promote the model.
- Clean forward evidence starts `2026-09-05T00:00:00Z` and matures only as required horizons complete.

## Canonical identities
- Candidate config SHA256: `cda041cba387688e67801e3ca4bed7976e32a234cf47ee2439fa65caefee5907`
- **Frozen config SHA256: `91f8c39d993e18927f24f19a39f3833ec1bc3002266c62c9e2f4faf24c76db88`**
- Engine Git blob SHA1: `c24dc41e1b01691cb165c077dcc43d3da890a779`
- Contract Git blob SHA1: `08aa0069283e717532c9f520f02654e3384a9002`
- PIT contract PASS run: `34105181179`, job `101688447501`

Any change to a frozen rule, threshold, risk budget, engine implementation or holding/exit rule creates a new R2.x baseline and requires a new pre-replay freeze.

## Purpose
R2.0 is an independent BTC trend-capture architecture, not an R1.x threshold patch. It separates trend regime, initiation, participation and holding/exit. LONG and SHORT are independent state machines.

## State machines
LONG: `L0_NEUTRAL -> L1_WATCH -> L2_SEED -> L3_CONFIRMED -> L4_CORE -> L5_HOLD -> L6_DERISK -> EXIT`

SHORT: `S0_NEUTRAL -> S1_WATCH -> S2_SEED -> S3_CONFIRMED -> S4_CORE -> S5_HOLD -> S6_DERISK -> EXIT`

No R1.x 4H ADD layer is inherited. No fixed +3R full exit. No automatic same-episode re-entry after failure.

## Risk budgets
LONG: `0.35R SEED + 0.35R CONFIRM + 0.30R CORE = 1.00R max`.

SHORT: `0.30R SEED + 0.30R CONFIRM + 0.25R CORE = 0.85R max`.

Original structural stop may not be widened.

## Core causal features
Closed candles only. Prior range and prior volume baselines exclude the current bar. Weekly context uses only completed Monday-Sunday weeks and as-of joins only the latest completed higher-timeframe row.

- EMA20 / EMA50
- Wilder-style ATR14
- prior completed 20-bar high/low
- prior completed 20-bar mean volume
- EMA20 5-session slope
- EMA50 10-session slope
- prior 3 completed H4 bars as causal swing reference
- H4 de-risk reversal: prior LH then current LL; SHORT mirror prior HL then current HH

## LONG
WATCH requires Daily and H4 close/EMA20 slope alignment plus proximity to prior 20D high.

SEED allows either:
- H4 breakout above prior 20 H4 high with volume ratio >=1.20 and close in upper 40% of range, or
- Daily EMA20 reclaim after a breach within prior 5 sessions, close above prior daily high and Daily volume ratio >=1.10.

CONFIRM must occur within 5 completed daily sessions, remain above Daily EMA20, exceed the SEED-day high, keep EMA20 rising, and avoid reference failure beyond 0.5 ATR.

CORE requires weekly bullish/nonnegative EMA20 context, Daily close above EMA50 and EMA alignment/rising condition.

## SHORT
WATCH requires Daily and H4 bearish EMA20 alignment plus proximity to prior 20D low.

SEED requires weekly/daily bearish regime and either:
- H4 breakdown below prior 20 H4 low with volume ratio >=1.25 and close in lower 40%, or
- deterministic failed retest. Reference = minimum of latest completed Daily EMA20 and prior completed 20D low; a prior completed H4 close must have broken below reference, current H4 high retests reference, then completed close returns below reference and H4 EMA20 with volume ratio >=1.10.

CONFIRM must occur within 4 completed daily sessions, remain below Daily EMA20, break SEED-day low, keep EMA20 falling and avoid reference failure beyond 0.5 ATR.

CORE requires EMA20 below EMA50, EMA50 nonpositive slope and weekly close below EMA20.

## HOLD / EXIT
No fixed profit target. No mandatory break-even stop move.

De-risk 50% only when Daily EMA20 adverse close and completed-H4 structural reversal occur together.

Full exit can occur on:
- two consecutive adverse Daily EMA20 closes with adverse/nonpositive slope,
- adverse Daily EMA50 close,
- 3 ATR Chandelier exit.

## Reset
After exit, at least 3 completed daily sessions **and** a new causal 20D family are required. Route, stop and confirmation state from the prior episode may not be inherited.

## PIT validation completed before freeze
PASS run `34105181179`:
1. feature prefix invariance,
2. future-data mutation cannot change past features,
3. prior ranges/volume baseline exclude current candle,
4. higher-timeframe as-of cannot use a future completed week.

Historical performance replay remained 0 before freeze.

## Predeclared gates
- mean MCR90 >=20%
- mean MCR365 >=20%
- 90D trend recall >=55%
- 365D trend recall >=55%
- LONG expectancy >0R
- SHORT expectancy >0R
- confirmed false-start <=45%
- capture/loss ratio >1.10
- median SEED lag <=7 days
- cycle bucket expectancy >=-0.15R when n>=10
- PIT invariance PASS
- robustness PASS
- cycle independence PASS
- state monotonicity PASS
- full Risk Governor required for production
- exact V2.6 H2H remains required under current governance
- untouched forward OOS required for production

## Governance
Historical replay after this file is frozen may diagnose the architecture but may not justify production promotion. No best historical variant may silently replace the frozen baseline. Any rule change creates a new baseline and a new evidence firewall.
