# MONEY MASTER OS — NEW ROOM BOOTSTRAP

Use this contract whenever a MASTER is moved to a new ChatGPT room.

## Required load order
1. Read `money_master_os/registry/MASTER_REGISTRY.json`.
2. Select the requested MASTER entry only.
3. Read its `manifest_path`.
4. Read all listed `shared_dependencies`.
5. Verify `status`, `expected_version`, `repo_version`, `source_path/canonical_source`, and `bootstrap_allowed`.
6. If status is `VERSION_DRIFT` or `SOURCE_MISSING`, STOP automatic restoration. Report the block; never reconstruct from memory.
7. If status is `READY`, read the exact canonical source and machine contract/state pointers.
8. Read the latest valid handoff/state snapshot if one exists. Missing historical state must remain missing; do not backfill from conversation memory.
9. Run or verify MONEY MASTER OS validation status.
10. Before any MASTER execution, print a compact bootstrap receipt:
   - MASTER
   - expected version
   - repository version
   - source path
   - source SHA/commit if available
   - state/handoff status
   - validation PASS/BLOCKED

## Authority order for migration identity
`Registry > MASTER Manifest > Canonical Source/Contract > Persisted State/Handoff > Chat context`

Chat context may add current user instructions, but it must not silently downgrade/replace the canonical version or invent missing persisted history.

## Current safe bootstrap state at creation
- MASTER MARKET V1.2 FINAL: READY.
- MASTER ALT V4.8 FINAL target: BLOCKED by repository VERSION_DRIFT (stored source V2.2.1).
- MASTER BTC TREND V3.0 target: BLOCKED by SOURCE_MISSING.

## Migration command convention
A user can say, for example: `새 방이야. MASTER MARKET 복원.`
The assistant should then follow this file instead of asking the user to paste the entire old prompt again.
