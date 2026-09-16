# MASTER TRADING — BASELINE+ V3 EDGE V1 (BTC DAILY)

Status: RESEARCH / SHADOW ONLY
Production canonical/UI: UNCHANGED
Purpose: keep CURRENT MASTER TRADING as the authoritative setup/execution engine, and import only the BTC advantages demonstrated by V3 research.

## 1. Why baseline remains primary
BTC OOS comparison showed CURRENT baseline had slightly better win rate, lower SL rate, and lower average MAE, while V3 had higher expectancy, PF, MFE and lower cumulative drawdown with far fewer trades. Therefore V3 must NOT replace the baseline generator/gates.

Baseline continues to own:
- direction/scenario engine
- Entry Zone outer structure
- Trade Frame Lock
- Structural SL / invalidation
- TP1/TP2/TP3 and >=3R gate
- 15m/30m completed-candle Trigger
- SMALL ENTER -> ADD logic
- Non-Chasing / Risk Veto / Time Validity
- Screen 1~6 UI

## 2. V3 components promoted into a non-destructive EDGE OVERLAY
Only the following V3 strengths are imported.

### A. Setup quality ranking — internal 20 points
This score does not create or cancel a baseline setup.
- MTF structure/location quality: 6
- Volume + CVD/Taker participation: 5
- Session VWAP / AVWAP / PDH-PDL / value confluence: 4
- ATR / extension / non-chase quality: 3
- direction-specific flow alignment: 2
- Fibonacci: 0 points, context-only

### B. Preferred Entry Pocket inside the existing baseline Entry Zone
The baseline Entry Zone is never moved or expanded by the overlay.
Within that zone, select a preferred pocket when verified confluence exists:
1) baseline S/R / swing structure,
2) session VWAP or anchored VWAP,
3) PDH/PDL/previous close/session open,
4) Volume Profile POC/HVN/LVN when available,
5) 15m/30m completed reaction,
6) aligned CVD/Taker/volume participation.

The existing 1st/2nd entry prices may be placed preferentially inside this verified pocket, but the outer structural Entry Zone and Structural SL remain baseline-owned.

### C. Action strictness instead of hard filtering
EDGE >=14/20: HIGH QUALITY. Existing baseline setup may receive BEST priority. SMALL ENTER remains allowed only if the canonical E1 gate already passes.
EDGE 9~13: STANDARD. Keep baseline treatment; completed trigger preferred.
EDGE <=8: LOW QUALITY. Do not delete the setup; require completed 15m/30m trigger + participation before execution. Default WATCH/WAIT until confirmation.

This preserves opportunity count while using V3 information to concentrate conviction.

## 3. Preserve baseline strengths
Because V3 did not improve BTC win rate / SL rate / MAE, the following are explicitly NOT imported from the V3 proxy:
- next-15m-open proxy entry
- any hard V3 >=70 filter
- wider stop or new stop formula
- V3 score as ENTER permission
- Fib score contribution
- any rule that bypasses the baseline 15m/30m completed Trigger

## 4. Fibonacci final rule
Fibonacci Retracement / Extension stay visible as context only.
- score contribution: 0
- never create Entry, SL or TP
- useful only when overlapping baseline structure/SR/VWAP/Volume Profile/liquidity target
- Fibonacci Time remains OFF

## 5. Internal execution order
1D context
-> 4H regime
-> CURRENT baseline setup generation
-> 1H structural Entry Zone / Structural SL / TP map
-> V3 EDGE OVERLAY quality ranking
-> Preferred Entry Pocket inside baseline zone
-> 15m/30m completed Trigger
-> volume/CVD/Taker participation confirmation
-> Non-Chasing / Risk Veto / Time Validity
-> R:R >=3
-> ENTER / SMALL ENTER / WATCH / WAIT / AVOID

## 6. UI behavior
Existing SCREEN 1~6 remains unchanged.
No new mandatory visible table is added for the daily engine.
The overlay may only affect existing fields:
- which setup is marked ⭐ BEST
- 1차 / 2차 preference inside the already-derived Entry Zone
- 핵심 근거
- Entry Quality
- Action strictness
It must not alter the locked screen order/labels.

## 7. BTC evidence target
The overlay is considered useful if BTC OOS score tiers show monotonic or materially higher Expectancy/PF/MFE in higher-quality tiers while the baseline universe is preserved. This is a ranking validation, NOT a replacement-engine promotion test.

## 8. Final intended architecture
CURRENT MASTER TRADING = authoritative engine
V3 EDGE = quality/ranking/entry-pocket overlay
Fib = context-only

Principle: preserve the baseline's win-rate/entry-discipline advantages, while importing V3's setup-selection and move-capture advantages without discarding most baseline opportunities.
