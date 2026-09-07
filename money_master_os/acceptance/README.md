# MONEY MASTER OS V2 — P2 Acceptance

P2 Acceptance is deliberately split into two gates.

## Gate A — Baseline Acceptance
Runs immediately against the current repository/control-plane state. It validates identity/canonical integrity, new-room restore, CORE data-health safety, Shared Fact authority boundaries, OFFICIAL/WATCH separation, five-MASTER end-to-end publishing/restore, BTC production/research separation, and public-repository privacy.

A Baseline PASS does **not** mean P2 is finished. It means the system is structurally acceptable at the current checkpoint.

## Gate B — Long-run Stability
Required before P2 FINAL PASS. Minimum observation window is 24 hours with multiple Source Health, Shared Fact and Acceptance checkpoints. This gate is intended to detect drift, repeated CORE data outages, OFFICIAL/WATCH contamination, restore regression and privacy regression that a single checkpoint cannot prove absent.

## Degraded-data rule
- A CORE source in FAILED/STALE/MISSING/UNKNOWN => hard FAIL.
- OPTIONAL/CONTEXT failures => warning only, provided the MASTER can degrade safely and no CORE source is affected.
- Source Health is availability metadata, never trade permission.
- `NO_STORED_OFFICIAL_RUN` is valid and must never be reconstructed.

Machine-readable authority: `money_master_os/acceptance/ACCEPTANCE_CRITERIA_V1.json`.
Baseline runner: `money_master_os/tools/run_p2_acceptance.py`.
