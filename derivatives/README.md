# MASTER DERIVATIVES HISTORY ENGINE V1

Purpose: collect hourly Binance USD-M perpetual open interest and funding snapshots for BTC + MASTER ALT FINAL20, store compact history, and publish MASTER-readable 1H/4H/24H change summaries.

This module is isolated under `derivatives/` while it is tested. It does not alter the existing CVD engine on `main`.

Planned outputs:
- `derivatives/data/hourly_derivatives.csv`
- `derivatives/output/latest_summary.json`
- `derivatives/output/latest_summary.csv`
- `derivatives/state/collector_state.json`

Data source policy:
- Binance USD-M public market-data endpoints only.
- No API key or account credentials.
- OI and funding are market-wide Binance venue observations, not global derivatives totals.
- Missing/unlisted symbols remain N/A; no cross-exchange backfill.
- Derivatives data can validate a setup but cannot override Entry/Trigger/SL/TP/R:R or Risk Veto rules.
