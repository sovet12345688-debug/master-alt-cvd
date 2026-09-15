# MONEY OS WORK — TASK BLUEPRINTS

These are target Work surfaces. They do not alter the current Chat automations until final cutover approval.

| Work surface | Type | Cadence | User input | Durable output root |
|---|---|---|---|---|
| MONEY OS · ALT 1 | Scheduled + interactive | HH:30; 10:30 OFFICIAL | optional manual question | `systems/alt_top100/runtime/` |
| MONEY OS · ALT 2 | Scheduled + interactive | HH:45; 01:45/05:45/09:45/13:45/17:45/21:45 OFFICIAL | optional manual question | `systems/alt_final20/runtime/` |
| MONEY OS · MARKET | Scheduled + interactive | HH:00; 01/05/09/13/17/21 OFFICIAL | optional manual question | `systems/market/runtime/` |
| MONEY OS · BTC TREND | Scheduled + interactive | 05/08/13/17/21 OFFICIAL | chart images for precision report | `systems/btc_trend/runtime/` |
| MONEY OS · 유튜버 관점 | Persistent manual Work conversation | manual/event-driven | screenshots/chart images/viewpoint materials | `systems/youtuber_view/runtime/` |
| MONEY OS · TRADING | Persistent manual Work conversation | manual only | chart images/current trading request | `systems/trading/runtime/` |

## Generic scheduled worker prompt
Use only for the selected system and replace placeholders from the V3 registry.

1. Read `money_os_work_v3/registry/SYSTEM_REGISTRY.json` and resolve exactly `<SYSTEM_ID>`.
2. Read only `<SYSTEM_ID>` canonical and machine contract.
3. Read only `<SYSTEM_ID>` runtime namespace.
4. Do not read another MONEY system's runtime/output/history/source-health.
5. Collect the selected system's required current external data directly.
6. Execute the existing canonical without changing visible UI.
7. Persist only actual current run data prospectively to `<SYSTEM_ID>/runtime`.
8. For OFFICIAL, append actual OFFICIAL state/history only. For WATCH, write only meaningful WATCH events according to canonical. Do not mix them.
9. If persistence fails, do not invent stored history. Preserve the analytical report and mark persistence pending internally.
10. If canonical/version cannot be verified, fail closed rather than rebuilding from conversation memory.

## MARKET Work task
Canonical: `master_prompts/master_market_v1_2_current.md`.
Keep exact 5-screen output and consolidated N/A behavior. Real source N/A remains visible per canonical. Do not consume global Shared Fact Vault, global Source Health, another MASTER output, or another system's stored market values.

## ALT 1 Work task
Canonical: `master_prompts/master_alt_top100_v4_8_current.md`.
Keep exact 4-screen DAILY OFFICIAL and HUNTER WATCH behavior. Other MASTERs are never inputs. Maintain TOP100 discovery/history prospectively in ALT1 local storage.

## ALT 2 Work task
Canonical: `master_prompts/master_alt_final20_current.md`.
Keep exact current visible layout, FINAL20 selection, CVD quality rules, baseline/outcome rules and WATCH semantics. Replace global development/outcome storage dependencies with ALT2-owned local persistence during backend migration; do not change user-visible layout.

## BTC TREND Work task
Canonical: `master_prompts/master_btc_trend_v2_6_current.md` plus its current machine/UI contracts.
Keep V2.6 production and V3.0 research separation. Keep exact 3-screen BASIC OFFICIAL. No hourly WATCH. Precision report starts when the user supplies the currently required chart images/manual request; images belong to BTC only.

## YouTuber View Work task
Canonical: `money_os_work_v3/systems/youtuber_view/CANONICAL_RULES.md` V2 wrapper plus current room-visible UI as frozen presentation.
Manual/event-driven. Every new forecast is stored before outcome resolution. View changes append rather than overwrite. Creator View and MY VIEW stay separate. Later outcome scoring uses only subsequent data. No retroactive records. User-supplied screenshots/charts belong only to this system.

## TRADING Work task
Canonical: `master_prompts/master_trading_current.md` plus machine contract and `money_master_os/masters/trading/MASTER-TRADING-UI-V2-FINAL.md`.
Manual only. No recurring schedule. Directly validate Current/Entry/Trigger/SL/TP/R:R and preserve the exact 4-semantic-section/6-visual-screen format. Never use another MONEY system's conclusion or stored market fact as an execution gate or fallback.

## MONEY OS CONTROL Work conversation
This surface is not a seventh analytical system. It may:
- open/route the user to the requested worker;
- show operational health such as `last successful run / persistence status / canonical match` for each worker, based on controller metadata only;
- start independent work items.

It may not:
- calculate a combined market score;
- compare or reconcile stored conclusions automatically;
- copy a system's analytical values into another system;
- become a shared fact/history layer.
