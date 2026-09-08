# MASTER BTC TREND V3.0 — R2.5 FULL RISK GOVERNOR

Status: PRE-FREEZE / NO R2.5 HISTORICAL REPLAY
Parent: Frozen R2.4 (`MATURE_BEAR_SEED_ONLY_RISK_CAP`)

## Objective
Separate **state confirmation** from **capital authorization**. R2.5 does not alter signal eligibility, state transitions, structural stops, holding/exit logic, reset logic, PIT semantics, or R2.4 MATURE_BEAR classification. It only governs when additional risk is allowed.

## Single logical change
`CONFIRMED` remains a valid market/state-machine state but receives **no incremental risk**. Additional risk is authorized only when the existing parent `CORE` state is reached.

### Risk schedule
- LONG: SEED 0.35R → CONFIRMED remains 0.35R → CORE total cap 1.00R (CORE add = 0.65R).
- SHORT, NOT_MATURE_BEAR: SEED 0.30R → CONFIRMED remains 0.30R → CORE total cap 0.85R (CORE add = 0.55R).
- SHORT, MATURE_BEAR: inherit R2.4 cap: SEED 0.30R → CONFIRMED 0.30R → CORE 0.30R (no adds).

No new performance-derived threshold is introduced. The numbers above are arithmetic reallocation of already-frozen parent seed allocations and already-frozen parent maximum episode risk caps.

## Governor invariants
1. Parent signal/state rules remain unchanged.
2. Structural stop may never widen.
3. No incremental risk after DERISK.
4. Episode exposure may never exceed the applicable frozen maximum R cap.
5. MATURE_BEAR cap from R2.4 dominates all normal SHORT scaling.
6. CONFIRMED is informational; only CORE can authorize incremental capital.
7. Leverage/margin does not define risk. Structural stop distance and external 1R budget define position sizing at runtime.
8. Opposite-direction overlap and reset behavior remain inherited from the parent engine.
9. Closed-candle/PIT availability rules remain inherited.
10. Same 2021-2026 historical window is diagnostic only and cannot promote production.

## Evidence firewall
The observed `CONFIRMED_NO_CORE` loss concentration may motivate testing the governance hypothesis, but no winner/loser threshold, duration cutoff, excursion cutoff, volume cutoff, or state-specific performance cutoff is used.

## Predeclared validation
Before any R2.5 historical replay:
- parent R2.4 frozen identity must match;
- R2.0 PIT tests 7/7 must pass;
- R2.1 and R2.4 contracts must pass;
- R2.5 contract must prove state rules unchanged and risk arithmetic exact;
- historical replay count before freeze = 0.

After freeze, one historical diagnostic may compare R2.5 with Frozen R2.4 on MCR90/365, LONG/SHORT expectancy, capture/loss, MDD, Cycle, state monotonicity, and CONFIRMED_NO_CORE loss exposure. Historical results cannot modify Frozen R2.5.