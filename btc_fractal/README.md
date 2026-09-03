# BTC Historical Regime & Outcome Engine V0.1

Purpose: build the independent historical-memory engine proposed for MASTER BTC TREND V2.6 **without changing MASTER BTC TREND execution gates or scores yet**.

## What this first implementation does

1. Pulls BTC daily price + community-accessible Coin Metrics on-chain metrics.
2. Pulls official U.S. Treasury 2Y/10Y/30Y and 10Y real yields where available.
3. Builds daily point-in-time-safe features using only information available up to each anchor date.
4. Builds a full Event Registry for +30/+50/+100/+150/+200% and -20/-30/-50/-70% targets over 30/90/180/365 days.
5. Computes first-passage direction (`+30% first` vs `-20% first`).
6. Finds diversified historical analogs; dates too close together cannot fill the Top-N list.
7. Separates similarity from outcome and confidence.
8. Runs an expanding walk-forward validation.
9. Produces a price-only baseline for the mandatory PASS/FAIL comparison.

## Deliberate limits in V0.1

- Historical case ratios are **not called probabilities** until calibration succeeds.
- GitHub Bitget OI/Funding is not forced into 2011+ history. It belongs to the later `modern-market` layer after enough comparable history exists.
- Spot BTC ETF flow is not forced into pre-2024 history. It belongs to the later `institutional-market` layer.
- Political/news events are not assigned arbitrary bullish/bearish points. They remain an external event/risk layer.
- Missing metrics are excluded and weights are renormalized; missing is never zero.

## Outputs

- `data/historical_raw_daily.csv`
- `data/historical_features_daily.csv`
- `data/event_registry.csv`
- `output/build_meta.json`
- `output/current_fractal.json`
- `output/current_price_only.json`
- `output/walk_forward.csv`
- `output/walk_forward_summary.json`

## Acceptance logic before MASTER integration

The complex model must beat or materially improve on the price-only baseline out of sample, especially for:

- +30% vs -20% first-passage direction
- high-confidence accuracy
- analog diversity
- false-positive control
- feature-removal stability (next validation patch)

If it does not improve the simple baseline, it must not be integrated into MASTER BTC TREND.
