# MASTER BTC TREND V3.0 — TREND CAPTURE ENGINE

Status: RESEARCH ONLY / NOT INTEGRATED / NO LIVE MASTER EFFECT
Branch: `btc-trend-v30-build`
Base snapshot: `main@d59cb7a5da947fa5faba9617daa7006f898f15b9`

## 1. Locked purpose
The engine is not optimized for asking "what must I buy today?". It must reconstruct the active medium/long-term trend, identify the most likely trend origin, estimate where the trend can travel next, and pre-register high-quality pullback/bounce entry zones so the user can place planned staggered orders without FOMO.

Primary questions:
1. Has a medium/long-term reversal/trend origin likely already formed?
2. If yes, around what date and price zone did the active trend most likely originate?
3. Is that origin the likely current-cycle low/high candidate?
4. Is the active trend in early, progressing, mature, or termination-risk stage?
5. What are the next T1/T2/T3 target zones?
6. At each target, how strong is the next reversal-risk evidence?
7. Is the current price a good entry, or should entry be pre-registered at future pullback/bounce zones?
8. For each planned entry zone: structure stop, R:R, allowed allocation, and add-on conditions.

## 2. Non-negotiable philosophy
- Never default to WAIT merely because price already rose or because current trend is still down.
- A downtrend may still allow small leading LONG exposure when bottom/reversal evidence is strong and structural risk is controlled.
- An uptrend may still allow small leading SHORT exposure when top/reversal evidence is strong and structural risk is controlled.
- NonChase is a sizing penalty, not an automatic zero-allocation switch except at extreme overextension/invalid R:R.
- A WAIT outcome must be typed: `PRE-REGISTERED_ENTRY_WAIT`, `CONFIRMATION_WAIT`, or `NO_TRADE`.
- `PRE-REGISTERED_ENTRY_WAIT` must include actual future entry zones; empty WAIT is not acceptable.
- Missed large trends are scored as failures, not as safe successes.

## 3. Core architecture
1. `TREND ORIGIN ENGINE` — identify the most likely origin date/price zone of the current active medium/long-term trend.
2. `CHART STRUCTURE ENGINE` — price structure, wave energy, candle force/pattern, moving-average structure, RSI, KDJ.
3. `FLOW & PARTICIPATION ENGINE` — volume, CVD, taker buy/sell, OI, funding, participation/absorption/distribution efficiency.
4. `HISTORICAL FRACTAL PATH ENGINE` — historical regime/path analogs from origin through 30/90/180/365D, including counter-fractals.
5. `TREND STATE ENGINE` — EARLY / PROGRESSING / MATURE / TERMINATION_RISK.
6. `TARGET & REVERSAL MAP ENGINE` — T1/T2/T3 reach score and target reversal-risk score separately.
7. `ENTRY MAP / RISK ENGINE` — pre-touch Zone Quality, post-touch Reaction Strength, SL, R:R, staggered allocation.
8. `TREND CAPTURE SYNTHESIZER` — choose strongest of LONG_REVERSAL / LONG_CONTINUATION / SHORT_REVERSAL / SHORT_CONTINUATION and size exposure.

## 4. Required chart analysis
Chart analysis is mandatory and may not be reduced to indicator snapshots.

### Price structure
HH/HL/LH/LL, failed new high/low, reclaim, breakdown, BOS/CHoCH, failed breakout/breakdown, role reversal, compression/expansion, retest quality, multi-timeframe transmission.

### Candle force
Body/range ratio, close location, upper/lower wick rejection/absorption, engulf/recovery fraction, volume support, next-candle persistence. Pattern names alone cannot determine direction.

### Wave energy
Price amplitude, duration, velocity, acceleration/deceleration, volume, CVD, OI, candle force, and price movement efficiency versus prior same-direction wave. Key idea: strong flow with diminishing price progress can imply absorption/exhaustion.

### Moving-average structure
Slope, ordering, compression/expansion, reclaim/failure, retest, distance/overextension, 1H→4H→1D transmission. Golden/death cross alone is insufficient.

### RSI/KDJ
Divergence, regime shifts, 50 reclaim/failure, oversold/overbought exit, swing-level progression. RSI/KDJ are momentum confirmation axes, not standalone direction engines.

### Volume/CVD/OI/Taker/Funding
Always interpreted jointly with price efficiency. Examples: heavy selling without new lows = absorption candidate; strong buying without progress = distribution/failed demand candidate; price↓+OI↓ = deleveraging; price↑+OI↑ = participation.

## 5. Four independent opportunity setups
Calculate all four every evaluation:
- LONG_REVERSAL
- LONG_CONTINUATION
- SHORT_REVERSAL
- SHORT_CONTINUATION

The strongest setup is selected only after Entry Quality and Path Quality are considered.

## 6. Setup score — reversal /100
| Axis | Points |
|---|---:|
| Historical fractal | 20 |
| Price structure | 15 |
| Wave energy | 15 |
| Candle force/pattern | 8 |
| MA structure | 8 |
| RSI+KDJ | 6 |
| Volume | 8 |
| CVD/Taker | 8 |
| OI/Funding | 7 |
| ETF/Macro | 5 |
| Total | 100 |

## 7. Setup score — continuation /100
| Axis | Points |
|---|---:|
| Price structure persistence | 20 |
| Wave energy persistence | 15 |
| MA structure | 12 |
| Candle force | 8 |
| Volume | 10 |
| CVD/Taker | 10 |
| OI/Funding | 8 |
| RSI/KDJ | 5 |
| Historical fractal path | 7 |
| ETF/Macro | 5 |
| Total | 100 |

Missing axes are N/A and valid weights are renormalized. The same raw fact may not be double-scored across subaxes.

## 8. ENTRY QUALITY /100
| Axis | Points |
|---|---:|
| Pre-touch Zone Quality | 30 |
| Post-touch Reaction Strength | 20 |
| Structural stop distance | 15 |
| Target-based R:R | 20 |
| NonChase / distance | 10 |
| Retest / liquidity location | 5 |
| Total | 100 |

If price has not touched a future zone, Reaction Strength is N/A and remaining weights renormalize. Pre-touch Zone Quality is explicitly required so planned limit-entry zones can be ranked before arrival.

## 9. TREND MATURITY /100
High is not good; it means late/termination risk.
| Axis | Points |
|---|---:|
| Price travel / overextension | 25 |
| Wave-energy exhaustion | 25 |
| Higher-TF resistance/support | 20 |
| RSI/KDJ divergence/heat | 10 |
| CVD/OI distribution/crowding | 15 |
| Blow-off volume | 5 |
| Total | 100 |

## 10. TARGET MAP
For each T1/T2/T3 calculate two separate scores:
- `TARGET_REACH_SCORE /100`: evidence the active trend can reach the zone.
- `TARGET_REVERSAL_SCORE /100`: evidence that, if reached, the zone may become the next medium/long-term reversal area.

Do not treat reach score as reversal score or vice versa.

## 11. TREND CAPTURE SCORE /100
Initial locked design for validation:
- Selected setup score: 45%
- Entry Quality: 30%
- Remaining path / target quality: 15%
- Inverse Trend Maturity: 10%

This is a quality score, NOT a probability.

## 12. Preliminary allocation map for backtesting
This mapping is a candidate to validate, not a live rule until OOS calibration:
- 0–49: 0%
- 50–59: up to 5%
- 60–69: 10–15%
- 70–79: 15–30%
- 80–89: 30–50%
- 90–100: 50–70%

Actual permitted allocation = MIN(score-based max allocation, structural-risk max allocation). Risk cap always wins.

Staged exposure model is required:
- Leading entry: small exposure when reversal evidence emerges before full trend confirmation.
- Confirmation add: increase after 1H/4H confirmation.
- Trend add: increase after higher-timeframe continuation/retest confirms.
- Never use all planned allocation on the first reversal clue.

## 13. Probability rules
Scores and probabilities are separate.
No `XX% probability` may be shown until out-of-sample calibration exists for that exact event definition and setup family.
Separate calibrated probabilities are required for:
1. Origin low/high formation.
2. Active trend continuation.
3. T1/T2/T3 reach.
4. Reversal at T1/T2/T3.
5. Entry TP-before-structural-SL outcome.

Every displayed probability must show sample count and OOS definition.

## 14. Historical fractal role
Fractal is a leading-prior layer, not a decorative read-only box and not a standalone trade trigger.
It must contribute to:
- Origin identification.
- Historical path after origin.
- Target travel expectations.
- Counter-fractal invalidation/failure paths.

Current price/flow/chart structure acts as live confirmation; R:R and structural risk determine whether capital is deployed.

## 15. Evaluation KPI — not accuracy-only
Primary KPI: `MOVE_CAPTURE_RATIO`.
Also required:
- Origin localization error.
- Detection lag after true low/high.
- Entry lead/lag.
- Expectancy.
- R:R realized.
- False-start loss.
- Missed Trend Rate.
- Max drawdown.
- LONG/SHORT separate performance.
- Reversal/continuation separate performance.
- Score monotonicity/calibration.
- Leave-one-cycle-out robustness.

A WAIT followed by a large unserved trend is a missed-trend failure.

## 16. Minimum promotion gates
Before any V3.0 live MASTER promotion:
- Move Capture Ratio >= 20% on locked OOS definition.
- Positive expectancy for LONG and SHORT separately.
- High-grade signals materially outperform lower-grade signals.
- Missed Trend Rate improves versus V2.6.
- Leave-one-cycle-out performance remains viable.
- No structural-risk budget breach.
- Probability calibration must be honest; insufficient sample => no probability.
- Shadow comparison versus V2.6 must be completed.
- Explicit user approval required before live MASTER replacement.

## 17. GitHub isolation contract
- V2.6 live MASTER prompt/schedule/plan must not be modified by this branch.
- `main` must not be written by V3.0 research workflows.
- Production collector directories (`derivatives/`, `live_flow/`, `market_vault/`, `market_whales/`) are read-only inputs from the research perspective.
- Existing `btc-fractal-v26-build` and PR #7 remain separate; no merge/rebase into V3.0 automatically.
- V3.0 may read validated findings/artifacts from the fractal/SR research branch, but must not silently inherit unvalidated claims.
- No PR merge, no live integration, no automation schedule modification without explicit user approval.
