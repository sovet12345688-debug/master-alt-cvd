# CODEX CHANGE HANDOFF

**Change ID**: MONEY-V4-W5-BTC-DAILY-20260918-01

**Date KST**: 2026-09-18

**User intent**: Include the existing Trading Room as one MONEY OS V4 worker;
reconfigure BTC daily execution using January 2024–September 2026 backtests with
0.06% fee +0.02% slippage per side and historical funding. Seek net win >=50%,
realized payoff >=2, and first fill within 24h >=50%.

**Current canonical**: MONEY OS V4 audited migration spec already assigns final
execution ownership to W5. Legacy MASTER TRADING is CURRENT + TIME VALIDITY V2.1,
manual only. Main snapshot e65510ce8c62ca77abb83a86c6bb09a1c8488831 contains no
money_os_v4 directory. Legacy rules are preserved; no official private trade
history is reconstructed. Prior price-proxy backtests are not Champion results.

**Requested change**: Name and expose W5 TRADING ROOM / EXECUTION with a BTC_DAILY
profile. Add typed shadow adapter, candidate plan builder, event replay, explicit
cost/risk accounting, maturity-aware fill denominators and adoption gates.
The research acceptance targets are encoded, not assumed achieved. After 216
initial and 36 pre-holdout refinement variants, none qualified for adoption.
The one fixed diagnostic candidate returned 20.69% win, 2.366 payoff, 82.86% first
fill, and −0.1443R per filled plan in the 2026 check. Release status is
RESEARCH_REJECTED; execution_enabled=false and production_weight=0.

**Affected components**: W5 worker/profile; Navigator typed request and downgrade
contract; Alert Router trigger eligibility; W4 research-outcome linkage; source
adapters and BTC daily research. This patch supplies a callable shadow adapter
and replay, not the full V4 live runtime, scheduler or durable state system.

**Must preserve**: W5 sole final Entry/SL/TP owner; planned net R:R >=3; structural
stops; completed confirmation before ADD and a later retest; 40/60 risk-budget
semantics; current manual Champion; legacy UI meaning; actual history append-only;
N/A distinct from zero; optional context N/A not an automatic veto; both directions.

**Must not change**: No W6 competing execution owner; Navigator may not upgrade;
only Alert Router sends alerts; Wave/Pattern/Fib weights remain zero/context-only;
no W2 or W3 score changes; no public account balance, real size, identifiers,
credentials or private execution details; no schedule activation or legacy shutdown.

**Contract impact**: Add BTC_DAILY profile to BTC_TRADE typed requests. Require
fresh current, completed source facts, known core health and explicit risk veto
result. Three performance KPIs have fixed denominators; fill probability on a
new live setup remains null until calibrated. Historical fill rate is not a
future guarantee. A daily order horizon is separate from structural invalidation
and does not overwrite legacy rolling Time Validity. Optional funding N/A uses
an explicitly labeled planning reserve; it is never stored as observed zero.

**Data migration impact**: None. Preserve all existing records. New research
plans, simulated fills and validation summaries are research artifacts, never
backfilled Official/Journal records. Price data ends 2026-09-18 00:00 UTC; later
September data is unavailable. Allow a 48h maturity window at each split boundary.

**Test impact**: Checked 87 archive checksums, continuous 103,968 price bars,
3,198 monthly funding-rate reconciliations against REST, actual marks for all
tested funding dates, synthetic long/short stop cost and two-stage sequences,
future-prefix invariance, all 204 selected filled-plan ledgers independently,
no overlap, metric denominators, optional N/A handling and Navigator/alert blocks.
These checks do not establish discretionary Champion parity or profitable edge.

**Rollback**: Additive isolated branch; discard it without touching production.
If later merged, revert this additive change before any separate activation.
No private state migration, destructive delete or schedule rollback is needed now.

**Production impact**: None. Do not activate this candidate. A successful
backtest alone would still not authorize main merge, cutover, schedules, actual
trading or legacy shutdown. Current release is rejected on performance evidence.

**Recommended implementation order**:
1. Retain the W5 owner/profile contract and fail-closed shadow interface.
2. Connect the actual legacy manual price-engine adapter and prospective
   structured plan/event capture; preserve human rationale and chosen Trade Frame.
3. Resolve parity of structural zones/targets and trigger clocks before another
   performance claim; keep research and private execution history separate.
4. Evaluate future paper execution and actual costs under a new preregistered
   experiment. Do not retune against the now-opened 2026 result.
5. Review adoption only after all three gates plus adequate samples/expectancy
   pass; production approval remains a final separate action.

WORK↔CANONICAL DRIFT: Requested V4 addition is captured on this development
branch. Main and the live legacy room have not been promoted to this profile.
