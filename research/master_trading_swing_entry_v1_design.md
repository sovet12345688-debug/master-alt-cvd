# MASTER TRADING — SWING ENTRY ENGINE V1 RESEARCH DESIGN

Status: RESEARCH / SHADOW ONLY
Purpose: add high-quality LONG/SHORT swing-entry derivation while preserving the existing MASTER TRADING daily-trading engine and Screens 1~6.
Primary holding horizon: roughly 2~14 days, occasionally longer only when the locked Trade Frame is 1W and the structure remains valid.

## 0. NON-DESTRUCTIVE RULE
- Existing Production MASTER TRADING canonical and Screens 1~6 remain unchanged during research.
- Swing is a separate execution horizon, not an excuse to widen a failed daily-trading stop.
- Daily and Swing must use separate Setup IDs, Trade Frame Locks, Entry/SL/TP/R:R, Time Validity and outcome logs.
- Never convert a losing Daily setup into a Swing setup after entry.
- Score ranks quality; hard execution gates dominate.

## 1. FRAME STACK
- 1W: macro regime / major structural S/R / cycle context
- 1D: primary swing regime and trend structure
- 4H: setup structure / Entry Zone / structural invalidation
- 1H: completed-candle Trigger / retest / execution refinement
- 15m/30m may be used only for execution refinement after the 1H thesis is valid; they must not redefine the Swing thesis.

## 2. DATA PRIORITY
### Tier A — Core Swing Execution
1. 1W/1D/4H multi-timeframe structure: HH/HL, LH/LL, BOS/CHoCH, reclaim, failed breakout, base/compression.
2. Higher-timeframe S/R and prior major swing high/low.
3. Daily/4H volume and participation persistence.
4. Anchored VWAP from major swing/event + weekly/session VWAP where useful.
5. Volume Profile POC/HVN/LVN over multi-day/multi-week windows.
6. ATR / realized-volatility regime and extension/non-chasing.
7. OI level + change/velocity, Funding, Basis/perp premium.
8. CVD/Taker and spot-vs-perp confirmation when history/source is valid.
9. Liquidation/Liquidity map for stop-hunt and major magnet context.
10. 1H/4H completed Trigger and retest.
11. Structural SL and realistic TP1/2/TP3 with core R:R >= 3.

### Tier B — Swing Context
- Relative strength vs BTC and sector breadth.
- RSI/KDJ/MACD as a correlated momentum family, capped.
- Wave/Energy as maturity/context only.
- Fibonacci PRICE retracement/extension from objective completed 4H/1D swings; Fibonacci Time remains OFF.
- ETF/institutional flow for BTC/ETH when fresh.
- On-chain exchange flow / MVRV / SOPR / NUPL / realized-price family when source-valid.
- Verified Whale/Smart-Money flow only; anonymous wallet != accumulation.
- Options expiry / IV / skew / major strikes for BTC/ETH when source-valid.
- Macro/event risk and crypto-specific exploit/delisting/unlock/supply events.

## 3. SCORE ARCHITECTURE — 100 POINTS
### PRE-TOUCH QUALITY — 75
1. Weekly/Daily regime & MTF alignment — 15
2. Structure & higher-timeframe location — 24
3. Multi-day volume / participation / RS — 12
4. Derivatives & liquidity positioning — 8
5. Volatility / extension / asymmetry — 8
6. Slow/context confirmation — 8
   - Momentum family cap 2
   - Wave/Energy cap 1
   - Fib price cap 1
   - ETF/On-chain/Whale/Options/Macro combined cap 4

### REACTION QUALITY — 25
7. 1H/4H completed reaction candle — 9
8. Trigger participation / CVD / OI consistency — 7
9. Retest / liquidity behavior / non-chasing — 5
10. Time Validity / freshness — 4

TOTAL = 100.
Research thresholds are not Production locks until OOS validation.

## 4. ENTRY ZONE ENGINE
Generate Swing candidate zones in this order:
1. 4H/1D structural swing / BOS / reclaim / failed-break levels
2. 1D/1W S/R cluster
3. Anchored VWAP / multi-day Volume Profile
4. prior-week/month reference levels where relevant
5. objective Fibonacci price confluence
6. major liquidation/liquidity pools
7. ATR/volatility normalization

Prefer zones with independent confluence, low extension, nearby structural invalidation and enough room to the next real higher-timeframe target for >=3R.

## 5. STRUCTURAL SL
- Lock Swing Trade Frame before entry: normally 4H or 1D; 1W only when explicitly selected.
- SL is the failure of that Swing structure, not a fixed percentage.
- ATR/liquidity buffer may refine placement only after validation.
- Do not move a 4H Swing SL to 1D after entry merely to avoid loss.

## 6. TARGET ENGINE
- TP1 = nearest meaningful 4H/1D reaction/liquidity objective.
- TP2 = primary structural target and preferred core >=3R checkpoint.
- TP3 = higher-frame expansion/major swing objective.
- Fibonacci 1.272/1.618 may support TP projection only when aligned with real structure/liquidity.
- Do not manufacture a distant 3R target using Fib alone.

## 7. EXECUTION TRIGGER
Preferred Swing trigger:
- 1H completed reclaim / rejection / breakout-retest / failed-break-retest.
- 4H completed candle may be required for major 1D structure transitions.
- 15m/30m is execution refinement only after the Swing thesis is already valid.

## 8. HARD GATES
Actual Swing ENTER/ADD requires:
- fresh Current
- Swing Trade Frame locked
- valid structural Entry/Retest
- required completed 1H/4H Trigger
- participation confirmation
- Non-Chasing
- structural SL/invalidation
- TP1/2/3
- realistic core R:R >= 3
- no Severe Risk Veto
- Time Validity acceptable/revalidated

One missing critical gate => WAIT.

## 9. DAILY ↔ SWING ISOLATION
- A Daily trade and Swing trade on the same asset may coexist only as separate setups with separate IDs and independent risk.
- Daily SL failure does not inherit the Swing thesis.
- Swing analysis may explain higher-timeframe context for Daily, but may not override a failed Daily execution frame.
- User-visible tables must clearly label `DAILY` vs `SWING` so the user cannot confuse the holding horizon or SL logic.

## 10. VALIDATION PLAN
After Daily V3 is validated, Swing V1 should be backtested separately using BTC/ETH first, then liquid large-cap alts.
Metrics: Expectancy R, PF, MaxDD, false-start rate, MFE/MAE, Entry Efficiency, TP1/2/3 rates, missed-move rate, hold time, performance by regime/asset/direction.
No historical unavailable OI/CVD/liquidation/on-chain values may be reconstructed.

FINAL: Swing is an additive second execution horizon. It must improve opportunity selection without weakening Daily Trade Frame discipline or changing existing Screens 1~6.