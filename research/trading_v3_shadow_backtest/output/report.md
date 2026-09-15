# MASTER TRADING · DAILY ENTRY ENGINE V3 SHADOW BACKTEST

**FINAL VERDICT: V3 PARTIAL — MORE VALIDATION REQUIRED**

## Method
- Point-in-time OHLCV + Binance futures taker-buy quote from 15m bars; 30m/1H/4H/1D derived without look-ahead.
- This is a reproducible execution proxy comparison, not a reconstruction of historical MASTER signals that were never durably stored.
- Train=2024, Validation=2025, OOS=2026-01-01 through 2026-08-31.
- Historical optional fields that cannot be reconstructed are N/A, not zero.

## Locked ATR buffer selected on TRAIN
- V3_NOFIB: 0.10 x 1H ATR
- V3_FIB: 0.10 x 1H ATR

## OOS headline
| Version | n | Exp R | PF | MaxDD R | False-start | MFE | MAE | TP2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BASELINE | 0 | nan | nan | nan | nan% | nan | nan | nan% |
| V3_NOFIB | 0 | nan | nan | nan | nan% | nan | nan | nan% |
| V3_FIB | 2 | 1.732 | inf | 0.00 | 0.0% | 2.61 | 1.42 | 50.0% |

## Fib ablation
- Verdict: **FIB_NEUTRAL_OR_SAMPLE_INSUFFICIENT**
- Bootstrap V3_FIB - V3_NOFIB: {"n_a": 2, "n_b": 0, "mean_diff": null, "ci95": null, "p_gt_0": null}

## Promotion reasons
- OOS sample below 30 trades in baseline or V3

## Important limitations
- OI history from Binance public endpoint is retention-limited; not backfilled beyond available point-in-time history.
- Historical orderbook depth, liquidation heatmap, on-chain, ETF/whale/options intraday context are N/A in this test, so this validates the common-coverage V3 core rather than every optional live axis.
- Production UI/canonical remains unchanged by this research run.