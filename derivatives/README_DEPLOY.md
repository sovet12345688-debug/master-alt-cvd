# Deployment note

- Collector venue priority: Bitget USDT Futures -> Bybit Linear fallback
- Universe: BTC + FINAL20 inherited from final20_config.json
- Schedule: every hour at minute 07
- Output: derivatives/output/latest_summary.json
- OI comparisons are venue-locked; no cross-venue delta comparisons
- MASTER integration remains separate and is not enabled by this deployment
