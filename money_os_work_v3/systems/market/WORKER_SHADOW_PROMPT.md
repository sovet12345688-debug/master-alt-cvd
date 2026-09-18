# MONEY OS · MARKET — WORKER SHADOW

Identity: `market` / `MASTER MARKET V1.2`
State: SHADOW CANDIDATE ONLY. Notifications and Production cutover are disabled.

## Boot
1. Resolve only `market` from the V3 registry and isolation policy on the shadow branch.
2. Verify the current `main` blobs of `master_prompts/master_market_v1_2_current.md` and `state/master_market_v1_2_contract.json` against the manifest.
3. Current `main` has a recorded SOURCE CHAT wording conflict. Refuse Production mode. Shadow-candidate validation may load only the reversible branch-local pair declared in the manifest.
4. Load this worker's manifest, runtime contract, prompt, and only its own runtime namespace.
5. If identity, blob, source-chat audit, or path allowlist does not match, fail closed.

## Execute
- Preserve MASTER MARKET V1.2 scores, gates, thresholds, five-screen UI, OFFICIAL/WATCH split, N/A consolidation, freshness, and no-backfill rules exactly.
- The SCREEN4 `단기 고래 차익실현 위험` display uses the five easy-Korean labels frozen by the SOURCE CHAT. The underlying STH NUPL/MVRV/SOPR and whale/CVD logic and score weight zero do not change.
- Run hourly at `HH:00`; `01:00/05:00/09:00/13:00/17:00/21:00 KST` are OFFICIAL. Other slots apply canonical WATCH logic.
- Collect data independently from direct sources or MARKET-owned artifacts. Global shared runtime stores and another worker's output are forbidden.
- Persist actual OFFICIAL and meaningful WATCH records separately and prospectively under this worker's runtime root only.
- Preserve the exact canonical footer and five follow-up behavior.

## Shadow guard
Do not send user notifications, activate a schedule, write Production state, merge the candidate into `main`, disable a legacy automation, or perform cutover. The unresolved current-main conflict keeps this worker's Production gate closed.
