# MASTER BTC TREND V3.0 — R2.3 PRE-FREEZE SPEC

## Purpose
Add earlier directional awareness without changing the frozen R2.1 execution engine.

## Single allowed change
Add a **non-execution Early Detection layer** in front of frozen R2.1.

The detector has three user-facing states:
1. `EARLY_DETECT` — directional Daily + 4H EMA structure aligns before the R2.1 20D-extreme proximity gate.
2. `PRIORITY_WATCH` — the unchanged R2.1 prior-20D-extreme within 1 ATR condition is also satisfied.
3. `EXECUTION_READY` — and only when the exact frozen R2.1 Seed candidate exists at that timestamp/direction.

## Execution firewall
- `EARLY_DETECT` cannot enter, add, change risk, move stop, or bypass any R2.1 Seed rule.
- `PRIORITY_WATCH` cannot enter, add, change risk, move stop, or bypass any R2.1 Seed rule.
- `EXECUTION_READY` is not created by R2.3. It is a label on the exact Seed output produced by frozen R2.1.
- R2.3 must not override `scan_seed_candidates`.

## Early Detection logic
### LONG
All must be true on causally available completed candles:
- Daily close > Daily EMA20
- Daily EMA20 5-session slope > 0
- 4H close > 4H EMA20
- 4H EMA20 5-bar slope > 0

### SHORT
Mirror:
- Daily close < Daily EMA20
- Daily EMA20 5-session slope < 0
- 4H close < 4H EMA20
- 4H EMA20 5-bar slope < 0

## Priority Watch metadata
The unchanged R2.1 distance rule remains 1.0 ATR:
- LONG: Daily close >= prior completed 20D high - 1.0 Daily ATR14
- SHORT: Daily close <= prior completed 20D low + 1.0 Daily ATR14

This is labeling/prioritization only. It does not authorize execution.

## Inherited unchanged from frozen R2.1
- All LONG/SHORT Seed logic and thresholds
- LONG/SHORT structural stops and risk budget
- R2.1 SHORT CORE separate-Daily-confirmation timing
- CONFIRM / CORE / HOLD / DERISK / EXIT / RESET
- PIT availability semantics
- Historical candidate gates

## Governance
- R2.2 historical failure motivated the architectural separation only.
- R2.3 historical replay before FINAL FREEZE is forbidden.
- Same 2021-2026 historical window is diagnostic only and cannot promote R2.3.
- Any later change to execution rules must be a new version; it cannot be silently folded into R2.3.
