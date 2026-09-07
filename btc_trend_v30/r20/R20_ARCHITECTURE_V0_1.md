# MASTER BTC TREND V3 — R2.0 INDEPENDENT TREND CAPTURE ENGINE

## Status
- Branch: `btc-trend-v30-r20-design`
- Design stage: V0.1 architecture only.
- NO R2.0 historical replay has been performed.
- R1.2/R1.3/R1.4/V2.6 remain untouched.
- R2.0 is not an R1.x threshold patch. It is a new trend-participation architecture.

## 1. Primary objective
R2.0 optimizes for **early participation in durable BTC trends and sustained capture**, not for maximizing short-term +3R trade frequency.

Primary questions:
1. Did the engine recognize a genuine directional trend early enough?
2. Did it build exposure only as the trend proved itself?
3. Did it stay in the trend long enough to capture a material fraction of MFE?
4. Did it avoid repeatedly reusing a failed thesis?

Primary KPI: MCR 90D / 365D.
Secondary KPIs: trend recall, false-start rate, directional expectancy, capture/loss ratio, entry lag, risk-weighted drawdown.

## 2. Core architecture
R2.0 separates four engines:

1. `REGIME ENGINE` — completed 1W + 1D permission/context.
2. `INITIATION ENGINE` — 1D + 4H early trend ignition detection.
3. `PARTICIPATION ENGINE` — deterministic exposure state machine.
4. `HOLD / EXIT ENGINE` — trend persistence, de-risk and exhaustion exit.

LONG and SHORT are separate independent machines. They do not share a final score or symmetric permission contract.

## 3. Data contract
Core reproducible inputs only:
- BTCUSDT spot OHLCV: completed 1D, 4H, 1H.
- Completed weekly bars resampled causally from daily data.
- EMA20, EMA50, ATR14, 20-bar prior high/low, volume ratio versus prior 20 completed bars.
- No derivatives, CVD, funding, ETF, whale or macro field is required for the historical core.

Optional external Risk Governor variables may later veto risk but may not create a trend signal.

Rules:
- completed candles only;
- all rolling features shifted so the current decision uses only information available at that timestamp;
- no future labeling variable is available to the live state machine;
- same-bar target/stop ambiguity is not resolved optimistically.

## 4. State machines
### LONG
`L0_NEUTRAL -> L1_WATCH -> L2_SEED -> L3_CONFIRMED -> L4_CORE -> L5_HOLD -> L6_DERISK -> L0_EXIT`

### SHORT
`S0_NEUTRAL -> S1_WATCH -> S2_SEED -> S3_CONFIRMED -> S4_CORE -> S5_HOLD -> S6_DERISK -> S0_EXIT`

A stopped/invalidated state must return to NEUTRAL and satisfy a reset condition before a new trend episode may begin. No automatic same-episode re-entry.

## 5. LONG engine V0.1
### L1 WATCH — early trend setup
All must be true on completed data:
- Daily close > Daily EMA20.
- Daily EMA20 slope over the prior 5 completed sessions > 0.
- Daily close is within 1.0 ATR14 below the prior completed 20D high OR has closed above it.
- 4H close > 4H EMA20 and 4H EMA20 slope positive.

Volume is supportive, not mandatory at WATCH.

### L2 SEED — initial participation
Requires L1 plus either path:

**LONG BREAKOUT**
- completed 4H close > prior completed 20-bar 4H high;
- volume ratio >= 1.20;
- breakout close is in the upper 40% of its own range;
- no daily close below Daily EMA20 on the seed date.

**LONG RECLAIM**
- price traded below Daily EMA20 within the prior 5 completed sessions;
- current completed Daily close reclaims above Daily EMA20;
- current Daily close > prior day's high;
- daily volume ratio >= 1.10.

Seed risk budget: 0.35R.
Initial structural stop: below the lower of the causal 4H swing low and entry minus 1.5 x 4H ATR14, capped by a maximum initial stop distance of 12%.
If a valid structural stop cannot be defined, no seed.

### L3 CONFIRMED — trend proof
Within 5 completed daily sessions after SEED, all must occur before invalidation:
- Daily close remains above Daily EMA20;
- at least one Daily close > the SEED-day high;
- Daily EMA20 remains rising;
- no completed Daily close returns below the pre-seed breakout/reclaim reference level by more than 0.5 Daily ATR14.

Confirmation adds 0.35R risk budget.
If confirmation does not occur within 5 sessions, no add; SEED remains managed by the exit engine.

### L4 CORE
Requires L3 plus:
- completed weekly close is above weekly EMA20 OR weekly EMA20 slope is non-negative;
- Daily close > Daily EMA50;
- Daily EMA20 > Daily EMA50 OR Daily EMA20 has risen for 10 completed sessions while price is above EMA50.

Core transition adds final 0.30R risk budget.
Total episode risk budget cap = 1.00R.

## 6. SHORT engine V0.1
SHORT is intentionally stricter because BTC has structural long-run positive drift.

### S1 WATCH
All must be true:
- Daily close < Daily EMA20.
- Daily EMA20 slope over the prior 5 completed sessions < 0.
- Daily close is within 1.0 ATR14 above the prior completed 20D low OR has closed below it.
- 4H close < 4H EMA20 and 4H EMA20 slope negative.

### S2 SEED
Requires S1 plus both regime and trigger permission.

Regime permission:
- completed weekly close < weekly EMA20 OR weekly EMA20 slope < 0;
- Daily close < Daily EMA50.

Trigger, either path:

**SHORT BREAKDOWN**
- completed 4H close < prior completed 20-bar 4H low;
- volume ratio >= 1.25;
- breakdown close is in the lower 40% of its range.

**SHORT FAILED-RETEST**
- a prior completed breakdown below Daily EMA20 or the prior 20D low occurred within 5 sessions;
- price retested that reference from below;
- completed 4H close returned below the reference;
- 4H close < 4H EMA20;
- volume ratio >= 1.10.

Seed risk budget: 0.30R.
Structural stop: above the higher of the causal 4H swing high and entry plus 1.5 x 4H ATR14, capped at 10% initial stop distance.

### S3 CONFIRMED
Within 4 completed daily sessions after SEED:
- Daily close remains below Daily EMA20;
- at least one Daily close < the SEED-day low;
- Daily EMA20 remains falling;
- no completed Daily close reclaims the breakdown reference by more than 0.5 Daily ATR14.

Confirmation adds 0.30R.

### S4 CORE
Requires:
- Daily EMA20 < Daily EMA50;
- Daily EMA50 slope over the prior 10 completed sessions <= 0;
- completed weekly close < weekly EMA20.

Core adds final 0.25R.
SHORT total episode risk budget cap = 0.85R in V0.1. The remaining 0.15R is intentionally unused rather than forced into the market.

## 7. Holding engine — no fixed profit target
R2.0 has no mandatory +3R full exit.
The purpose is trend capture.

For each active fraction:
- hard structural stop always exists;
- the stop may tighten but never widen;
- no break-even move is mandatory solely because price reached a fixed R multiple.

### LONG HOLD
Remain in HOLD while:
- Daily close >= Daily EMA20, OR
- one Daily close falls below EMA20 but 4H structure has not produced a confirmed lower-low/lower-high reversal.

DERISK 50% of current exposure when both occur:
- completed Daily close < Daily EMA20;
- completed 4H structure confirms a lower high followed by a lower low.

FULL EXIT when any occurs:
- two consecutive completed Daily closes < Daily EMA20 while EMA20 slope <= 0;
- completed Daily close < Daily EMA50;
- Chandelier stop: highest completed Daily close since SEED - 3.0 x current Daily ATR14 is breached on a completed Daily close;
- severe external Risk Governor veto when available.

### SHORT HOLD
Mirror directionally:
DERISK when Daily closes above EMA20 + 4H higher-low/higher-high reversal.
FULL EXIT on two consecutive Daily closes > EMA20 with non-negative EMA20 slope, Daily close > EMA50, or 3.0 ATR Chandelier reversal from the lowest completed Daily close since SEED.

## 8. Thesis reset / no revenge re-entry
After structural stop or FULL EXIT:
- return to NEUTRAL;
- a new episode requires at least 3 completed Daily sessions OR a new causal 20D breakout/reclaim family, whichever is later;
- prior route, prior stop, and prior confirmation state cannot be inherited.

This is specifically designed to prevent R1.x failed-thesis reuse.

## 9. Exposure accounting
LONG maximum episode risk:
- SEED 0.35R
- CONFIRM +0.35R
- CORE +0.30R
- total <= 1.00R

SHORT maximum episode risk:
- SEED 0.30R
- CONFIRM +0.30R
- CORE +0.25R
- total <= 0.85R

No additional 4H ADD layer exists outside these state transitions.

## 10. Truth and KPI definitions
R2.0 keeps the existing future-only truth labels for comparability, but performance is evaluated at the trend-episode level.

Required metrics:
1. MCR90 mean on complete medium truth-positive episodes.
2. MCR365 mean on complete long truth-positive episodes.
3. Trend Recall90 = truth-positive episodes with any SEED before horizon / complete truth-positive episodes.
4. Trend Recall365 similarly.
5. False Start = SEED episodes that hit structural invalidation before entering CONFIRMED and have no medium truth trend.
6. LONG risk-weighted expectancy per entered episode.
7. SHORT risk-weighted expectancy per entered episode.
8. Capture/Loss ratio.
9. Median trend-entry lag from causal episode start to SEED.
10. Risk-weighted episode-order MDD; later replaced/supplemented by true concurrent portfolio MDD.

MCR numerator uses only price movement captured by actually active exposure before the respective horizon. Post-horizon exits or marks are forbidden.

## 11. Predeclared candidate gates for FINAL FREEZE
These are candidate gates and must be frozen before the first R2.0 historical replay:
- MCR90 mean >= 20%.
- MCR365 mean >= 20%.
- Trend Recall90 >= 55%.
- Trend Recall365 >= 55%.
- LONG expectancy > 0R.
- SHORT expectancy > 0R.
- confirmed False Start <= 45%.
- Capture/Loss ratio > 1.10.
- median SEED lag <= 7 days on complete truth-positive episodes that were captured.
- no direction or major cycle bucket may have expectancy < -0.15R with n>=10.
- robustness, PIT invariance, cycle independence and score/state monotonicity required.
- Full Risk Governor and forward untouched OOS remain production blockers.
- exact V2.6 H2H remains LINK N/A until a deterministic V2.6 replay source exists.

## 12. Evidence firewall
R2.0 is designed after R1.x historical diagnosis. Therefore any replay of 2021-01-01 through 2026-09-04 is historical diagnostic evidence only.
It cannot promote R2.0 to production.
Untouched forward evidence starts no earlier than 2026-09-05 UTC and matures only as the required horizons complete.

## 13. What R2.0 deliberately does NOT do
- no R1.4 4H ADD reuse;
- no fixed +3R full-profit architecture;
- no shared LONG/SHORT score;
- no same-episode automatic re-entry after failure;
- no derivatives requirement in the historical core;
- no threshold grid search before freeze;
- no choosing a best perturbation after viewing R2.0 replay.

## 14. Next implementation gate
Before any historical replay:
1. encode this contract in canonical JSON;
2. implement LONG/SHORT state machine deterministically;
3. add synthetic contract tests for every transition and reset;
4. add PIT prefix-invariance tests;
5. generate canonical config SHA256;
6. FINAL FREEZE;
7. only then run one accelerated historical diagnostic replay on the verified canonical baseline artifact.
