# BTC zero-base research — 2026-09-19

**Numeric intersection found; the 70-USDT-every-day objective failed. Not deployed.**

This branch is isolated from production. No trading credentials, order endpoints, legacy entry rules or wave-energy rules were used.

Use `RUN_RESEARCH.py` as the canonical launcher, not the original transport file `research.py`. It writes the full readable source into the output directory before execution. The launcher fixes the pandas `DataFrame.hist` method/column collision identified in the first Actions run. The source SHA is checked before and after the fix.

The first Actions run verified and retained 274 public Binance archives, 235,392 fifteen-minute bars and 7,356 funding events. It then failed in feature generation; that failed run is not a backtest pass. Its raw-data artifact was downloaded and the corrected engine was executed locally. The 6,912-configuration grid completed. A computationally equivalent vectorized threshold scan resumed that saved grid without changing trading rules or selection criteria. The selected configuration was locked before evaluation of 2025 onward.

At original thresholds, 199 configurations passed the numerical train/validation filters, and 7 also met positive PnL, sample-size and margin-diagnostic requirements. No relaxation was needed. The selected candidate is a one-hour bullish body engulf with a four-hour EMA20>EMA50 regime and positive rolling taker delta, with limit offset 0 ATR, stop 1.5 hourly ATR (minimum 0.5%, maximum 3%), and target 4 gross R.

Full history: 539 fills / 546 issued signals; net win rate 36.73%, realized payoff 1.97, fill rate 98.72%; +439.66 USDT on 1,000 initial capital with 10-USDT planned per-trade risk. Mean complete-calendar-day cash PnL +0.18 USDT. No day reached +70 USDT. Historical test from 2025: 133 fills, win rate 41.35%, payoff 1.83, +192.51 USDT; mean +0.31/day.

This is not a daily-income system: approximately one fill per 4.55 days. 2021, 2022 and 2024 lost money. Monthly block-bootstrap mean-PnL intervals include zero. Historical queue, partial fills, exact liquidation tiers and all settlement-mark instants are not reconstructed. Fee and slippage are each charged on both sides. Funding rates and timestamps are actual; most settlement valuations use official 15-minute mark opens, with explicit fallback counts.

`RESULT_SUMMARY.json` is a compact receipt, not a substitute for the complete ledgers and local reproducibility package delivered in the conversation. No main-branch merge, live enablement or recurring trading automation occurred.
