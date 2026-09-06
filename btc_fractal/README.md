# BTC Historical Regime & Outcome Engine V0.3.2

Purpose: build and validate the independent historical-memory engine proposed for MASTER BTC TREND V2.6 **without changing MASTER BTC TREND scores, entry gates, schedule, or live plan**.

## Current status

- V0.1: technical PASS, statistical FAIL.
- V0.2: confidence and independent-analog fixes worked, but blended price/on-chain/macro model still failed to beat price-only.
- V0.3: price-core + on-chain/macro confirmation architecture improved apparent accuracy, but confidence degenerated and labeled every walk-forward row HIGH.
- V0.3.1: fixed the confidence degeneration and exposed the deeper problem: price-core behaved almost like an always-UP classifier and on-chain/macro confirmation did not add reliable OOS utility.
- V0.3.2: diagnostic repair candidate. It debiases the price-core vote, validates on independent test episodes, adds a naive majority benchmark, and only allows on-chain/macro to rerank a price-similar pool.

No version is integrated into MASTER BTC TREND. PR remains pre-integration research only.

## V0.3.1 failure causes

The V0.3.1 artifact showed four structural problems that raw accuracy hid:

1. Price-core predicted UP on almost all evaluable rows; balanced accuracy was near random and raw accuracy did not beat a naive always-UP benchmark.
2. Monthly walk-forward anchors use 365-day first-passage outcomes, so adjacent validation rows have heavily overlapping future windows. They are useful diagnostics but not independent acceptance samples.
3. Raw analog consensus was used as confidence even though the HIGH subset was not more accurate than the overall sample.
4. On-chain/macro usability was based on standalone historical similarity confidence, not incremental utility conditional on a comparable price regime.

## V0.3.2 design

### 1. Debiased price core

- Price structure remains the directional core.
- Historical analogs remain one-per-point-in-time Episode.
- Analog evidence is similarity-weighted.
- UP/DOWN evidence is adjusted using the point-in-time class prior computed from independent, fully-known historical Episodes only.
- The prior correction is inverse-square-root rather than full inverse weighting to reduce instability.
- If adjusted directional support is too close, the engine may `ABSTAIN` instead of forcing a direction.

### 2. Independent OOS acceptance

Monthly walk-forward output is still stored, but acceptance is based on the **first predeclared walk-forward anchor in each point-in-time test Episode**. The validation summary must show:

- number of independent test Episodes
- directional coverage
- raw accuracy
- balanced accuracy
- UP and DOWN recall separately
- prediction mix
- old price-core comparison
- naive point-in-time training-majority benchmark

A model that wins only by predicting UP most of the time cannot pass.

### 3. Confidence calibration

HIGH confidence is not accepted merely because the analogs agree. On independent OOS Episode rows, HIGH must have enough cases, limited coverage, and materially outperform the model's own overall accuracy. LOW/MEDIUM/HIGH also must not be materially inverted.

### 4. Price-conditional non-price confirmation

On-chain and macro are no longer allowed to search the full historical universe independently and call the result confirmation. V0.3.2 first creates a wider pool of price-similar independent Episodes; on-chain or macro may only rerank within that pool.

They still cannot change the price-core direction. They can only confirm, contradict, or abstain. Each domain must prove incremental OOS utility before it can be trusted.

## Locked V0.3.2 core acceptance

The candidate core must satisfy all gates on independent test Episodes:

- at least 30 independent Episodes
- at least 60% directional coverage
- balanced accuracy at least 55%
- balanced-accuracy gain over old price core at least +3pp
- raw accuracy no worse than naive point-in-time majority by more than 2pp
- both UP and DOWN recall at least 35%
- minority prediction share at least 10%

Confidence and non-price confirmation have separate acceptance gates. Layer B derivatives and Layer C ETF/institutional work remain blocked until the required validation gates pass.

## Point-in-time / leakage rules

- Only data available at the historical timestamp may enter features.
- Candidate analog outcomes must be fully known before the query timestamp.
- Class priors use fully-known historical Episodes only.
- Episode assignment is point-in-time and uses no future reversal information.
- N/A is excluded and never converted to zero.
- `MVRVZ_DERIVED_PIT` is a transparent point-in-time proxy, not a vendor-reported official MVRV Z-Score.
- Historical case/support shares are not calibrated probabilities.

## V0.3.2 outputs

- `output/v031_failure_analysis.json`
- `output/current_v032.json`
- `output/walk_forward_v032.csv`
- `output/episode_independent_v032.csv`
- `output/v032_validation_summary.json`
- legacy V0.3.1 comparison outputs are retained for audit

## Integration rule

`master_readiness=PASS` is necessary but still not sufficient for live integration. Even after statistical validation, MASTER BTC TREND remains unchanged until the user explicitly approves integration. PR #7 must not be merged merely because CI succeeds technically.
