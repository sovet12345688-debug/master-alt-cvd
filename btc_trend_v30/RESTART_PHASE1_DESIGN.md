# MASTER BTC TREND V3.0 — RESTART PHASE 1 DESIGN

Status: RESEARCH ONLY. V2.6 / production modification forbidden.

## Purpose
Detect the birth of a major BTC medium/long trend early enough to capture its body, without optimizing the system into permanent WAIT. A false early probe with bounded structural loss is not treated the same as a missed +30~50% trend.

## What is changed vs old Phase1.1~1.4
1. Do not optimize only pivot precision/recall. Primary research objective is utility: Move Capture + early detection + expectancy - false-start loss - missed-trend penalty.
2. Fractal is never a hard gate. Compare NO_FRACTAL / SOFT_ADD / RANK_BOOST / INTERACTION in identical OOS folds.
3. Origin Candidate and Confirmation are separate. Candidate may be early; confirmation is a sizing accelerator, not a prerequisite for existence of a candidate.
4. Zone Quality and Reaction Strength are separate. Zone Quality must exist before touch. Reaction is evaluated only after touch.
5. Pre-touch Zone Quality is no longer MA-confluence-only. It includes prior pivots, role reversal, freshness, repeated-test weakening, displacement origin, higher-timeframe overlap, prior reaction efficiency, distance normalized by ATR, and only point-in-time information.
6. Historical OI/Funding/CVD that do not exist are N/A, never synthesized. Taker-flow proxy from Binance spot is allowed only as a price/flow feature.

## Event / outcome design
Labels may use future data; prediction features may not.
- LONG major-origin outcome: +30% before -15% within 180D.
- SHORT major-origin outcome: -25% before +15% within 180D.
- Truth origin anchors are clustered ex-post pivot candidates that subsequently produce the qualifying major outcome. Labels are for research evaluation only.
- Evaluation also records 30/90/180/365D path, MFE, MAE, first-passage, detection lag, localization error and independent episode de-duplication.

## Phase1 candidate score
PIT-only chart features:
- opposing prior trend context
- HH/HL/LH/LL transition / failed new-low or new-high / reclaim / failed break
- wave-energy deceleration and price-efficiency change
- candle force
- EMA20 / MA50 / MA200 slope, alignment, compression and reclaim
- RSI regime/divergence proxy, KDJ turn
- volume climax/dry-up/expansion
- spot taker-flow efficiency vs price

The score is continuous. Thresholds are derived only from training folds and constrained to broad stable quantiles; no yearly micro-parameter search intended to maximize one fold.

## Fractal prior experiment
Identical OOS comparison:
- NO_FRACTAL
- SOFT_ADD: bounded +/- 8 score points
- RANK_BOOST: fractal rank only re-ranks similar candidates
- INTERACTION: fractal can add confidence only when structure+wave agree; conflict can trim but never delete.

Fractal analogs must be fully knowable at query time. A 180D analog outcome requires analog date <= query-181D.

## Zone Quality V2
Pre-touch features only:
- pivot density / prior swing clustering
- role reversal count
- untouched/freshness
- repeated-test weakening penalty
- displacement-origin proximity
- HTF overlap
- historical prior reaction efficiency
- ATR-normalized distance / width
- MA or rolling structure confluence as only one feature, not the score itself

Zone outcome is evaluated by realized R multiple after touch using a structural invalidation derived before/at registration. Zone Quality must show OOS monotonicity by score bucket before any MASTER use.

## OOS protocol
- Walk-forward by year from 2020 onward.
- All thresholds / transforms / empirical calibrations use earlier data only.
- Add leave-one-cycle-out audit after Phase1 candidate architecture stabilizes.
- Same independent episode cannot be counted repeatedly.

## Primary evaluation
Not one accuracy number. Record:
1. Move Capture Ratio proxy
2. Trend Origin detection lag
3. origin price localization error
4. signal expectancy / path utility
5. False Start Loss
6. Missed Trend Rate
7. precision/recall as diagnostics
8. score monotonicity
9. LONG / SHORT separately

## Phase1 promotion gate
Phase1 is not MASTER-ready unless both LONG and SHORT produce positive path utility, missed-trend rate improves versus the old Phase1 baseline, score tiers are directionally monotonic with adequate samples, and no anti-leakage / isolation guard fails. Exact probability display remains forbidden until a later calibration phase.
