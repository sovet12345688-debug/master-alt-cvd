# MASTER MARKET DATA VAULT V1.1

Purpose: preserve same-source historical observations so MASTER MARKET can compare `prior snapshot / 24H / 7D` without reconstructing past values from memory or mixing sources.

## Role
- Historical comparison cache only.
- Not a new score.
- Not a replacement for direct current-market verification.
- A stale/missing Vault metric must never block MASTER MARKET.

## Validated keyless sources
Mandatory core:
- U.S. Treasury official XML: 2Y / 10Y / 30Y nominal Treasury yields.
- U.S. Treasury official real-yield XML: 10Y real yield.
- Federal Reserve Bank of New York Markets API: EFFR.
- U.S. Treasury Fiscal Data Daily Treasury Statement: TGA closing balance.

Secondary:
- New York Fed Markets API: SOFR.

Optional context:
- DefiLlama Stablecoins: USDT / USDC / total stablecoin supply.
- CoinGecko Global: total crypto market cap, 24H volume, BTC/ETH dominance.

FRED was tested from GitHub-hosted U.S. runners and excluded from production V1.1 because repeated requests timed out. Fed balance-sheet/reserve balances, DXY, oil and equity indexes remain direct-source checks inside MASTER MARKET until a reliable GitHub-accessible adapter is separately validated. ETF flows are also intentionally excluded until a stable free source is separately validated.

## Data model
`data/market_history.csv` stores one metric row per GitHub snapshot with the source observation timestamp and timestamp quality.

`output/latest_summary.json` computes comparisons only when the metric AND source match. Cross-source deltas are forbidden.

## Safety
- Current MARKET verification remains primary. Vault is historical context only.
- Repeated hourly retrieval of the same daily official observation does not imply a new move.
- `new_source_observation_since_prior_snapshot` distinguishes a real source update from a repeated snapshot.
- Missing values are not zero-filled.
- 24H / 7D comparisons remain null until actual history exists within tolerance.
- A source failure removes only that metric from Vault; it must never stop MASTER MARKET.
