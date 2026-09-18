# MONEY OS WORK V3 — CHANGE SYNC POLICY

Goal: remove the current manual step of noticing a chat change and separately asking for a GitHub patch.

## Rule
When the user explicitly changes a rule, formula, schedule, persistence behavior, data source, or UI of one MONEY system, that explicit instruction is permission to prepare the matching GitHub update for that same system.

For reversible changes, Work should complete the system-scoped patch and review artifact without asking a separate 'GitHub에 반영할까요?' question.
Final merge/cutover or an irreversible/destructive action remains an approval point.

## Required flow
1. Identify exactly one owning system.
2. Load only that system's canonical/contract/runtime metadata.
3. Apply the requested change only to that system's canonical/contract/files.
4. If the change affects visible UI, apply it only because the user explicitly requested that UI change; otherwise UI remains frozen.
5. Create/update a system-scoped review branch or PR.
6. Show `what changed / what did not change / risk / rollback`.
7. Merge only at the final approval point when the change is contract-breaking or production-cutover relevant.

## Forbidden
- Do not patch another MONEY system because the same data concept appears there.
- Do not update a shared fact/schema/score layer across systems.
- Do not propagate one system's new threshold/formula to another.
- Do not use a global 'sync all masters' patch.
- Do not silently redesign the visible UI.

## Backend maintenance
A system may automatically repair its own reversible collector/schema/path issue in a shadow/review branch when that repair preserves the approved analytical/output contract. It must not wait for user approval for every small reversible edit. If repair would change analytical meaning, score, threshold, or visible output, stop at the review artifact and require the user's final approval.

## Result
The user gives the analytical/product instruction once. Work handles the matching system-local GitHub persistence automatically, so chat-room rules and GitHub canonicals cannot drift merely because a separate manual patch step was forgotten.
