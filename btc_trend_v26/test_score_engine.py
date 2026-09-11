from __future__ import annotations

import unittest

from score_engine import ScoreValidationError, compute_trend_score


WEIGHTS = {
    "STRUCTURE_1D": 22,
    "STRUCTURE_4H": 10,
    "STRUCTURE_1W": 8,
    "VOLUME_WAVE": 15,
    "EMA_MA_STACK": 10,
    "SPOT_ETF_FLOW": 10,
    "DERIVATIVES_STATE": 8,
    "VALUE_OVEREXTENSION": 7,
    "LIQUIDITY_MACRO": 10,
}


def record(state: str, availability: str = "CURRENT"):
    if availability != "CURRENT":
        return {"availability": availability}
    return {
        "availability": "CURRENT",
        "state": state,
        "feature_timestamp": "2026-09-11T00:00:00Z",
        "source_timestamps": ["2026-09-11T00:00:00Z"],
        "lineage": {"test": True},
    }


def payload(default_state: str = "NEUTRAL"):
    return {
        "asof_utc": "2026-09-11T00:00:00Z",
        "features": {fid: record(default_state) for fid in WEIGHTS},
    }


class TrendScoreEngineTests(unittest.TestCase):
    def test_all_strong_long(self):
        r = compute_trend_score(payload("STRONG_LONG"))
        self.assertEqual(r["status"], "OK")
        self.assertEqual((r["long"], r["short"], r["trend_strength"]), (100, 0, 100))
        self.assertEqual(r["dominant_state"], "LONG")
        self.assertEqual(r["coverage_pct"], 100.0)

    def test_all_strong_short(self):
        r = compute_trend_score(payload("STRONG_SHORT"))
        self.assertEqual((r["long"], r["short"], r["trend_strength"]), (0, 100, 100))
        self.assertEqual(r["dominant_state"], "SHORT")

    def test_all_neutral(self):
        r = compute_trend_score(payload("NEUTRAL"))
        self.assertEqual((r["long"], r["short"], r["trend_strength"]), (50, 50, 0))
        self.assertEqual(r["dominant_state"], "NEUTRAL")

    def test_one_axis_directional_math(self):
        p = payload("NEUTRAL")
        p["features"]["STRUCTURE_1D"] = record("STRONG_LONG")
        r = compute_trend_score(p)
        self.assertEqual(r["directional_score"], 0.22)
        self.assertEqual((r["long"], r["short"]), (61, 39))
        self.assertEqual(r["trend_strength"], 22)
        self.assertEqual(r["dominant_state"], "LONG")

    def test_missing_axis_is_excluded_and_renormalized(self):
        p = payload("STRONG_LONG")
        p["features"]["DERIVATIVES_STATE"] = record("NEUTRAL", "N_A_INSUFFICIENT_HISTORY")
        r = compute_trend_score(p)
        self.assertEqual(r["coverage_pct"], 92.0)
        self.assertEqual(r["valid_weight"], 92)
        self.assertEqual((r["long"], r["short"]), (100, 0))
        self.assertFalse(any(c["feature_id"] == "DERIVATIVES_STATE" for c in r["components"]))

    def test_zero_valid_weight_is_unavailable_not_zero_score(self):
        p = payload("NEUTRAL")
        p["features"] = {fid: record("NEUTRAL", "N_A_SOURCE_MISSING") for fid in WEIGHTS}
        r = compute_trend_score(p)
        self.assertEqual(r["status"], "SCORE_UNAVAILABLE")
        self.assertIsNone(r["long"])
        self.assertIsNone(r["short"])
        self.assertIsNone(r["trend_strength"])

    def test_invalid_current_state_fails_closed(self):
        p = payload("NEUTRAL")
        p["features"]["STRUCTURE_1D"] = record("BULLISHISH")
        with self.assertRaises(ScoreValidationError):
            compute_trend_score(p)

    def test_current_record_requires_lineage(self):
        p = payload("NEUTRAL")
        del p["features"]["STRUCTURE_1D"]["lineage"]
        with self.assertRaises(ScoreValidationError):
            compute_trend_score(p)

    def test_ratio_always_sums_to_100(self):
        p = payload("NEUTRAL")
        p["features"]["STRUCTURE_1D"] = record("LONG")
        p["features"]["VOLUME_WAVE"] = record("SHORT")
        p["features"]["EMA_MA_STACK"] = record("STRONG_LONG")
        r = compute_trend_score(p)
        self.assertEqual(r["long"] + r["short"], 100)
        self.assertEqual(r["trend_strength"], abs(r["long"] - r["short"]))
        self.assertFalse(r["production_eligible"])


if __name__ == "__main__":
    unittest.main()
