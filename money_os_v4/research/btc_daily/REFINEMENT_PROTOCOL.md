# One bounded pre-holdout refinement

The original 216 variants and selection lock remain preserved. None was eligible
for adoption: four had >=60 trades in both train/validation, and none combined
positive expectancy with required fill and win/payoff targets. Apparent 50%
win rates came from only a few observations.

Before any 2026 candidate-result evaluation, add exactly 36 variants:
family (3) × structural stop lookback (3/6/12 H1 bars) × entry depth (50/75%) ×
exit (FULL/SCALED), BASIC quality, 24h hold, CONFIRMED E1.

CONFIRMED E1 first waits for completed 15m reaction near the planned zone, a later
completed hold, then creates a retest order executable after 15m latency. Entry
may improve to the already declared zone boundary, only if its cost-aware R:R
remains >=3. A pre-fill structural break cancels the order and remains in the
24h-fill denominator. E2 still requires its own later reaction/hold/retest.
The 40/60 risk budget, structural targets, fee/funding rules, sample minimums and
acceptance targets are unchanged. No projected/fabricated target levels added.

This is exploratory refinement using 2024 and 2025, not an independent test of
those years. Total variants examined =252. Re-rank the combined 252 under the
original fixed selector; freeze one final config; run the 2026 check once.
No further model search after the 2026 result, even if no configuration qualifies.
