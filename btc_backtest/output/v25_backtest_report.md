# MASTER BTC TREND V2.5 Stage-1 Backtest

- Data: Binance Vision USD-M Futures BTCUSDT 1h monthly klines
- Range: 2023-09-01 00:00:00+00:00 ~ 2026-08-31 23:00:00+00:00
- 1H rows: 26,304
- Registered plans: 22
- Zone-touch events: 40

## Full sample

| Metric | Confirm-only proxy | V2.5 strict EARLY | Moderate | Loose |
|---|---:|---:|---:|---:|
| Signals | 0 | 0 | 0 | 0 |
| SL-first % | N/A | N/A | N/A | N/A |
| TP1-first % | N/A | N/A | N/A | N/A |
| Median +24h % | N/A | N/A | N/A | N/A |
| Median +72h % | N/A | N/A | N/A | N/A |
| Median MFE % | N/A | N/A | N/A | N/A |
| Median MAE % | N/A | N/A | N/A | N/A |

- Strict EARLY vs later-confirm paired cases: **0**
- Median entry improvement: **N/A%**
- Median lead time: **N/Ah**
- Confirm within 24h after strict EARLY: **N/A%**

## Split check

- In-sample events/signals(strict): 16 / 0
- Out-of-sample events/signals(strict): 24 / 0

## Preliminary decision
- STRICT EARLY sample <10: do not optimize thresholds yet; keep EARLY small/experimental.

## Important limitations
- No historical OI/Funding axis in Stage-1
- No ETF/macro severe-risk veto in signal simulation
- Zone/score rules are deterministic proxies of visual MASTER rules
- Fees/funding/slippage excluded