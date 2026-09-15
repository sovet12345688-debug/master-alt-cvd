# MONEY OS — SIX SOURCE CHAT LINKS FOR FINAL PARITY AUDIT

Added: 2026-09-14 KST
Purpose: these are the exact user-supplied source conversations that must be treated as the final human-approval provenance check before Production cutover.

| System | Source chat |
|---|---|
| ALT 1 | https://chatgpt.com/share/6aa75306-9b30-83e8-bc35-b936e6b5cb44 |
| ALT 2 | https://chatgpt.com/share/6aa75505-05a4-83ee-b68b-08c60340146a |
| MARKET | https://chatgpt.com/share/6aa7551d-a41c-83e8-bba8-45349f6fe6db |
| BTC TREND | https://chatgpt.com/share/6aa75536-cc28-83e9-90a1-b654d737a310 |
| 유튜버 관점 | https://chatgpt.com/share/6aa75553-eee8-83ee-acd5-9e532996d85f |
| TRADING | https://chatgpt.com/share/6aa75566-7a70-83e8-9537-e11634a8be5e |

## Mandatory verification inside Work
For each link, inspect the full conversation and extract only the latest user-approved state. Compare it against the current `main` canonical and the V3 worker manifest.

Required comparison axes:
1. latest approved version/identity;
2. latest approved UI/output structure and wording locks;
3. schedule vs manual mode;
4. scoring/formulas/gates/thresholds;
5. data-source and N/A/freshness behavior;
6. history/persistence/no-backfill rules;
7. later user-approved patches or overlays that did not cause a version bump;
8. research-only material that must not replace production;
9. manual image/chart workflow requirements;
10. follow-up/footer behavior when applicable.

If source-chat content conflicts with a GitHub canonical, do not guess. Mark `CHAT↔CANONICAL CONFLICT`, stop that worker's cutover, and prepare a system-scoped reversible patch for review. Do not propagate the conflict to another worker.

No Production cutover for a system until its source-chat parity result is PASS.

## Completion record

All six conversations were inspected in full on 2026-09-14. Extraction metadata, SHA-256 evidence, ten-axis findings, reversible fixes, and per-worker results are recorded in `SOURCE_CHAT_PARITY_20260914.md` and `SOURCE_CHAT_PARITY_20260914.json`. MARKET passes only with the Shadow candidate and remains a current-main cutover hold.
