# MASTER BTC TREND V3 R2.6 — Forward Scorecard

- As-of UTC: `2026-09-10T20:27:57.334711+00:00`
- Strict Forward days: **2 / 365**
- Automated assessment: **HOLD_COLLECTING**
- Production decision: **HOLD** (automatic promotion prohibited)

## Eligibility
| Item | Current | Minimum | Status |
|---|---:|---:|---|
| Calendar days | 2 | 365 | WAIT |
| Resolved total | 0 | 12 | WAIT |
| Resolved LONG | 0 | 5 | WAIT |
| Resolved SHORT | 0 | 5 | WAIT |
| MCR90 truth windows | 0 | 5 | WAIT |
| MCR365 truth windows | 0 | 3 | WAIT |
| State ALL cohorts | 0 | 10 | WAIT |

## Primary gates
| Gate | Value | Rule | Status |
|---|---:|---|---|
| LONG expectancy | None | > 0R | N/A_NOT_MATURE |
| SHORT expectancy | None | > 0R | N/A_NOT_MATURE |
| Capture/Loss | None | > 1.10 | N/A_NOT_MATURE |
| MCR90 | None | >= 0.20 | N/A_NOT_MATURE |
| MCR365 | None | >= 0.20 | N/A_NOT_MATURE |
| MDD | None | > -6R | N/A_NOT_MATURE |
| State monotonicity | - | non-decreasing | N/A_NOT_MATURE |
| Cycle safety | - | eligible bucket >= -0.15R | N/A_NOT_MATURE |
| MATURE_BEAR SHORT | None | eligible mean >= -0.15R | N/A_NOT_MATURE |

## Early detection (diagnostic only; zero execution authority)
- Explicit EARLY→executed samples: 0
- Median explicit EARLY lead: None h
- Lead gate: N/A_NOT_MATURE
- EARLY→Seed conversion: None
- Noise-chain rate: None

## Integrity
- Hard gate: **PASS**
- Failures: []

> This scorecard cannot promote Production. A separately executed formal review is required by R26_FORWARD_PROMOTION_SPEC_V1.
