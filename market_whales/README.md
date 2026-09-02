# MASTER MARKET Whale Time-Series Recorder V1

Purpose: build a read-only hourly history of large BTC/ETH positions from public Hyperliquid data for later MASTER MARKET use.

## Sources
- Hyperliquid public leaderboard: candidate discovery only.
- Hyperliquid official `/info` API `clearinghouseState`: current position, entry, leverage, liquidation price, unrealized PnL and account value.
- Hyperliquid official `/info` API `allMids`: current BTC/ETH mark reference for liquidation-distance calculation.

No API key, wallet private key, or trading permission is used.

## Thresholds
- >= $5M current position: internal history/scout storage.
- >= $20M new/increase/reduce/close/flip magnitude: MARKET large-change candidate.
- >= $50M change magnitude: LEVEL1 candidate.

These are observation/event thresholds, not trading signals.

## Discovery
Each run selects a capped union of high-account-value and high-PnL/volume public leaderboard accounts, then re-queries previously tracked wallets during a retention window so closures are not lost.

## Output
- `data/positions_history.csv`: 30-day hourly position history.
- `output/latest_summary.json`: BTC/ETH top positions, 1H/4H/24H changes, recent series, scout tier.
- `output/latest_events.json`: large position-change events.
- `state/recorder_state.json`: coverage, failures and tracked-wallet state.

## Safety
- Public leaderboard `displayName` is never treated as verified real-world identity.
- Missing API data never means a position is closed.
- A closure is recorded only after a successful wallet query shows the position absent/below the storage threshold.
- MASTER MARKET scoring is not modified by this development branch.
