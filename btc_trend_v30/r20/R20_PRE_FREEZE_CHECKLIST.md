# R2.0 PRE-FREEZE CHECKLIST

## Design identity
- [x] R2.0 is a new architecture, not R1.x threshold tuning.
- [x] LONG and SHORT are separate machines.
- [x] R1.x 4H ADD layer is removed.
- [x] Fixed +3R full exit is removed.
- [x] Failed-thesis automatic re-entry is forbidden.
- [x] Historical core uses reproducible OHLCV only.

## Before first historical replay
- [ ] Candidate config converted to canonical frozen config.
- [ ] Config SHA256 generated and stored in manifest.
- [ ] LONG state transitions implemented deterministically.
- [ ] SHORT state transitions implemented deterministically.
- [ ] HOLD/DERISK/FULL EXIT implemented deterministically.
- [ ] Reset/new-family rule implemented.
- [ ] Episode risk budget invariants tested.
- [ ] No stop widening invariant tested.
- [ ] Closed-candle timing tested.
- [ ] PIT prefix invariance tested.
- [ ] MCR 90D/365D horizon firewall tested.
- [ ] Same-bar ambiguity policy tested.
- [ ] Candidate gates finalized before replay.
- [ ] Freeze guard confirms zero R2.0 replay output before freeze.
- [ ] R1.2/R1.3/R1.4/V2.6 untouched check passes.

## Historical diagnostic after freeze
- [ ] Verify canonical upstream artifact SHA.
- [ ] Run exactly one baseline R2.0 diagnostic replay before any variant.
- [ ] Report MCR90/365, Recall90/365, False Start, LONG/SHORT expectancy, capture/loss, lag and MDD.
- [ ] Run only predeclared robustness perturbations.
- [ ] Do not adopt best historical perturbation into the same frozen baseline.

## Production blockers
- [ ] Forward untouched OOS evidence mature.
- [ ] Full external Risk Governor.
- [ ] Concurrent portfolio risk/MDD validation.
- [ ] Exact V2.6 H2H if deterministic source becomes available; otherwise governance LINK N/A remains explicit.
