# BTC S/R STRENGTH V0.1 — RESEARCH SPEC

Status: RESEARCH ONLY. No MASTER score or gate effect until walk-forward validation and explicit user approval.

## Objective
Convert already-identified BTC support/resistance zones into an independent `Support Strength /100` or `Resistance Strength /100` that answers: how likely is this level to produce a meaningful reaction before a structural break?

This is NOT a future-return probability and must not bypass Entry/Safety/NonChase/SL/R:R gates.

## Timeframe roles
- Short: 1H/4H
- Medium: 4H/1D
- Long: 1D/1W

## Score /100
1. Multi-timeframe confluence — 20
   - same price zone independently visible on adjacent higher/lower timeframe
2. Historical reaction quality — 20
   - prior defended/rejected reactions, normalized by ATR/volatility
3. Role-flip evidence — 15
   - resistance→support or support→resistance confirmation
4. Participation/volume confirmation — 15
   - reaction occurred with meaningful volume/participation; no volume spike alone
5. Freshness / retest degradation — 15
   - first/second clean test favored; repeated tests reduce remaining strength
6. Structural alignment — 10
   - support gets credit in intact bullish structure; resistance in intact bearish/distribution structure
7. Bitget/OKX price-zone corroboration — 5
   - confidence only; never duplicate underlying reaction points

Total = 100.

## Critical anti-false-positive rules
- Repeated touches do not always strengthen a level. After the second meaningful retest, additional tests can reduce `remaining strength`.
- A large wick alone is insufficient without close/reclaim/rejection context.
- In-progress candles cannot count as completed defense/rejection.
- No future pivot information. A pivot becomes usable only after it would have been known point-in-time.
- Support and resistance are scored separately; no inverse shortcut.
- Missing subcomponents are removed and remaining weights renormalized. N/A is never zero.

## Validation target
Historical point-in-time event test on BTC 1H/4H/1D where each event is a first entry into a pre-existing zone.

Support success example: after first touch, price reaches +2 ATR (or horizon-specific calibrated reaction threshold) before a confirmed structural break below the zone.
Resistance success: mirror definition.

Primary validation:
- Score bands should be monotonic: higher score => higher observed reaction/hold rate.
- Compare score>=80 versus all valid levels.
- Walk-forward / leave-era-out only; no full-sample threshold fitting.
- Separate short/medium/long horizon results.
- Minimum initial research target: >=50 matured events per side where history allows.

Provisional grade labels MUST remain hidden until calibration. Candidate labels after validation only:
- 85+ very strong
- 75–84 strong
- 65–74 meaningful
- 50–64 moderate
- <50 weak

## MASTER integration contract after validation
Visible table candidate:
`구분 | 핵심 지지 | 지지강도 | 핵심 저항 | 저항강도`

Example values must come only from the actual score engine; never manually invented.

The score remains a read-only decision aid. It may prioritize which pre-existing zone to watch, but cannot create a new zone or authorize Entry/SL/TP by itself.
