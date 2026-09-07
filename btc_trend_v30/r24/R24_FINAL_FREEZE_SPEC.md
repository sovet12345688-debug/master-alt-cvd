# MASTER BTC TREND V3 · R2.4 · MATURE-BEAR EXHAUSTION RISK LAYER

## Status
PRE-FREEZE SPEC. Historical R2.4 performance replay is forbidden until FINAL FREEZE passes.

## Parent
Frozen R2.1 remains the execution parent. R2.4 does not rewrite LONG logic, SHORT Seed logic, SHORT market confirmation rules, Stop, Exit, Reset, PIT, or the R2.1 delayed SHORT CORE rule.

## Single structural change
R2.4 adds one SHORT risk-allocation layer only.

### MATURE_BEAR classification
At the completed Daily context available at SHORT Seed, classify `MATURE_BEAR` only if all are true:
1. Daily close < Daily EMA200.
2. Daily EMA20 < Daily EMA50.
3. Daily EMA50 < Daily EMA200.
4. Daily EMA20 5-bar slope < 0.
5. Daily EMA50 10-bar slope <= 0.
6. Daily EMA200 20-bar slope < 0.

EMA200 is an EWM span-200 feature computed from completed Daily closes. Its 20-bar slope uses only completed Daily bars.

This classifier intentionally uses **no new price-distance cutoff, no days-below-EMA threshold, no days-since-high threshold, no new volume threshold, and no winner/loser median cutoff** from the BEAR SHORT 16 forensic.

## Risk action
The maturity classification is latched at SHORT Seed and cannot later clear within that episode to justify adding risk.

If `MATURE_BEAR` at Seed:
- SHORT Seed remains allowed at the inherited 0.30R.
- SHORT CONFIRM state may still form, but adds 0.00R.
- SHORT CORE state may still form if the unchanged R2.1 conditions pass, but adds 0.00R.
- Maximum episode risk remains 0.30R.
- No hard veto is introduced.
- Stop and Exit are unchanged.

If not `MATURE_BEAR`:
- Inherit the exact R2.1 risk path: 0.30R Seed + 0.30R Confirm + 0.25R Core, max 0.85R.

## Why this structure
The prior forensic suggested that BEAR SHORT losses often occurred after an already mature bearish structure and reversed quickly, but winner count was only 3. Therefore R2.4 does not convert observed forensic medians into thresholds. It tests only the higher-level hypothesis that **fully aligned mature bearish structure should not automatically receive additional risk after Seed**.

## Invariants
- LONG behavior identical to Frozen R2.1.
- SHORT Seed candidate set identical to Frozen R2.1.
- SHORT Stop identical.
- SHORT Exit identical.
- R2.1 `CONFIRM != CORE same decision event` rule identical.
- MATURE_BEAR never increases risk above R2.1.
- Classification is causal and closed-Daily only.
- No dynamic risk restoration in the same episode.

## Evidence firewall
- Same 2021-2026 history is diagnostic only.
- No R2.4 historical result may alter Frozen R2.4.
- No best historical threshold selection is allowed.
- Forward untouched OOS and full Risk Governor remain required for production.
