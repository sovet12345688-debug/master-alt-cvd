# MASTER BTC TREND V3 R1.4 — FAST-TRACK PRE-REPLAY FINAL FREEZE

## Status
- New research baseline: R1.4
- R1.3 and V2.6 untouched.
- R1.4 rules are frozen before any R1.4 historical replay.
- Same-period historical R1.4 replay is diagnostic only, never untouched promotion evidence.

## Signal layer
R1.4 inherits the frozen R1.3 1H RETEST/IGNITION signal contract exactly. No initial-entry threshold was changed.

## Episode risk budget
- Total episode risk cap: 1.0R.
- Initial 1H risk budget: 0.70R.
- One optional 4H ADD: maximum 0.30R.
- No second ADD.

## 4H ADD redesign
4H is no longer an independent new trade generator. It is a thesis-continuity add.
It requires all of:
1. same causal family, engine and direction as the initial 1H entry;
2. same route as the initial 1H entry;
3. initial 1H leg still unresolved at 4H ADD confirmation;
4. CLOSED-1H favorable progress of at least +1.0 initial structural R before the ADD confirmation;
5. current risk state OPEN;
6. daily transition aligned with trade direction;
7. the R1.3 execution contract itself passes on the 4H candidate.

Route change => NO ADD. TP or SL already resolved => NO ADD.
The +1R progress rule is a pre-replay structural design choice, not selected from an R1.4 historical grid.

## Initial position management
The initial 0.70R risk position is split 50/50:
- Fixed half: TP at +3R or original structural SL.
- Runner half: original structural SL remains. No stop migration. Exit at the next available 1H open after the first completed 1D close against EMA20 after entry.

This creates a deterministic trend-capture path without loosening entry quality or inventing discretionary trailing.

## MCR
MCR numerator is the positive weighted realized price return from the initial position only: fixed half + runner half. 4H ADD PnL is portfolio performance and is excluded from the MCR numerator. Gates remain mean MCR90 >=20% and MCR365 >=20%.

## Predeclared gates
- LONG portfolio expectancy >0R
- SHORT portfolio expectancy >0R
- confirmed false-start rate <=50%
- MCR90 mean >=20%
- MCR365 mean >=20%
- capture/loss ratio >1
- robustness required
- cycle independence required
- score monotonicity minimum comparable-bin n=10
- full Risk Governor required for production
- exact V2.6 H2H remains required under current governance

## Evidence firewall
Because R1.4 was designed from R1.3 historical diagnosis, replaying 2021-2026 after this freeze is DIAGNOSTIC ONLY. Clean promotion evidence begins with forward data from 2026-09-05 UTC and matures only as the required horizons complete.

Canonical config SHA256: `74a534dfb97ccc278f33cc5595d945737f5f3e7da93fb88c1866f43c016e8e6b`.
Any later rule or threshold change creates a new baseline/version.
