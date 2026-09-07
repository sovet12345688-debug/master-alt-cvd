from __future__ import annotations

import pandas as pd
import validation_protocol_v1_step6_state_machine_replay as s6

EXPECTED_FIRST_TRADE_HOUR = pd.Timestamp("2017-08-17T04:00:00Z")

_original_audit = s6.audit_1h


def exchange_start_aware_audit(x, checks, failures):
    """Do not fabricate 00:00-03:00 UTC bars before BTCUSDT's first archived Spot 1H trade bar."""
    d = _original_audit(x, checks, failures)
    start_exact = bool(len(x) and x.time.iloc[0] == EXPECTED_FIRST_TRADE_HOUR)
    end_exact = bool(len(x) and x.time.iloc[-1] == s6.END_EXCL - pd.Timedelta(hours=1))
    d["expected_first_trade_hour"] = EXPECTED_FIRST_TRADE_HOUR.isoformat()
    d["exchange_start_exact"] = start_exact
    d["end_cutoff_exact"] = end_exact
    d["pre_listing_hours_not_fabricated"] = True
    d["exact_edges"] = bool(start_exact and end_exact)
    d["integrity_pass"] = bool(
        not failures
        and not d["checksum_mismatches"]
        and x.time.is_monotonic_increasing
        and not x.time.duplicated().any()
        and d["bad_row_logic"] == 0
        and start_exact
        and end_exact
    )
    d["gap_policy"] = "2017-08-17 00:00-03:00 UTC pre-listing hours and later exchange archive gaps are recorded, never forward-filled or fabricated."
    return d


s6.audit_1h = exchange_start_aware_audit

if __name__ == "__main__":
    s6.main()
