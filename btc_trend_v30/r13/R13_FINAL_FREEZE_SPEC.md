# MASTER BTC TREND V3 R1.3 — PRE-OOS FINAL FREEZE

## 1. Status
- Model: MASTER BTC TREND V3 R1.3
- Status: RESEARCH BASELINE FROZEN BEFORE R1.3 OOS
- Parent R1.2 commit: `6b3158d7c289b36153b2d9dc87f810c3562d1515`
- Freeze time: 2026-09-07 13:50 KST
- V2.6 is untouched.
- R1.2 is frozen and must not be retuned from its OOS results.

## 2. Why R1.3 exists
R1.2 validated data/PIT/walk-forward/state-machine/metrics/robustness, but promotion HOLD was correct because trend capture was too low: MCR90 and MCR365 both failed the predeclared 20% target. R1.2 Step8 diagnosis also showed that exact Zone Touch and New Risk gating were major participation bottlenecks. These observations are used only to identify failure modes, not to select a best historical variant.

R1.3 therefore redesigns the execution architecture before any R1.3 OOS replay.

## 3. Core architectural change — TWO-PATH PARTICIPATION
R1.3 no longer requires every valid trend to return to the original causal zone before any participation is possible.

### Path A — RETEST
Purpose: preserve the high-quality R1.2 behavior when price actually retests the causal zone.

Frozen requirements:
- Risk State: OPEN or CAUTION
- Original causal Zone Touch required
- Closed 1H Reaction Score >=65
- Independent reaction reasons >=2
- Next-candle persistence required
- Safety Score >=80
- Non-chasing: entry no more than 1.50 Daily ATR beyond zone outer edge
- Structural stop: R1.2 zone/0.45ATR reference +0.35ATR buffer, mirrored by direction
- Stop distance <=15%
- Fixed research R:R = 3:1

### Path B — IGNITION
Purpose: participate in a genuine trend ignition before a full zone retest, but only while still underextended.

Frozen requirements:
- Risk State: OPEN only. CAUTION cannot use IGNITION.
- Original Zone Touch NOT required
- Closed 1H Ignition Quality >=70
- Independent reasons >=3
- Next 1H candle must preserve the breakout
- Non-chasing: entry no more than 0.75 Daily ATR beyond original zone outer edge
- Safety Score >=80
- Local structural stop required
- Stop distance <=15%
- Fixed research R:R = 3:1

Ignition Quality /100 is frozen as:
- prior-3 structure break: 25
- directional candle quality: 20
- EMA20 alignment: 15
- Volume ratio20 >=1: 15
- directional taker imbalance: 15
- daily transition alignment: 10

Breakout persistence uses the breakout reference with a 0.25*1H ATR tolerance. The local structural stop is anchored to the prior-3 opposite swing with a 0.25*1H ATR invalidation buffer, mirrored for SHORT.

This 0.75 Daily ATR ignition chase limit is a clean pre-OOS design choice equal to one-half of the old 1.50 ATR general chase allowance. It was not selected from an R1.3 OOS grid.

## 4. New Risk Gate redesign — OPEN / CAUTION / BLOCK
R1.2 used `new_risk_ok` as a binary serial gate. R1.3 replaces it with route control.

### OPEN
- No transition conflict
- No legacy caution flag
- RETEST and IGNITION both allowed

### CAUTION
Any legacy warning is present, but no transition conflict:
- FALSE_BOTTOM_RISK / FALSE_TOP_RISK
- long_overextended / short_overextended

CAUTION no longer means automatic exclusion. It means:
- RETEST allowed after full confirmation
- IGNITION forbidden

This preserves a route for valid trends that later prove themselves at the zone without allowing aggressive early participation in a risky/extended state.

### BLOCK
- TRANSITION_CONFLICT, or
- confirmed Severe Risk Veto

No entry route is allowed.
Historical severe-event reconstruction that is unavailable stays N/A and may not be silently converted to PASS for production promotion.

## 5. State machine
`SCOUT 0% -> ARMED -> RETEST_CONFIRM or IGNITION_CONFIRM -> 1H_ENTRY -> optional separate 4H_ADD -> 1D_CONFIRM_LOG_ONLY`

Rules:
- Earliest clue stays observe-only.
- LC/SC Stage1 remain WATCH ONLY.
- 4H add is never automatic; it needs an independent confirmation and the same risk-state/route matrix.
- 1D confirmation is logged only; no third allocation leg is invented.
- No protected-level or stop-migration rule is invented.

## 6. Unchanged hard principles
- Point-in-Time only
- No future features
- Closed candles only
- Causal Zone Family dedupe
- Fractal excluded from core score
- Score is not probability
- Structural stop mandatory
- Non-chasing mandatory
- R:R >=3:1
- same-bar TP+SL => AMBIGUOUS
- no OOS retuning
- V2.6 untouched

## 7. Predeclared validation gates
These are locked BEFORE R1.3 OOS:
- MCR 90D mean >=20%
- MCR 365D mean >=20%
- LONG expectancy >0
- SHORT expectancy >0
- capture/stop-loss ratio >1
- Confirmed false-start rate <=50% of executed first-1H-entry episodes with complete 90D truth
- Score monotonicity: minimum 10 observations per comparable bin; insufficient sample => UNRESOLVED, never forced PASS/FAIL
- Robustness battery required; no best variant may replace the frozen baseline
- Cycle independence required
- Full Risk Governor replay required for production promotion
- Exact V2.6 H2H remains required by current governance; if still non-replayable it remains UNRESOLVED, never replaced by a proxy

## 8. OOS firewall
R1.3 OOS MUST NOT be executed before this freeze.

Because R1.3 was motivated by R1.2 OOS diagnosis, a later historical R1.3 replay over the old 2021-2026 period is useful only as DIAGNOSTIC evidence. It is not clean untouched promotion evidence.

The new untouched forward OOS starts at:
`2026-09-05 00:00:00 UTC`

30D/90D/365D truth labels become valid only after their full horizons mature. Until then, production promotion cannot claim those forward gates as PASS.

## 9. Freeze authority
The canonical machine-readable rules are in:
`btc_trend_v30/r13/r13_frozen_config.json`

Canonical config SHA256:
`b417282c66bec49c1c5de672f8317635508eba1dd05f538074a1508f14217fa9`

Any rule/threshold change creates a NEW baseline/version and requires a new freeze plus untouched evidence. It may not be called the same frozen R1.3 baseline.
