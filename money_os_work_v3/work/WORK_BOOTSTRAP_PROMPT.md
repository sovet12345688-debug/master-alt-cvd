# MONEY OS · ONE-TIME WORK BOOTSTRAP PROMPT

Use this in a new ChatGPT Work conversation inside the MONEY project after the V3 review branch is approved for shadow operation.

Shadow package state as of 2026-09-14: all six SOURCE CHAT bodies have been inspected, six per-system Worker Shadow packages pass validation, and seven inactive Work surface specs are prepared. Verify `audit/SOURCE_CHAT_PARITY_20260914.json` and `audit/WORKER_SHADOW_VALIDATION_20260914.json` before launch. Do not reinterpret this prepared state as schedule activation or Production cutover.

---

Build and operate `MONEY OS` from GitHub repository `sovet12345688-debug/master-alt-cvd`.

## BRANCH PRECEDENCE — CRITICAL
During shadow validation, use two distinct branches for two distinct purposes:

1. **V3 architecture/control files** → read from `money-os-work-v3-isolated-20260914`.
2. **Five existing production canonicals/contracts and any system-owned current production artifacts** → always resolve from the current `main` head immediately before each run.

Do NOT treat the shadow branch as a live market-data branch. `main` continues receiving scheduled runtime updates while the shadow branch is intentionally isolated. A data-only commit on `main` does not require rebasing the V3 architecture branch.

Before booting a worker, compare the current `main` canonical blob with `money_os_work_v3/audit/LATEST_VERSION_AUDIT_20260914.md`. If the blob changed, do not silently use the old shadow copy: inspect the new `main` canonical, confirm it reflects a later user-approved change, then update that worker's V3 manifest/version audit in the shadow branch before continuing.

The exception is `youtuber_view`, which had no prior durable `main` canonical. During shadow it uses `money_os_work_v3/systems/youtuber_view/CANONICAL_RULES.md` from the V3 branch and must pass a manual parity run before production cutover.

## SOURCE CHAT PARITY — MANDATORY
The user supplied one exact source conversation for each of the six systems. Read `money_os_work_v3/audit/CHAT_SOURCE_LINKS_20260914.md` from the V3 branch and inspect each linked conversation in full before that worker can be declared cutover-ready.

For each system compare:
- latest approved version/identity;
- latest UI/output lock;
- schedule/manual mode;
- scoring/formulas/gates;
- data source/N/A/freshness behavior;
- history/persistence/no-backfill rules;
- later approved overlays/patches that did not cause a version bump;
- research-only material that must not replace production;
- manual image/chart workflow;
- footer/follow-up behavior.

If a source chat conflicts with GitHub canonical or the V3 manifest, mark `CHAT↔CANONICAL CONFLICT`, stop only that worker's cutover, and prepare a reversible system-scoped patch. Never use another worker to resolve or fill the conflict.

Read V3 control files first, from `money-os-work-v3-isolated-20260914`, in this order:
1. `money_os_work_v3/README.md`
2. `money_os_work_v3/registry/SYSTEM_REGISTRY.json`
3. `money_os_work_v3/registry/ISOLATION_POLICY.json`
4. `money_os_work_v3/audit/LATEST_VERSION_AUDIT_20260914.md`
5. `money_os_work_v3/audit/CHAT_SOURCE_LINKS_20260914.md`
6. `money_os_work_v3/work/MONEY_OS_WORK_INSTRUCTIONS.md`
7. `money_os_work_v3/overlays/RUNTIME_ISOLATION_OVERLAY.md`
8. `money_os_work_v3/work/WORK_TASK_BLUEPRINTS.md`
9. `money_os_work_v3/work/CHANGE_SYNC_POLICY.md`

Then prepare the following seven user surfaces without changing any existing system UI:
- `MONEY OS · CONTROL` — routing/status only, no market data
- `MONEY OS · ALT 1`
- `MONEY OS · ALT 2`
- `MONEY OS · MARKET`
- `MONEY OS · BTC TREND`
- `MONEY OS · 유튜버 관점`
- `MONEY OS · TRADING`

Absolute isolation is mandatory. Each analytical worker may load only its own canonical, its own explicitly approved system-owned artifacts, direct external sources and its own V3 runtime namespace. Never read another MONEY worker's stored values, state, history, source-health, score, direction, permission or output.

Preserve current schedules exactly as declared in the V3 registry. Keep YouTuber and TRADING manual. Keep BTC precision manual-image driven. Do not create duplicate user notifications during shadow validation.

For recurring scheduled workers, bootstrap from current `main` canonical + direct/system-owned sources rather than relying on project-uploaded files or frozen shadow-branch market snapshots. For manual BTC precision, YouTuber and TRADING sessions, user-uploaded chart images may be used only inside the invoked worker.

Do not merge the V3 branch, disable existing Chat automations, or cut over production during shadow validation. Complete all reversible preparation and produce a final cutover report with pass/fail for each of the six systems. Ask for approval only at the final merge/cutover step.

---
