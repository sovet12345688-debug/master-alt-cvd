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

## MCR horizon firewall
MCR numerator uses only the initial position. For 90D and 365D separately, each fraction uses its actual exit if that exit occurred on/before the truth horizon; if still open at the horizon, it is marked at the last available close on/before that horizon. Post-horizon prices are forbidden. 4H ADD PnL is portfolio performance and is excluded from MCR. Gates remain mean MCR90 >=20% and MCR365 >=20%.

## Predeclared robustness battery
No best variant may replace the frozen baseline. Diagnostic perturbations are predeclared only: ADD progress 0.75R / 1.25R, runner fraction 0.40 / 0.60 with complementary fixed fraction.

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

Canonical config SHA256: `4745855cdf99052756ac0eb7046979399c474d73a966ada119f9c4ec13ec7a3d`.
Any later rule or threshold change creates a new baseline/version.
