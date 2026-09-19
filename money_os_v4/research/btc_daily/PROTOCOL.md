# BTC daily Trading Room Challenger — frozen study protocol

User instruction: 2026-09-18. Add Trading Room to V4 as W5; seek net win rate >=
50%, realized payoff >=2, and first-entry fill rate within 24h >=50%.
These are acceptance targets, not assumed facts or optimization guarantees.

## Scope and denominators

- BTCUSDT Binance USD-M futures only. Public OHLCV and actual historical funding.
- Warm-up October–December 2023; train calendar 2024; selection/validation 2025;
  final fixed-model check 2026-01-01 through the available September cutoff.
- Data cutoff: 2026-09-18 00:00 UTC, the last available full day at study start.
  September 18–30 is not synthesized. Report September as partial.
- Only plans with a complete 24h entry window plus 24h maximum holding window
  before the applicable split boundary are evaluated. Two-day split embargo.
- Net win rate = profitable closed plans / all filled closed plans, including
  zero-PnL plans in the denominator; an E1+E2 plan is one trade.
- Realized payoff = mean positive plan PnL / absolute mean negative plan PnL.
- 24h fill rate = plans with at least E1 filled within 24h / all issued matured
  plans. Unfilled, invalidated and cancelled plans stay in the denominator.
  Report E2/full-risk fill separately and never substitute it for first fill.
- Report plans per day and calendar-day coverage as well as the three targets.
  No retrospectively selecting only filled orders or successful days.
- At most one outstanding plan/position per BTC. No private account sizing.

## Fixed candidate space (216 variants)

Three setup families: trend pullback, local breakout/retest, level sweep/reclaim.
All use completed 1H structure with 4H trend context and 15m event execution.
Cross product: family (3) × 1H stop lookback (1/3/6 bars) × structural reaction
entry depth (25/50/75% of reaction low-to-close or high-to-close) × quality
(basic/strong) × exits (full at TP3 / 20%-30%-50% at TP1-TP2-TP3) × holding
budget (12h/24h). 216 total; no new grids after inspecting the final holdout.

Targets are three distinct, already confirmed 4H pivots on the favorable side.
5-bar pivots become available only after the two right-hand bars close. Use
the last 90 days, group levels within 0.1 current 1H ATR, and take the nearest
three distinct levels. If there are fewer than three, issue no plan.
No synthetic fixed-R targets for the challenger. Stop is the adverse extreme
of the fixed 1H lookback including the reaction bar, buffered by 0.1 ATR.
Reject stops under 0.20 1H ATR or over 6% of entry, and extended entry zones.

Basic quality requires 4H context not opposing the setup, favorable completed
reaction candle, and completed 1H relative volume >=0.8. Strong adds 4H trend
alignment/slope and direction-aligned taker imbalance, relative volume >=1.0.
Entry is a pullback limit inside the completed reaction candle. This is a
new deterministic Challenger operationalization, not a claim of exact manual
Champion parity. Wave Energy and Pattern remain context-only, weight zero.

## Two-stage execution and costs

- E1 uses 40% of an abstract one-unit risk budget, quantity computed from
  structural stop distance plus assumed entry/stop costs.
- E2 uses the remaining 60% only after a completed 15m reaction and a later
  completed hold candle, followed by a separate future retest fill. Trigger
  PASS is never immediate market ADD. No addition after a target was visited.
- Recheck cost-aware structural R:R >=3 at issue and at E2. This is separate
  from the requested realized-payoff target >=2; do not lower canonical 3:1.
- Every fill incurs 0.06% fee and 0.02% adverse execution-cost allowance per
  side, including limit fills. Ideal fills remain bounded by the limit;
  slippage is debited as a separate cost. Funding uses historical rate × exact
  archived REST mark × signed held quantity. No future funding used as signal.
- Planning uses the last known absolute funding rate as a conservative 24h
  funding allowance; actual PnL uses subsequent realized settlements.
- Orders begin after the signal/confirmation close, with 15m execution latency.
  Require 0.1 USD penetration of a limit to avoid optimistic touch-only fills.
- Gap-through invalidation before entry cancels the plan; entry and stop in one
  bar are treated as fill then stop. For simultaneous stop/TP, stop comes first.
- Position clocks start at first fill. Fixed daily order/holding budgets are a
  BTC_DAILY profile choice, not a rewrite of legacy rolling Time Validity.
  Daily order expiry and structural invalidation remain different fields.
- Rolling revalidation may cancel an unfilled plan on a newly closed adverse
  4H context. No widening stop or extending timeframe after a loss.

## Selection and acceptance

Store every variant's train/validation result. Eligible selection needs at least
60 filled train plans and 60 validation plans, positive net expectancy in both,
and fill rate >=50% in both. Rank by the minimum normalized attainment of the
win/payoff/fill targets across train and validation; then by the lower mean R,
then sample size, then deterministic config ID. If none eligible, report the
best diagnostic candidate under the same attainment ranking with adequate
samples. Do not declare adoption solely because a candidate is the best.

Freeze exactly one configuration before opening its 2026 results. Also provide
the original closed-candle BTC price-proxy baseline on the identical intervals.
The historical current manual room still has no complete official signal ledger.
Its measured performance remains N/A; the baseline must be labeled price proxy.

Success requires all three targets on BOTH 2025 validation and 2026 check, at
least 40 filled 2026 plans, and positive net expectancy. Show train and full-period
metrics too. Weekly/monthly block uncertainty, doubled-slippage stress, direction
and subperiod diagnostics accompany the result. Previously seen market history
is not called a new untouched OOS. A failed target stays failed; no silent goal
relaxation, cherry-picked direction, backfilled signals or deployment.

## V4 integration and rollout

W5 TRADING ROOM / EXECUTION remains the sole plan owner. Navigator issues typed
requests and may maintain or downgrade W5 action. W5 emits alert_candidate only;
Alert Router handles delivery after Final Navigator. No new W6 competing owner.
Production activation, main merge, trading permissions and legacy shutdown are
not part of this study. Add code, schema, handoff and reproducible evidence to an
isolated development branch. Keep Champion available and history append-only.
