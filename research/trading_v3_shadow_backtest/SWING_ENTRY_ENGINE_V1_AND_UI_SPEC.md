# MASTER TRADING · SWING ENTRY ENGINE V1 + ADDITIVE UI SPEC

Status: RESEARCH / ADDITIVE ONLY
Purpose: extend MASTER TRADING from daily-trading-only execution precision into a dual-horizon system without changing existing SCREEN 1~6 semantics.

## 1. OPERATING PURPOSE
MASTER TRADING has two execution horizons:
1. DAILY TRADING — intraday to ~1 day. `1D context -> 4H regime -> 1H setup -> 30m/15m trigger`.
2. SWING TRADING — several days to several weeks. `1W/1D regime -> 4H setup/entry -> 1H/4H trigger`.

The same asset may have different Daily and Swing actions at the same time. Never force them to agree.

## 2. SWING DATA PRIORITY
Tier A:
- 1W/1D market regime and multi-timeframe structure
- 1D/4H S/R, major swing high/low, BOS/CHoCH/reclaim/failed breakout
- 1D/4H volume and participation persistence
- MA/EMA location, AVWAP from major swing/event, Volume Profile POC/HVN/LVN
- ATR / realized-volatility regime and non-chasing
- structural SL + realistic TP1/TP2/TP3 + core R:R >= 3

Tier B:
- OI / Funding / Basis trends, not single snapshots
- CVD/Taker persistence when history is fresh and comparable
- ETF/institutional flow for BTC/ETH
- On-chain exchange flow / SOPR / MVRV / realized-price family when source-valid
- verified whale/smart-money flow
- relative strength / BTC and ETHBTC context for alts
- Fibonacci PRICE retracement/extension from objectively locked completed 1D/4H swings
- Wave/Energy as context only

Tier C / risk:
- macro/event calendar, major unlock/exploit/delisting
- BTC/ETH options expiry, major strikes, IV/skew/gamma context when source-valid
- liquidation/liquidity map as entry-refinement context, not sole swing thesis

Fibonacci Time remains OFF.

## 3. SWING RESEARCH SCORE — 100
PRE-TOUCH 75:
1. 1W/1D Regime & MTF Alignment — 18
2. Structure & Location Quality — 25
3. Volume / Participation Persistence — 10
4. Derivatives Positioning Trend — 7
5. Institutional / On-chain / Verified Flow — 7
6. Volatility / Extension / Non-Chasing — 5
7. Wave / Fibonacci / Momentum secondary context — 3

REACTION 25:
8. 4H / 1H completed reaction trigger — 10
9. Reaction participation + order-flow confirmation — 8
10. Time validity / retest / no-chase — 4
11. Event / severe risk revalidation — 3

Score ranks setups; it never replaces hard gates.

## 4. SWING ENTRY ENGINE
Candidate Entry Zone order:
1. completed 1D/4H swing / BOS / reclaim / failed-break level
2. major 1D/4H S/R cluster
3. 1D/4H AVWAP / Volume Profile / major MA confluence
4. prior week/day high-low-close and weekly/monthly open where useful
5. Fibonacci PRICE confluence from the same objectively locked 1D/4H structure
6. liquidity/supply-demand context
7. ATR normalization

Trigger:
- LONG pullback: 1H/4H completed lower-wick bullish close or break/reclaim; next candle holds reaction low.
- LONG breakout: 1H/4H completed close above resistance then retest holds.
- SHORT: 1H/4H upper-wick bearish close or fake break back below resistance / breakdown failed retest.

SL:
- locked Swing Trade Frame structural invalidation, usually 4H or 1D.
- never widen to a longer frame after entry to avoid realizing a loss.
- Fib cannot define SL by itself.

Targets:
- TP1 nearest confirmed structural/liquidity objective
- TP2 primary swing objective; prefer TP2 >=3R where realistic
- TP3 higher-frame expansion objective
- Fib 1.272/1.618 only as confluence/projection, never manufactured target

## 5. ADDITIVE UI — SCREEN 7 ONLY
Existing SCREEN 1~6 remain unchanged.
Add one final execution screen before follow-up questions/footer:

# SCREEN 7 — SWING TRADING LONG / SHORT 타점

### 7-1 SWING LONG
Columns exactly:
`항목 | 단기 스윙 | ⭐ BEST | 최대 마지노선`
Rows:
- 타임프레임
- 예상 보유기간
- 진입 구간
- 1차
- 2차
- 평균가
- Trigger
- SL
- TP1
- TP2
- TP3
- R:R
- 도달확률
- 시간 유효성
- 핵심 근거
- 무효화
- Action

LONG Entry Zone display rule: ascending low -> high.

### 7-2 SWING SHORT
Same columns and row order.
SHORT Entry Zone display rule: descending high -> low.

Screen 7 summary exact semantic form:
`SWING LONG BEST: ___ / SWING SHORT BEST: ___ / 실제 SWING ENTER 가능 방향: ___ / 가장 먼저 필요한 조건: ___`

## 6. HORIZON SEPARATION HARD RULES
- Daily and Swing setup IDs, Trade Frames, SLs, TP ladders, Time Validity and outcome logs are separate.
- A failed Daily trade may not be converted into a Swing trade to avoid loss.
- A Swing thesis may remain valid while Daily action is SHORT/WAIT; show both independently.
- Daily 15m/30m trigger does not automatically authorize Swing ADD.
- Swing 1H/4H trigger does not authorize Daily chase.

## 7. MIGRATION RULE
This swing engine/UI addition is explicitly user-requested and may be implemented during Work migration after Daily V3 promotion is validated. It must be developed as a separate horizon module and must not rewrite historical Daily records or existing SCREEN 1~6.
