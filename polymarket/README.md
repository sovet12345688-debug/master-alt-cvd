# MASTER MARKET Polymarket Expectation Radar V1

Purpose: collect public, unauthenticated Polymarket market data for MASTER MARKET SCREEN5.

- Source: Polymarket Gamma API + CLOB API + Data API.
- No API key, wallet, signer, or trading authentication is required for these read-only endpoints.
- Output: `polymarket/output/latest_summary.json`
- State: `polymarket/state/collector_state.json`
- History: `polymarket/data/hourly_top10.csv`
- Schedule: hourly at minute 43 UTC via GitHub Actions.
- TOP10 selection: market-impact relevance + volume/liquidity/spread quality + theme diversification.
- Themes: Fed/inflation, geopolitics/oil, crypto price, US macro, crypto policy.
- Excludes sports, entertainment, and general election-winner markets unless a direct market-impact keyword match exists.
- Deltas: Polymarket CLOB price history for 1H/4H/1D/7D, in percentage points.
- Score weight: **0**. It is forward-expectation context only.
- WATCH: Polymarket alone never sends an alert. An A/B-grade probability move >=10pp/4H or >=15pp/1D becomes a candidate only when an independent MASTER axis confirms the same direction.
