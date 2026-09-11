# BTC Trend V2.6 ACCUM Fastpath V1

Coverage-gated **LIMITED DISPLAY** recovery path for structural `ACCUMULATION` N/A. The engine remains partial and has no independent execution authority.

## Current fast path

Uses only already machine-available facts:
- `STRUCTURE_1D` -> `ACCUM_BASE_1D` proxy (20)
- `STRUCTURE_4H` -> `ACCUM_IMPROVEMENT_4H` proxy (10)
- `STRUCTURE_1W` -> `ACCUM_LOCATION_1W` proxy (8)
- `SPOT_ETF_FLOW` -> `ACCUM_SPOT_ETF_FLOW` proxy (15)

Maximum available canonical weight is 53/100. Missing weight is excluded and valid weight is renormalized, while Coverage remains the original canonical weight coverage.

## Display and safety

- Coverage below 50 => fail-closed N/A.
- Coverage 50-69 => `부분산출` limited display only; zero independent Entry Gate authority.
- Strong confirmation requires Coverage >=70.
- If 1D or 4H structure is `STRONG_LONG`, the canonical rapid-rise cap is conservatively proxied and the score is capped at 64.
- Same-run successful workflow + artifact + matching score SHA256 provenance are required.
- The engine never writes `official_state` directly. Only an actual BASIC OFFICIAL may persist a verified display value.
- No effect on LONG:SHORT, TREND_STRENGTH, LIVE PLAN, BOTTOM, TOP, Fractal, S/R, or BTC leading signal engine.
- No historical backfill, stale substitution, cross-MASTER decisions, or invented missing values.

## Current validation

The branch live workflow passed with Coverage 53% (C), generated a same-run artifact and matching SHA256 provenance, and preserved all zero-effect guards. The observed ACCUMULATION value is dynamic and must be recalculated every run; it is not a fixed score.

## Not yet included

`ACCUM_VOLUME_LEAD`, `ACCUM_CVD_TAKER_DERIVATIVES`, `ACCUM_NONCHASE_VALUE`, `ACCUM_EMA_WAVE`, and `ACCUM_MACRO` remain excluded until their numeric thresholds and runtime linkage are separately validated.
