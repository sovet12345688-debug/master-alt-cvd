# BTC Historical Regime & Outcome Engine V0.2

Purpose: build and validate the independent historical-memory engine proposed for MASTER BTC TREND V2.6 **without changing MASTER BTC TREND execution gates, scores, schedule, or live plan**.

## V0.2 validation order

1. Rebuild the confidence engine so mixed 8:4-style historical outcomes cannot be labeled HIGH confidence.
2. Replace simple date spacing with point-in-time swing Episode diversification so one cycle cannot fill the analog list with nearby dates.
3. Expand free on-chain inputs where mathematically defensible. `CapMrktCurUSD` + `CapMVRVCur` are used to derive realized-cap history when available; `MVRVZ_DERIVED_PIT` is explicitly a point-in-time proxy, not a vendor-reported official MVRV Z-Score.
4. Run domain-removal stability tests for price, on-chain, and macro inputs.
5. Re-run expanding walk-forward validation against the same price-only baseline.
6. Build modern-market derivatives and institutional ETF layers only if the full-history complex model passes the locked out-of-sample acceptance gate.

## What the engine does

- Pulls BTC daily price + community-accessible Coin Metrics on-chain metrics.
- Pulls official U.S. Treasury 2Y/10Y/30Y and 10Y real yields where available.
- Builds daily point-in-time-safe features using only information available up to each anchor date.
- Builds a full Event Registry for +30/+50/+100/+150/+200% and -20/-30/-50/-70% targets over 30/90/180/365 days.
- Computes first-passage direction (`+30% first` vs `-20% first`).
- Finds historical analogs with one analog per point-in-time-defined swing Episode.
- Separates similarity, outcome, and confidence.
- Runs expanding walk-forward validation.
- Produces a price-only baseline for mandatory PASS/FAIL comparison.
- Produces current and walk-forward ablation audits.

## Confidence V0.2

HIGH confidence is intentionally difficult. It requires all of the following, not merely a large analog count:

- score >= 75
- >=10 directional first-passage cases
- >=5 distinct historical years
- >=8 independent Episodes
- majority share >=80%
- conservative Wilson lower bound >=58%

A 66.7% majority such as 8 vs 4 is therefore not allowed to appear as HIGH confidence.

## Episode rule

Episode assignment is point-in-time only. No future price path is used to retroactively relabel a historical date.

- 20% reversal confirms a new opposite-direction Episode.
- A 365-day maximum duration forces very long regimes to split.
- Only one analog can represent an Episode in Top-N selection.

## Deliberate limits

- Historical case ratios are **not probabilities** until calibration succeeds.
- GitHub Bitget OI/Funding is not forced into 2011+ history.
- Spot BTC ETF flow is not forced into pre-2024 history.
- Missing metrics are excluded and weights are renormalized; missing is never zero.
- `MVRVZ_DERIVED_PIT` is a transparent proxy and must never be presented as an official vendor MVRV Z-Score.

## Outputs

- `data/historical_raw_daily.csv`
- `data/historical_features_daily.csv`
- `data/event_registry.csv`
- `output/build_meta.json`
- `output/current_fractal.json`
- `output/current_price_only.json`
- `output/ablation_current.json`
- `output/ablation_walk_forward_summary.json`
- `output/walk_forward.csv`
- `output/walk_forward_price_only.csv`
- `output/walk_forward_summary.json`
- `output/model_decision.json`

## Acceptance logic before MASTER integration

The complex model passes only if all required gates are met:

- overall OOS accuracy >= price-only +1.0 percentage point
- enough HIGH-confidence OOS cases exist
- HIGH-confidence accuracy >= price-only overall accuracy +2.0 percentage points
- max on-chain/macro removal direction-flip rate <=10%

If it does not pass, the correct architecture is **price core + on-chain/macro confirmation**, and modern derivatives/ETF layers are not built yet.
