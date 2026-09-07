# MASTER BTC TREND OFFICIAL State Inbox

This directory is transport-only.

- Accept only actual completed MASTER BTC TREND V2.6 BASIC OFFICIAL state JSON payloads.
- Normal file name: `MBTC-V26-YYYYMMDD-HH10-KST.json`.
- Do not backfill old runs from chat memory, legacy handoff, or V3.0 research.
- Do not place WATCH, provisional, draft, dry-run, research-only, or private account/position data here.
- A push of `*.json` to this directory triggers `.github/workflows/btc_trend_official_state_bridge.yml`.
- The workflow calls `official_state/process_btc_trend_inbox.py`, which delegates final validation/publication to `official_state/publish_official_state.py`.
- On success the inbox file is removed and the publisher updates `latest/btc_trend.json`, append-only monthly history, and `latest/index.json`.
- Invalid payloads are moved to `official_state/rejected/btc_trend/` with an error sidecar.
- Persistence failure does not invalidate an already valid user-visible current report; it becomes `PERSISTENCE_PENDING`.
