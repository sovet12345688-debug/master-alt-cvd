# MASTER BTC TREND ↔ BTC 선행신호 엔진(R2.6) 연결 FINAL AUDIT V1

Status: FINAL_LOCK_BASELINE
Date KST: 2026-09-09

## Official schedule
- MASTER BTC TREND BASIC OFFICIAL: 05:00 / 08:00 / 13:00 / 17:00 / 21:00 KST
- R2.6 Forward OOS: 05:20 / 08:20 / 13:20 / 17:20 / 21:20 KST
- UTC cron: `20 4,8,12,20,23 * * *`

## Mapping
- 05:00 MASTER ← previous-day 21:20 Forward
- 08:00 MASTER ← 05:20 Forward
- 13:00 MASTER ← 08:20 Forward
- 17:00 MASTER ← 13:20 Forward
- 21:00 MASTER ← 17:20 Forward

If the expected Forward run is missing/running/failed/unverifiable, use the immediately previous verifiable successful Forward OOS run that completed before the MASTER report start, label it `STALE_FALLBACK`, and show the actual source time. Future/in-progress/failed runs are forbidden.

## Isolation
- BTC 선행신호 엔진 is READ_ONLY_ZERO_WEIGHT.
- It does not change V2.6 TREND, LONG:SHORT, BOTTOM/TOP, ACCUMULATION, Entry Gate, LIVE PLAN, Fractal or S/R.
- MASTER BTC TREND cannot fabricate or promote EARLY/PRIORITY/Seed/Confirm/Core.
- Execution/capital authority remains Frozen R2.5 only.
- STRICT_FORWARD + completed candle only. Bridge/history cannot be current signal.
- Zero STRICT Detection/Seed/Position is not a failure and must render as `신호 없음 / 대기`.

## Source-of-truth chain
Registry → BTC manifest → canonical → UI canonical → machine contract → R2.6 schedule-link contract → Forward OOS workflow / integration snapshot.

Central `MONEY MASTER OS Guard` must reject schedule/linkage/UI/authority drift from this baseline.
