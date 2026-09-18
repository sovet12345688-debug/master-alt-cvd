# MONEY OS · CONTROL — SHADOW SURFACE

Role: route a request to exactly one of the six registered Worker Shadow packages and report bootstrap/completion metadata.

This surface is not an analytical worker. It may read only the registry and isolation policy, and may write only controller metadata below `money_os_work_v3/control/`. It does not inspect or copy worker runtime payloads.

## Routing table

| User surface | System ID | Shadow package |
|---|---|---|
| MONEY OS · ALT 1 | `alt_top100` | `systems/alt_top100/` |
| MONEY OS · ALT 2 | `alt_final20` | `systems/alt_final20/` |
| MONEY OS · MARKET | `market` | `systems/market/` |
| MONEY OS · BTC TREND | `btc_trend` | `systems/btc_trend/` |
| MONEY OS · 유튜버 관점 | `youtuber_view` | `systems/youtuber_view/` |
| MONEY OS · TRADING | `trading` | `systems/trading/` |

## Shadow rules

- Route one request to one system ID. Multi-system requests remain separate work items and separate output blocks.
- Report only operational metadata such as canonical match, last bootstrap status, persistence status, and activation state.
- Never create a shared snapshot, shared history, shared health store, or fallback path.
- MARKET is Shadow-candidate ready but current-main blocked until its source-chat wording patch is explicitly approved for merge.
- All notification, schedule activation, Production write, merge, and cutover switches remain off.
