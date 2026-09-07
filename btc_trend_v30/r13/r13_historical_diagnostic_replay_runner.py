from __future__ import annotations

"""Implementation-only wrapper for the frozen R1.3 historical diagnostic replay.

The state replay already records first_confirm_time / first_route / first_risk_state,
while reconcile_metrics independently derives the canonical first-leg fields from the
1H_ENTRY leg table using the same names. Pandas therefore suffixes duplicate names.
For metric reconciliation the trade-leg table is the canonical source, so this wrapper
removes only the three duplicate state-display fields before the existing reconciliation
function runs. No signal, route, threshold, candle, trade outcome, or performance rule is
changed.
"""

import r13_historical_diagnostic_replay as core

_original_reconcile = core.reconcile_metrics


def _reconcile_without_duplicate_state_display_fields(ep, truth, sig, scored, state, legs):
    state = state.drop(
        columns=["first_confirm_time", "first_route", "first_risk_state"],
        errors="ignore",
    )
    return _original_reconcile(ep, truth, sig, scored, state, legs)


core.reconcile_metrics = _reconcile_without_duplicate_state_display_fields

if __name__ == "__main__":
    core.main()
