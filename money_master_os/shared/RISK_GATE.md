# MONEY MASTER OS — SHARED RISK / EXECUTION INVARIANTS

These invariants protect execution continuity during room migration. MASTER-specific gates may be stricter.

- Good asset + bad entry = WAIT.
- Extended/chased entry = WAIT.
- Missing required trigger = WAIT where trigger confirmation is mandatory.
- Severe Risk Veto cannot be offset by positive scores.
- Structural invalidation/SL must not be invented.
- TP and R:R must be based on explicit, source-supported prices when an execution decision is produced.
- If Current/Entry/Trigger/SL/TP/R:R are execution-critical and cannot be verified, automatic ENTER is forbidden.
- A bootstrap or migration validation failure cannot be bypassed by using a remembered prior answer.
