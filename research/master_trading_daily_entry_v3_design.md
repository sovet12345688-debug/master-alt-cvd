# MASTER TRADING — DAILY ENTRY ENGINE V3 RESEARCH DESIGN

Status: RESEARCH / SHADOW ONLY
Purpose: improve daily-trading entry, stop, trigger and target precision without changing MASTER TRADING production canonical or UI.
Primary horizon: intraday to ~1 day; 4H regime -> 1H setup -> 15m/30m execution. 1D is context, not the default execution frame.

## 0. NON-DESTRUCTIVE RULE
- Existing MASTER TRADING canonical, SCREEN 1~6, Entry/SL/TP/R:R fields and user-visible UI remain unchanged during research.
- V3 is shadow/research only until point-in-time OOS evidence supports promotion and the user explicitly approves cutover.
- Score ranks setups; score never replaces execution hard gates.
- Missing optional data is N/A, never zero and never fabricated.
- No look-ahead, no reconstructed historical CVD/OI/liquidation/on-chain values.

## 1. DAILY-TRADING PRINCIPLE
Daily-trading information decays quickly. Therefore weighting must prioritize data closest to the actual execution event:
1) regime and structure,
2) exact price location,
3) participation/order flow,
4) liquidity/positioning,
5) completed reaction trigger,
6) structural risk/reward.
Slow data such as on-chain/ETF/whale is context only unless it creates a severe risk condition.

Default frame stack:
- 1D: background regime / major S/R / event context
- 4H: primary regime and directional permission
- 1H: setup structure and candidate zone
- 30m + 15m: completed-candle reaction trigger and execution

## 2. DATA AXES

### TIER A — CORE DAILY EXECUTION
A1. Market regime / volatility regime
- trend vs range vs transition
- 1D/4H/1H alignment
- ATR / realized volatility percentile
- purpose: choose breakout, pullback, or mean-reversion logic; normalize zone width and SL buffer.

A2. Multi-timeframe price structure
- HH/HL, LH/LL, BOS/CHoCH, swing high/low, reclaim, failed breakout, compression
- purpose: primary Entry and structural invalidation skeleton.

A3. Location / value / reference levels
- support/resistance, prior-day high/low/close, weekly open, session open
- session VWAP and Anchored VWAP from major swing/event
- Volume Profile POC/HVN/LVN when available
- Fibonacci price retracement/extension confluence from objectively locked completed swings
- purpose: distinguish favorable location from mid-range/chasing location.

A4. Volume / participation
- spot/futures volume, breakout/reversal volume, relative volume, volume persistence
- purpose: verify that price movement has real participation.

A5. Order flow
- CVD, taker buy/sell, spot-vs-perp CVD divergence, large-trade flow when source-valid
- purpose: detect absorption, hidden accumulation/distribution and false price moves.

A6. Derivatives positioning
- OI level and delta/velocity, funding level and change, basis/perp premium, long-short positioning
- purpose: distinguish trend participation from squeeze/crowding/deleveraging.

A7. Liquidity / microstructure
- order-book depth/imbalance, spread, liquidation history, price-level liquidation heatmap when available
- purpose: identify stop-hunt risk, liquidity magnets and whether SL is placed at an obvious pool.

A8. Trigger / price action
- 15m/30m completed candle only
- LONG: lower wick + bullish close, break/reclaim, breakout + retest hold
- SHORT: upper wick + bearish close, fake break + close back below, breakdown + failed retest
- purpose: actual execution permission.

A9. Structural risk / asymmetry
- locked Trade Frame structural invalidation
- volatility-aware buffer
- TP1/2/3 from source-supported structure/liquidity
- core R:R >= 3
- purpose: reject good-looking setups with bad asymmetry.

### TIER B — SECONDARY CONFIRMATION
B1. Momentum family
- RSI, KDJ, MACD
- family cap: do not count correlated momentum indicators as independent signals.
- purpose: extension, exhaustion, divergence, acceleration/decay.

B2. Wave / energy
- upper-frame wave state, impulse vs correction, energy expansion/decay
- context/risk only; cannot authorize Entry by itself.
- existing TIME VALIDITY research showed Wave Energy improved drawdown in some cases but did not consistently improve average R/PF versus the stronger baseline, so it remains capped as secondary context.

B3. Relative strength / cross-asset context
- alt/BTC relative strength, ETHBTC where relevant, BTC direction, broad alt breadth
- purpose: avoid long weak alts against BTC risk-off or short strong leaders during rotation.

B4. Fibonacci price geometry
- price retracement and extension only; Fibonacci Time remains OFF.
- preferred retracement references: 0.382 / 0.5 / 0.618 / 0.786 when they align with completed 1H/4H swings.
- preferred extension references for target research: 1.272 / 1.618 when supported by structure/liquidity.
- anchors must be objectively defined completed swing high/low or impulse leg on the locked Trade Frame; arbitrary anchor cherry-picking is forbidden.
- Fibonacci cannot create an Entry, SL or TP by itself. It is a confluence/projection tool only.
- structural invalidation always overrides a Fibonacci level.

### TIER C — SLOW / EVENT CONTEXT
C1. Macro/event calendar
- CPI/PPI/FOMC/NFP, major crypto-specific event, exploit/delisting/unlock
- severe event risk can veto execution; otherwise low score weight.

C2. ETF / institutional flow
- BTC/ETH ETF flow when fresh
- context only for daily entries; no direct Entry price generation.

C3. On-chain
- exchange flow, MVRV/SOPR/NUPL/realized-price family when source-valid
- low weight for intraday; useful for medium-term distribution/accumulation context.

C4. Whale / smart-money
- only verified/entity-normalized flow; anonymous wallet != accumulation
- low weight unless independently confirmed by price/volume/order flow.

C5. Options context (recommended optional addition for BTC/ETH)
- expiry concentration, major strikes, IV/skew/gamma context when source-valid
- use as event/liquidity context only; never invent dealer gamma.

## 3. NEW HIGH-VALUE ADDITIONS RECOMMENDED
The following are higher priority than adding more oscillators:
1) ATR / realized-volatility regime
2) Session VWAP + Anchored VWAP
3) Volume Profile (POC/HVN/LVN)
4) Prior-day high/low/close + weekly/session opens
5) Spot-vs-perp CVD divergence
6) OI velocity/change, not only OI level
7) Order-book depth imbalance + spread
8) Price-level liquidation heatmap
9) Session/time-of-day liquidity model (Asia/London/NY overlap)
10) Fibonacci price retracement/extension confluence from objectively locked swings
11) Optional BTC/ETH options expiry/IV context

## 4. SCORE ARCHITECTURE — 100 POINTS
Score is for ranking/quality only. Hard gates remain dominant.

### 4.1 PRE-TOUCH QUALITY — 70 points
This score exists before the candidate Entry zone is touched.

1. Regime & multi-timeframe alignment — 12
- 4H regime fit 5
- 1H setup alignment 4
- 1D background/non-conflict 3

2. Structure & location quality — 22
- swing/BOS/reclaim/failure structure 8
- S/R quality and repeated reaction 5
- MTF confluence 3
- VWAP/AVWAP/Volume Profile/value location 3
- prior-day/session reference levels 2
- Fibonacci price confluence 1

3. Participation & order-flow lead — 14
- volume quality/persistence 5
- CVD/taker confirmation/divergence 5
- spot-vs-perp confirmation 2
- large-flow confirmation when valid 2

4. Derivatives & liquidity positioning — 10
- OI/funding/basis state 4
- depth/order-book imbalance 2
- liquidation/liquidity map 4

5. Volatility / extension / non-chasing — 7
- ATR/RV regime fit 3
- distance from fair value / extension 2
- zone efficiency vs volatility 2

6. Secondary context — 5
- momentum family cap 2
- wave/energy cap 1
- relative strength / macro / ETF / on-chain / whale context combined cap 2

PRE-TOUCH TOTAL = 70

### 4.2 REACTION QUALITY — 30 points
Only after Entry-zone touch/retest.

7. Completed candle reaction — 10
- correct 15m/30m trigger pattern 6
- close location/body-wick quality 2
- next-candle hold/failed retest confirmation 2

8. Trigger participation — 9
- reaction volume expansion/absorption 4
- CVD/taker alignment 3
- OI change consistent with the move 2

9. Microstructure confirmation — 6
- depth/spread/imbalance supports reaction 2
- liquidation sweep/absorption behavior 2
- spot-perp/basis behavior supports reaction 2

10. Time validity / execution freshness — 5
- prompt reaction after touch 2
- no material time decay 1
- retest not chased / execution still inside structure 2

REACTION TOTAL = 30

TOTAL TRADE QUALITY = PRE-TOUCH 70 + REACTION 30 = 100

## 5. CORRELATION / DOUBLE-COUNT CAPS
To prevent fake confidence:
- RSI + KDJ + MACD combined max = 2 points inside Secondary Context.
- EMA/MA + VWAP + AVWAP + Volume Profile + Fibonacci are all location/confluence tools; they increase confluence but cannot each be counted as separate full-strength independent signals.
- Fibonacci price contribution is capped at 1 point inside Structure & Location Quality.
- Volume + CVD + Taker + Depth are related participation families; family weights are capped as defined above.
- OI alone is never bullish/bearish.
- Wave/Energy remains max 1 point and context-only.
- On-chain + ETF + Whale combined cannot override current price/trigger evidence.
- Fibonacci Time remains OFF and contributes 0 points.

## 6. ENTRY ZONE ENGINE
Candidate zones are generated in this order:
1) structural swing / BOS / reclaim / failed-break levels,
2) 1H/4H S/R clusters,
3) prior-day/session levels,
4) VWAP / Anchored VWAP / Volume Profile confluence,
5) Fibonacci price retracement confluence from the same objectively locked swing structure,
6) liquidity/liquidation pools,
7) volatility/ATR normalization.

A zone is preferred when:
- it is underextended/non-chasing,
- multiple independent location families overlap,
- structural invalidation is close enough to preserve asymmetry,
- next meaningful target is far enough for >=3R,
- the zone is not directly inside an obvious adverse liquidity sweep without reclaim evidence.

Do not generate a zone from RSI/KDJ/MACD or Fibonacci alone.

## 7. STRUCTURAL SL ENGINE
SL order:
1) lock Trade Frame,
2) identify exact structure whose failure disproves the trade thesis,
3) place invalidation beyond that structure,
4) apply volatility/liquidity buffer only if supported,
5) reject the trade if the resulting SL destroys >=3R asymmetry.

Fibonacci rule for SL:
- Fibonacci levels may explain confluence near a structural invalidation but must never define the SL by themselves.
- do not move or widen SL merely to sit beyond 0.618/0.786 or another Fibonacci ratio.

Research parameters to test, NOT production constants:
- volatility buffer candidates: 0.10 / 0.15 / 0.20 x 1H ATR
- compare against no-volatility-buffer structural baseline
- evaluate stop-hunt reduction vs larger loss distance using MAE/MFE and expectancy

Never move SL to a longer-frame structure after entry merely to avoid a loss.

## 8. TARGET ENGINE
- TP1 = nearest confirmed reaction/liquidity objective
- TP2 = primary structural objective; preferred core R:R checkpoint
- TP3 = expansion/major higher-frame objective
- Fibonacci extensions (especially 1.272 / 1.618) may be tested as TP confluence/projection only when aligned with structure, prior highs/lows, VWAP/Volume Profile or liquidity objectives.

Recommended research rule for higher precision:
- prefer setups where TP2 itself can deliver >=3R.
- if only a remote TP3 creates 3R while TP1/TP2 are crowded by resistance/support, classify lower quality or WAIT; do not game R:R with an unrealistic distant TP3.
- a Fibonacci extension alone is never sufficient to manufacture a 3R target.

## 9. ACTION / GATE LOGIC
Hard gates dominate score.

ENTER requires:
- CurrentFresh
- TradeFrameLocked
- StructuralEntryOrRetest
- completed 15m/30m Trigger
- Participation confirmed
- NonChasing
- StructuralSL
- TP1/2/3
- core R:R >=3
- no Severe Risk Veto
- Time Validity acceptable/revalidated

Score interpretation for research/shadow ranking only:
- 85~100: A+ setup candidate
- 78~84: A setup candidate
- 70~77: B / WATCH
- <70: low priority / WAIT

These thresholds are research seeds, not production locks until OOS calibration.

### SMALL ENTER research guardrail
Existing E1 SMALL ENTER remains unchanged in production.
For V3 research, test whether PRE-TOUCH >= 60/70 plus all existing E1 hard gates improves or worsens expectancy. Do not production-lock this threshold without OOS evidence.

## 10. DAILY-TRADING SPECIAL RULES
1) Mid-range penalty: avoid entries in the middle of an established intraday range.
2) Session-awareness: mark Asia/London/NY transitions and scheduled US data windows.
3) Event blackout: around tier-1 macro or major crypto events, require revalidation and reduce/forbid pre-trigger entry.
4) Relative-volume rule: breakout/reversal without participation is downgraded.
5) Spot-perp divergence: perp-led move without spot confirmation is lower quality.
6) OI-crowding rule: price up + OI up + CVD down + hot funding is a crowding warning, not bullish confirmation.
7) Liquidity-sweep rule: obvious stop pool sweep + reclaim can improve reversal quality; direct entry before sweep is lower quality.
8) Time-decay: 1H setups should react quickly after touch; slower setups require rolling revalidation, consistent with TIME VALIDITY V2.1.
9) Fibonacci price rule: use retracement/extension only as confluence with objective structure; Fibonacci Time remains OFF.

## 11. VALIDATION PLAN
Do not optimize for win rate alone.
Primary metrics:
- Expectancy (R/trade)
- Profit Factor
- Max drawdown in R
- False-start loss rate
- MFE / MAE
- Entry efficiency
- TP1/TP2/TP3 hit rates
- Missed-move rate
- Time-to-trigger / time-to-progress
- performance by regime (trend/range/transition)
- performance by asset (BTC/ETH first; later liquid large-cap alts)

Method:
- point-in-time only
- walk-forward / train-validation-OOS separation
- no reconstructed unavailable order-flow/on-chain history
- ablation tests by data family
- compare Current Production baseline vs V3 shadow
- test weights as ranges, not single overfit optimum
- bootstrap/confidence intervals where sample permits
- include a Fibonacci ablation: V3 with Fibonacci price confluence vs V3 without Fibonacci, to verify whether the added location/target information improves OOS expectancy, PF, false-start rate or MAE/MFE rather than assuming usefulness.

Promotion criteria proposal (research target, not locked):
- higher OOS expectancy and PF than baseline,
- no material degradation in maxDD,
- lower false-start rate,
- stable direction across multiple regimes/assets,
- improvement not dependent on one optional data source.

## 12. IMPLEMENTATION PRIORITY
P0 — no-UI-change research wiring
1) add V3 shadow score calculator and lineage log
2) wire latest_microstructure (CVD/Taker/Basis/Depth/Liquidation) into Trading research input
3) add KDJ parsing when visible, but keep inside momentum-family cap
4) add ATR/RV, prior-day levels, session VWAP/AVWAP
5) add deterministic Fibonacci price retracement/extension calculator using completed 1H/4H swing anchors; Fibonacci Time remains OFF

P1 — higher-value location/liquidity
6) Volume Profile POC/HVN/LVN
7) price-level liquidation heatmap source with freshness/quality contract
8) spot-vs-perp CVD divergence
9) OI velocity/change

P2 — slow context
10) on-chain adapter for Trading context
11) verified Whale/ETF context
12) optional BTC/ETH options context

## 13. PRODUCTION SAFETY
- Existing SCREEN 1~6 unchanged.
- Existing user-visible fixed LONG/SHORT target tables unchanged.
- V3 runs shadow-only until explicit approval.
- If V3 disagrees with production, log disagreement; do not silently overwrite production.
- No historical signal reconstruction.
- Optional data failure lowers confidence/coverage but does not automatically veto a complete setup unless the missing field is explicitly required by the setup.
- Fibonacci price geometry is research/shadow-only until OOS validation; Fibonacci Time remains OFF.
