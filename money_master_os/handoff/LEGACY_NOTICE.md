# LEGACY HANDOFF NOTICE

`MARKET_LATEST.json`, `BTC_TREND_LATEST.json`, and `ALT_LATEST.json` were created before the final five-MASTER V2 identity/persistence structure.

They are retained for non-destructive migration lineage only.

## Effective authority after OFFICIAL State V1
- Latest actual MASTER OFFICIAL decision: `official_state/latest/<master_id>.json`
- Five-MASTER OFFICIAL index: `official_state/latest/index.json`
- Canonical identity: `money_master_os/registry/MASTER_REGISTRY.json`

## Forbidden use
- Do not use legacy handoff values to fill a `NO_STORED_OFFICIAL_RUN` state.
- Do not treat the old BTC V3/SOURCE_MISSING handoff as current BTC V2.6 production state.
- Do not treat the old mixed ALT handoff as either ALT TOP100 V4.8 or ALT FINAL20 V2.2.1 OFFICIAL state.
- Do not overwrite a newer OFFICIAL State with a legacy handoff.
- Do not reconstruct missing history from these files.

Legacy handoffs may be read only to explain migration lineage or diagnose old-version drift.
