# MONEY MASTER OS — Source of Truth Layer

Purpose: make GitHub the durable source-of-truth for MASTER continuity across ChatGPT room migrations.

## Operating rule
- GitHub stores canonical version identity, source pointers, state/handoff contracts, bootstrap rules, and validation.
- Chat rooms are execution/analysis surfaces, not the canonical archive.
- Existing collectors, market data, histories, and current prompt files remain untouched.
- A MASTER with `VERSION_DRIFT` or `SOURCE_MISSING` is blocked from automatic bootstrap until its exact current canonical prompt is committed.

## Current audit baseline — 2026-09-07 KST
- MASTER MARKET: repository canonical source exists and matches V1.2 FINAL.
- MASTER ALT: repository source exists as V2.2.1, while project-current target is V4.8; automatic migration is blocked to prevent silent downgrade.
- MASTER BTC TREND: project-current target is V3.0, but no exact canonical V3.0 prompt source was found in this repository; automatic migration is blocked.

## Layout
- `registry/MASTER_REGISTRY.json`: machine-readable version/source/status registry.
- `shared/`: common non-negotiable policies.
- `masters/*/manifest.json`: per-MASTER ownership, source, state, and bootstrap contract.
- `bootstrap/BOOTSTRAP_LATEST.md`: room-migration loader contract.
- `handoff/HANDOFF_TEMPLATE.json`: exact state handoff schema.
- `tools/validate_master_os.py`: structural and anti-drift validator.
- `.github/workflows/money_master_os_guard.yml`: automatic validation workflow.

## Safety invariant
Never reconstruct a missing MASTER from memory and call it canonical. Missing or drifted sources must remain blocked until the exact approved source is committed and the registry is updated.
