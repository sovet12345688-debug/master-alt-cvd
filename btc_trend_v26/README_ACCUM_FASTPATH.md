# BTC Trend V2.6 ACCUM Fastpath V1

Research/shadow-only recovery path for structural `ACCUMULATION` N/A.

## Current fast path

Uses only already machine-available facts:
- `STRUCTURE_1D` -> `ACCUM_BASE_1D` proxy (20)
- `STRUCTURE_4H` -> `ACCUM_IMPROVEMENT_4H` proxy (10)
- `STRUCTURE_1W` -> `ACCUM_LOCATION_1W` proxy (8)
- `SPOT_ETF_FLOW` -> `ACCUM_SPOT_ETF_FLOW` proxy (15)

Maximum available canonical weight is 53/100. Missing weight is excluded and valid weight is renormalized, while Coverage remains the original canonical weight coverage.

## Safety

- Coverage below 50 => N/A.
- Coverage 50-69 => shadow/partial only; zero Entry Gate authority.
- Strong confirmation requires Coverage >=70.
- If 1D or 4H structure is `STRONG_LONG`, the canonical rapid-rise cap is conservatively proxied and the score is capped at 64.
- No direct `official_state` writes.
- No effect on LONG:SHORT, TREND_STRENGTH, LIVE PLAN, BOTTOM, TOP, Fractal, S/R, or BTC leading signal engine.
- No backfill, stale substitution, cross-MASTER decisions, or invented missing values.

## Not yet included

`ACCUM_VOLUME_LEAD`, `ACCUM_CVD_TAKER_DERIVATIVES`, `ACCUM_NONCHASE_VALUE`, `ACCUM_EMA_WAVE`, and `ACCUM_MACRO` remain excluded until their numeric thresholds and runtime linkage are separately validated.
