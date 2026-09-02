# MASTER BTC TREND V2.5 Diagnostic Pass

- 1H rows: 26,304
- Plans: 22
- Zone-touch events: 40

## Gate reach

- zone_ge82_events: **8**
- fear72_events: **39**
- exhaust70_events: **25**
- early75_events: **31**
- reaction65_events: **39**
- independent3_events: **39**
- early_core_simultaneous_events_24h: **2**
- early_core_simultaneous_events_72h: **3**
- fast_core_simultaneous_events_72h: **11**

## R:R sensitivity (zone midpoint reference only)

- median: **0.56**
- ge3_events: **6**
- ge35_events: **5**
- ge4_events: **5**
- zone1_median: **0.26**
- zone2_median: **0.87**
- zone3_median: **5.75**

## Executable actual-close variants

| Variant | Signals | SL-first | TP1-first | Median +24h | Median +72h | Median MFE | Median MAE |
|---|---:|---:|---:|---:|---:|---:|---:|
| early_core72 | 3 | 0.0 | 66.7 | 0.03 | 3.25 | 7.53 | -7.95 |
| early_rr3_72 | 0 | N/A | N/A | N/A | N/A | N/A | N/A |
| early_rr35_72 | 0 | N/A | N/A | N/A | N/A | N/A | N/A |
| early_rr4_72 | 0 | N/A | N/A | N/A | N/A | N/A | N/A |
| fast_core72 | 11 | 9.1 | 45.5 | 1.6 | 2.12 | 6.05 | -7.95 |
| fast_rr3_72 | 0 | N/A | N/A | N/A | N/A | N/A | N/A |

## Guardrail
- This pass diagnoses the test harness. It does not automatically alter V2.5 thresholds.