# MASTER BTC TREND V3.0 — RESTART PHASE1 SPEC

Status: RESEARCH ONLY / NOT LIVE / V2.6 FROZEN

## Purpose
Detect major BTC trend birth early enough to capture the body of the move, while penalizing missed trends and limiting false-start losses. The engine must not optimize hit-rate by deleting signals.

## Phase1 research questions
1. Where did a meaningful medium/long-term trend most likely originate?
2. Can the origin be detected with useful lead/lag and localization, not merely after the trend is obvious?
3. Does historical fractal information improve ranking as a soft prior rather than a hard gate?
4. Can future reservation zones be ranked before touch using only point-in-time structure?

## Truth / outcome principles
- LOW truth: meaningful local low followed by +30% before -15% within 180D.
- HIGH truth: meaningful local high followed by -25% before +15% within 180D.
- Truth labels may use future bars only for evaluation; no future-derived feature enters a signal.
- Signals are evaluated as episodes, not daily labels.

## Origin architecture
Two-stage, but not all-or-nothing:
- Candidate map: loose/continuous exhaustion + location + structure + wave/candle/flow evidence to preserve recall.
- Confirmation map: reclaim/retest/structure turn/MA momentum/flow turn.
- Candidate origin time and confirmation time are retained separately.

## Fractal modes compared OOS
- NONE: no fractal.
- SOFT: small additive prior around neutral 50.
- RANK: fractal contributes as rank evidence, never deletes a candidate.
- INTERACTION: fractal boosts a strong base candidate more than a weak one, without a hard cutoff.
Only prior analogs whose 180D outcome was fully knowable before the query may be used.

## Origin evaluation
Report separately:
- precision
- recall / missed-trend rate
- median confirmation lag
- median origin localization error
- economic first-passage success from confirmation
- composite research utility for method selection only

No single precision-only objective.

## Pre-touch Zone Quality redesign
Candidate zone evidence may include only information available at registration:
- prior confirmed swing/pivot density
- role reversal evidence
- freshness / untouched-ness
- repeated-test weakening penalty
- displacement-origin strength
- higher-timeframe / MA / pivot overlap
- prior reaction efficiency resolved before registration
- ATR/distance feasibility

Reach and reaction are separate targets. A zone that is never reached is not treated as a failed reaction.
Reaction success uses a fixed structural 3R-before-1R first-passage rule for research comparison.

## Anti-overfit
- prior-year walk-forward only
- no test-year threshold fitting
- no probability labels before calibration
- no synthetic OI/Funding history
- no production writes
- no V2.6 changes

## Phase1 research gate
A candidate architecture must, at minimum:
- generate non-zero LONG and SHORT OOS signals
- materially reduce missed trend versus precision-only behavior
- retain useful origin localization and detection lag
- show positive economic utility in selected signals
- show soft-fractal utility vs no-fractal without signal extinction
- show Zone Quality OOS separation/monotonic tendency with sufficient sample

Exact thresholds are research gates, not future MASTER score definitions.
