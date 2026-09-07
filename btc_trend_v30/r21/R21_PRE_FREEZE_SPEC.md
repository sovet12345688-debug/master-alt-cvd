# MASTER BTC TREND V3 R2.1 — SINGLE-CHANGE PRE-FREEZE

## Parent
- Parent model: MASTER BTC TREND V3 R2.0 REV2_PIT_AVAILABILITY_HOTFIX
- Parent commit: `6e0c14f0b483dec1d15650e226ae65f9adc93800`
- Parent frozen config blob: `9f1a54edb0312bd86f230f025f539bd4255a016f`
- Parent engine blob: `cf5c5bf7a10ba699fe695964b7e7f435dead016b`

## Why R2.1 exists
R2.0 historical diagnostic showed SHORT expectancy slightly below zero. A FAST forensic of 21 SHORT episodes found that 16 episodes added CONFIRM and CORE on the same availability timestamp and this group was historically weak. This is hypothesis-generation only, not promotion evidence.

## The only allowed behavioral change
`SHORT_CORE_REQUIRES_SEPARATE_COMPLETED_DAILY_CONFIRMATION`

A SHORT CORE add is forbidden on the same decision/availability timestamp as SHORT CONFIRM. CORE may only be evaluated at a strictly later decision time after at least one completed 1D session has become available, and the unchanged R2.0 REV2 SHORT core market conditions must still pass.

Operational form:
1. SHORT SEED: unchanged.
2. SHORT CONFIRM: unchanged.
3. SHORT CORE: unchanged market-condition gate, but timing gate added:
   - `candidate_time > confirm_time`
   - `completed_daily_sessions_since_confirm >= 1`
4. LONG machine: unchanged.
5. Risk, stop, holding, derisk, exit, reset, PIT semantics and predeclared gates: unchanged.

## Explicit non-changes
No threshold changes. No stop changes. No risk-budget changes. No LONG changes. No SHORT seed/confirm market-condition changes. No exit changes. No reset changes. No PIT changes. No multi-parameter tuning.

## Evidence firewall
R2.1 must be frozen before any R2.1 historical replay. Historical 2021-01-01..2026-09-04 results may diagnose R2.1 but cannot promote it to production. Any further rule change requires a new version/freeze.

## Pre-freeze acceptance
- candidate identity PASS
- parent R2.0 REV2 identity PASS
- single-change contract PASS
- no R2.1 replay output exists before freeze
- no R2.0 files modified on this branch relative to the R2.1 base commit
