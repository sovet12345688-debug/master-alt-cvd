# MASTER BTC TREND V3 — R2.0 FINAL FREEZE REV2

## Status
`FINAL_FROZEN_NO_REPLAY_REV2`

REV2 supersedes the first R2.0 freeze before any R2.0 historical performance replay was run.
The reason is an implementation/PIT audit finding: source OHLCV timestamps are candle OPEN times, so cross-timeframe context must be delayed until each candle is complete.
No performance result was observed before this correction and no threshold, risk budget, entry rule, confirmation rule, exit rule, or promotion gate was tuned from historical R2.0 performance.

## Availability contract
- 1H source row available at open_time + 1H.
- 4H source row available at open_time + 4H.
- 1D source row available at open_time + 24H.
- Completed Monday-Sunday weekly context becomes available Monday 00:00 UTC after the Sunday daily candle has completed.
- All cross-timeframe joins use availability timestamps.

## Frozen identity
- Candidate SHA256: `2acb4c8b1cd24bc03ff1c14947361caa281b44680aa60d2323e1572284bfd048`
- Frozen config Git blob: `9f1a54edb0312bd86f230f025f539bd4255a016f`
- Engine Git blob: `cf5c5bf7a10ba699fe695964b7e7f435dead016b`
- Contract Git blob: `08aa0069283e717532c9f520f02654e3384a9002`
- PIT test Git blob: `eec7e2fdbbbf6421423b1f87ec4eb6ed8a65685c`
- PIT run: `34107216615`, 7/7 PASS.

## Evidence firewall
- Historical diagnostic window: 2021-01-01 through 2026-09-04 UTC.
- Historical replay before REV2 freeze: 0.
- Historical diagnostic cannot promote R2.0.
- Untouched forward evidence begins 2026-09-05 UTC.

## Rule freeze
All R2.0 strategy parameters and predeclared gates remain unchanged from V0.1. REV2 changes only timestamp availability semantics and the PIT test that enforces them.

After REV2 Freeze Guard and PIT battery pass in CI, exactly one historical diagnostic replay is permitted without changing the frozen files.
