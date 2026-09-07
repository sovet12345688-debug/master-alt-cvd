# MASTER BTC TREND V3.0 — R2.2 PRE-FREEZE SPEC

## Purpose
Reduce Seed detection latency without loosening actual execution triggers.

## Single allowed change
`Daily close within 1 ATR of the prior completed 20D high/low` moves from WATCH hard gate to priority-only metadata for both LONG and SHORT.

The numerical threshold remains exactly 1.0 ATR. It is not tuned.

## Inherited unchanged from frozen R2.1
- LONG: Daily EMA20 side/slope, 4H EMA20 side/slope, BREAKOUT/RECLAIM trigger, volume, close-location, stop, risk, CONFIRM, CORE, HOLD, EXIT, RESET.
- SHORT: Daily EMA20 side/slope, 4H EMA20 side/slope, Weekly/Daily bearish regime, BREAKDOWN/FAILED-RETEST trigger, volume, close-location, stop, risk, CONFIRM, CORE market conditions, HOLD, EXIT, RESET.
- R2.1 rule: SHORT CORE must be strictly after CONFIRM with >=1 later completed Daily session.
- PIT availability semantics and all predeclared gates.

## Priority semantics
The unchanged 1 ATR distance condition is still computed as `priority_extreme_near`. It may rank/label observations but may not by itself authorize execution.

## Execution firewall
A Seed still requires the unchanged causal 4H route trigger plus unchanged structural stop geometry and all remaining direction-specific regime/confirmation rules. No early entry without trigger is introduced.

## Governance
- Historical Seed-Lag forensic generated this hypothesis only.
- R2.2 historical replay before FINAL FREEZE: forbidden.
- Historical replay after freeze is diagnostic only and cannot promote R2.2.
- If this rule is changed after seeing results, the result must be a new revision/version.
