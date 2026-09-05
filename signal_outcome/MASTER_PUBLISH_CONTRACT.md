# MASTER → SIGNAL OUTCOME VAULT publish contract

This contract is intentionally **read-only from the MASTER point of view**.

## Hard rule
Publishing a snapshot must never change a MASTER score, Gate, schedule, decision, position plan, or output. The Vault receives a copy of already-finalized information only.

## When integration is later approved
At the end of an actual MASTER run, create one new JSON file under `signal_outcome/inbox/` using a globally unique `signal_id`.

Example payload:

```json
{
  "signal_id": "MASTER-ALT-FINAL20-20260904-1345-TAO-LONG",
  "observed_at_utc": "2026-09-04T04:45:00Z",
  "source_master": "MASTER_ALT_FINAL20",
  "source_version": "CURRENT_AT_RUN",
  "run_id": "EXACT_RUN_ID_IF_AVAILABLE",
  "symbol": "TAOUSDT",
  "direction": "LONG",
  "observed_price": 123.45,
  "signal_label": "상승준비 후보",
  "scores": {
    "accumulation": 84,
    "early_rise": 81,
    "breakout_ready": 76
  },
  "evidence": {
    "large_flow": "CONFIRM",
    "derivatives": "NEUTRAL"
  },
  "tags": ["NON_CHASE", "LARGE_FLOW_CONFIRM"]
}
```

## Allowed data
- finalized score snapshots already computed by the MASTER
- finalized LONG/SHORT/WAIT or equivalent direction state
- current observed price actually used in that run
- finalized evidence labels already present in that run
- run/version identifiers

## Forbidden
- reconstructing old signals that were never published
- editing a signal after future price movement is known
- using future outcomes to change the original payload
- sending estimated/missing values as zero
- generating a signal solely for the purpose of improving statistics
- changing MASTER logic merely to satisfy Vault schema

## Integration phases
1. `DEVELOPMENT`: branch validation only; no MASTER publishes.
2. `SHADOW`: explicit user approval; MASTER emits snapshots but Vault statistics do not affect MASTER.
3. `CALIBRATION_READ_ONLY`: enough forward samples; Vault may publish evidence-based improvement suggestions.
4. `MASTER_CHANGE`: separate user review and explicit approval required. Never automatic.

## Sampling rule
Store actual finalized runs only. Do not backfill historical MASTER outputs from memory, screenshots, or reconstructed calculations.
