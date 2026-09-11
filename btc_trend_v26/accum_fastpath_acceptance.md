# ACCUM Fastpath V1 Acceptance Gates

Promote from research/shadow to V2.6 LIMITED DISPLAY only if all gates pass:

1. Unit tests pass.
2. Live shadow run completes successfully on the branch.
3. Coverage is at least 50% of canonical ACCUMULATION weight.
4. The score remains 0-100 and obeys the rapid-rise max-64 cap when the proxy is active.
5. Same-run artifact and SHA256 provenance are generated.
6. Engine has zero direct official-state write authority.
7. Coverage below 70 has zero independent Entry Gate effect and zero effect on LONG:SHORT, TREND_STRENGTH, and LIVE PLAN.
8. No unapproved ACCUMULATION feature is silently filled.
9. Promotion requires an explicit bridge contract + manifest change after the live shadow result is reviewed.
