# MASTER BTC TREND OFFICIAL State Inbox

This directory is the single producer-to-bridge transport path for MASTER BTC TREND V2.6 OFFICIAL persistence.

- Accept only actual completed MASTER BTC TREND V2.6 BASIC OFFICIAL state JSON payloads.
- Scheduled file name: `MBTC-V26-YYYYMMDD-HH00-KST.json`.
- Explicit manual OFFICIAL file name: `MBTC-V26-YYYYMMDD-MANUAL-HHMM-KST.json`.
- Producer-side code must write only through `official_state/enqueue_btc_trend_official_state.py` or create the equivalent validated JSON in this inbox. Producer-side direct writes to `latest/btc_trend.json`, BTC TREND history, or the BTC TREND index entry are forbidden.
- Do not backfill old runs from chat memory, legacy handoff, or V3.0 research.
- Do not place WATCH, provisional, draft, dry-run, research-only, or private account/position data here.
- A push of `*.json` to this directory triggers `.github/workflows/btc_trend_official_state_bridge.yml`.
- The bridge runs `official_state/check_btc_trend_persistence.py` before processing. A pre-existing latest/index/history mismatch blocks publication until consistency is repaired.
- The workflow calls `official_state/process_btc_trend_inbox.py`, which delegates final validation/publication to `official_state/publish_official_state.py`.
- On success the inbox file is removed and the bridge commits `latest/btc_trend.json`, append-only monthly history, and `latest/index.json` together.
- Invalid payloads are moved to `official_state/rejected/btc_trend/` with an error sidecar; failed validation must not commit partial official targets.
- `.github/workflows/btc_trend_persistence_guard.yml` detects protected-target writes that bypass the bridge and reverts an unauthorized direct write when it is the current main tip.
- Persistence failure does not invalidate an already valid user-visible current report; it becomes `PERSISTENCE_PENDING`.
