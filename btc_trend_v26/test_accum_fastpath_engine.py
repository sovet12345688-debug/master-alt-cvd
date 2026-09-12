from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import accum_fastpath_engine as eng


def rec(state: str) -> dict:
    return {
        "availability": "CURRENT",
        "state": state,
        "feature_timestamp": "2026-09-11T00:00:00Z",
        "lineage": {"completed_candle_only": True},
    }


class AccumFastpathTests(unittest.TestCase):
    def test_neutral_transition_with_positive_etf_scores_strong_shadow(self) -> None:
        payload = {
            "features": {
                "STRUCTURE_1D": rec("NEUTRAL"),
                "STRUCTURE_4H": rec("NEUTRAL"),
                "STRUCTURE_1W": rec("NEUTRAL"),
                "SPOT_ETF_FLOW": rec("LONG"),
            }
        }
        with patch.object(eng, "build_live_features_r22", return_value=payload):
            out = eng.compute_accumulation(datetime(2026, 9, 11, tzinfo=timezone.utc))
        self.assertEqual(out["status"], "OK_SHADOW")
        self.assertEqual(out["coverage_pct"], 53.0)
        self.assertEqual(out["coverage_grade"], "C")
        self.assertEqual(out["accumulation_score"], 75)
        self.assertFalse(out["strong_confirmation_coverage_met"])
        self.assertEqual(out["entry_gate_effect"], 0)

    def test_rapid_rise_proxy_caps_at_64(self) -> None:
        payload = {
            "features": {
                "STRUCTURE_1D": rec("STRONG_LONG"),
                "STRUCTURE_4H": rec("STRONG_LONG"),
                "STRUCTURE_1W": rec("LONG"),
                "SPOT_ETF_FLOW": rec("STRONG_LONG"),
            }
        }
        with patch.object(eng, "build_live_features_r22", return_value=payload):
            out = eng.compute_accumulation(datetime(2026, 9, 11, tzinfo=timezone.utc))
        self.assertTrue(out["rapid_rise_proxy"])
        self.assertTrue(out["cap_64_applied"])
        self.assertEqual(out["accumulation_score"], 64)

    def test_missing_etf_fails_closed_below_50_coverage(self) -> None:
        payload = {
            "features": {
                "STRUCTURE_1D": rec("NEUTRAL"),
                "STRUCTURE_4H": rec("LONG"),
                "STRUCTURE_1W": rec("SHORT"),
                "SPOT_ETF_FLOW": {"availability": "N_A_SOURCE_MISSING"},
            }
        }
        with patch.object(eng, "build_live_features_r22", return_value=payload):
            out = eng.compute_accumulation(datetime(2026, 9, 11, tzinfo=timezone.utc))
        self.assertEqual(out["status"], "ACCUMULATION_UNAVAILABLE")
        self.assertEqual(out["coverage_pct"], 38.0)
        self.assertIsNone(out["accumulation_score"])


if __name__ == "__main__":
    unittest.main()
