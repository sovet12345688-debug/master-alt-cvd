# MASTER TRADING — BASELINE+ V3 EDGE V1.1 (BTC DAILY)

Status: RESEARCH / SHADOW ONLY
Production canonical/UI: UNCHANGED
Purpose: keep CURRENT MASTER TRADING as the authoritative setup/execution engine and import only V3-derived components that showed positive BTC OOS separation.

## 1. Why CURRENT baseline remains primary
BTC OOS baseline remained better on win rate / SL rate / average MAE, while the full V3 filter improved expectancy / PF / MFE but discarded ~75% of baseline opportunities. A tier audit also showed the full V3 composite score was NON-MONOTONIC: low-score setups sometimes outperformed high-score setups. Therefore:
- do NOT replace CURRENT with V3,
- do NOT use the full V3 score as a hard gate,
- do NOT delete baseline setups because of a V3 score.

Baseline continues to own:
- direction/scenario engine
- Entry Zone outer structure
- Trade Frame Lock
- Structural SL / invalidation
- TP1 / TP2 / TP3 and >=3R gate
- 15m/30m completed-candle Trigger
- SMALL ENTER -> ADD logic
- Non-Chasing / Risk Veto / Time Validity
- Screen 1~6 UI

## 2. BTC feature attribution — what is actually worth importing
2026 BTC OOS attribution on the preserved baseline universe found these components with positive Expectancy AND PF separation with adequate pass/fail sample:

### PROMOTE
1. Daily direction alignment
   - pass ExpR ~0.200 vs fail ~0.011
   - PF ~1.45 vs ~1.02
   - role: scenario confidence / execution strictness

2. 1H EMA20-MA50 directional alignment
   - pass ExpR ~0.192 vs fail ~0.075
   - PF ~1.44 vs ~1.13
   - materially lower MAE on aligned setups
   - role: entry quality / BEST ranking

3. Relative Volume participation
   - RV >=1.0: ExpR ~0.236 vs ~0.148, PF ~1.60 vs ~1.31
   - RV >=0.8 also positive but is the weaker version
   - role: participation confirmation
   - do not double count RV>=0.8 and RV>=1.0

4. MACD directional alignment
   - pass ExpR ~0.207 vs ~0.153
   - PF ~1.62 vs ~1.27
   - lower MAE, but lower MFE
   - role: timing/entry-quality confirmation, not trend target expansion

5. MA50 proximity
   - pass ExpR ~0.243 vs ~0.166, PF modestly better
   - BUT MAE / SL behavior is worse
   - role: secondary confluence only; never a standalone entry signal or hard gate

### WATCH-ONLY RESEARCH SIGNAL
- 4H EMA20 proximity showed very strong results but only 24 pass samples; sample is too small for promotion. Track prospectively but do not hard-code as a gate.

## 3. V3 features NOT promoted into baseline execution
These were negative or not proven in the BTC proxy and therefore must NOT become positive hard filters:
- Taker alignment by itself
- CVD alignment by itself
- both Taker+CVD alignment as a bullish/bearish yes/no gate
- session VWAP proximity as a standalone positive gate
- EMA20 proximity as a standalone positive gate
- ATR mid-range regime as a positive gate
- extension <=0.6ATR as a positive gate
- RSI quality as a scoring booster
- KDJ non-hot as a scoring booster
- Fibonacci score

Important: CVD/Taker/VWAP/ATR/RSI/KDJ remain useful CONTEXT, divergence, trap and trigger-confirmation information. The finding only says that simplistic `aligned = good` scoring did not improve this BTC OOS proxy.

## 4. BASELINE+ EDGE OVERLAY — no composite V3 score
Replace the old V3 100-point/filter concept with evidence tags layered on CURRENT.

### Evidence tags
- D1_ALIGN: Daily direction agrees with setup
- H1_TREND_ALIGN: 1H EMA20/MA50 directional alignment
- RV_CONFIRM: Relative Volume >=1.0 preferred; >=0.8 secondary
- MACD_CONFIRM: MACD histogram aligned with direction
- MA50_CONFLUENCE: price/entry structure near MA50, secondary only
- H4_EMA20_RESEARCH: context badge only until more samples exist

No numeric total is required for ENTER.
No tag can create Entry/SL/TP by itself.

### Execution use
A) Baseline setup + D1_ALIGN + H1_TREND_ALIGN + RV_CONFIRM
- mark as higher execution quality
- candidate for ⭐BEST among already-valid baseline setups
- SMALL ENTER still requires every existing canonical E1 condition

B) Baseline setup with one or more alignment gaps
- setup is preserved
- do not delete it
- require stronger 15m/30m closed Trigger and participation before execution
- default WAIT/WATCH until reaction proves the setup

C) MACD_CONFIRM
- helps choose between competing baseline setups / preferred timing
- cannot compensate for missing structure, Trigger, SL, >=3R or Risk Veto

D) MA50_CONFLUENCE
- bonus context only when it overlaps actual structural S/R
- because MAE worsened in the proxy, do not tighten SL or market-enter merely due to MA50 proximity

## 5. Preferred Entry Pocket — baseline zone preserved
Do not move or expand the baseline Entry Zone.
Inside the already-derived baseline zone, prefer the 1st/2nd entry placement where these coincide:
1) structural S/R / swing / reclaim,
2) D1 + H1 trend alignment,
3) adequate relative volume at reaction,
4) 15m/30m completed trigger,
5) optional MACD timing confirmation,
6) VWAP/AVWAP/Volume Profile/Fib only as secondary confluence.

CVD/Taker must be interpreted contextually (absorption/divergence/participation), not as a simple same-direction mandatory gate.

## 6. Fibonacci final rule
Fibonacci Retracement / Extension remain context-only.
- score contribution: 0
- never create Entry, SL or TP
- useful when overlapping baseline S/R / VWAP / Volume Profile / liquidity target
- Fibonacci Time remains OFF

## 7. Internal execution order
1D context
-> 4H regime
-> CURRENT baseline setup generation
-> 1H structural Entry Zone / Structural SL / TP map
-> evidence tags (D1/H1/RV/MACD/MA50)
-> preferred entry pocket inside baseline zone
-> 15m/30m completed Trigger
-> contextual Volume/CVD/Taker reaction check
-> Non-Chasing / Risk Veto / Time Validity
-> R:R >=3
-> ENTER / SMALL ENTER / WATCH / WAIT / AVOID

## 8. UI behavior
Existing SCREEN 1~6 remains unchanged.
The overlay may only influence existing fields:
- which already-valid setup is marked ⭐BEST
- 1차 / 2차 preference inside existing Entry Zone
- Entry Quality
- 핵심 근거
- Action strictness
It must not alter locked screen order/labels or rewrite historical values.

## 9. Evidence files
- final V3 comparison: `ab_quick_output/summary.json`
- baseline-preserving tier audit: `baseline_plus_output/summary.json`
- BTC feature attribution: `feature_attribution_output/summary.json`

## 10. Final intended architecture
CURRENT MASTER TRADING = authoritative engine
BASELINE+ EDGE = selective evidence overlay only
Fib = context-only

Principle: keep the baseline's win-rate/SL/MAE discipline and opportunity coverage, while selectively importing only the V3-derived features that demonstrated positive BTC OOS separation.
