# MASTER MARKET DATA VAULT V1

Purpose: preserve same-source historical observations so MASTER MARKET can compare `prior snapshot / 24H / 7D` without reconstructing past values from memory or mixing sources.

## Role
- Historical comparison cache only.
- Not a new score.
- Not a replacement for direct current-market verification.
- A stale/missing Vault metric must never block MASTER MARKET.

## V1 sources
- FRED official CSV: Fed balance sheet/reserves/TGA, Fed funds, US 2Y/10Y/30Y, 10Y real yield, broad dollar index, WTI, Brent, S&P 500, Nasdaq.
- DefiLlama Stablecoins: USDT/USDC/total stablecoin supply (optional).
- CoinGecko Global: total crypto market cap, 24H volume, BTC/ETH dominance (optional).

ETF flows are intentionally excluded from V1 until a stable free source is separately validated.

## Data model
`data/market_history.csv` stores one metric row per GitHub snapshot with source observation timestamp and timestamp quality.

`output/latest_summary.json` computes comparisons only when the metric AND source match. No cross-source delta is allowed.

## Safety
- Different-frequency macro series remain labeled daily/weekly; repeated hourly snapshots do not imply a new official observation.
- `new_source_observation_since_prior_snapshot` distinguishes an actual source update from a repeated retrieval of the same observation.
- Missing values are not zero-filled.
- 24H/7D comparisons remain null until actual history exists within tolerance.
