from __future__ import annotations

import unittest

from feature_engine import classify_derivatives, classify_ema_stack, classify_etf


class FeatureEngineTests(unittest.TestCase):
    def test_ema_strong_long(self):
        d1 = {"full_bull": True, "full_bear": False, "bull_count": 3, "bear_count": 0}
        h4 = {"full_bull": True, "full_bear": False, "bull_count": 3, "bear_count": 0}
        self.assertEqual(classify_ema_stack(d1, h4), "STRONG_LONG")

    def test_ema_strong_short(self):
        d1 = {"full_bull": False, "full_bear": True, "bull_count": 0, "bear_count": 3}
        h4 = {"full_bull": False, "full_bear": True, "bull_count": 0, "bear_count": 3}
        self.assertEqual(classify_ema_stack(d1, h4), "STRONG_SHORT")

    def test_ema_majority_long(self):
        d1 = {"full_bull": False, "full_bear": False, "bull_count": 2, "bear_count": 1}
        h4 = {"full_bull": False, "full_bear": False, "bull_count": 2, "bear_count": 1}
        self.assertEqual(classify_ema_stack(d1, h4), "LONG")

    def test_ema_mixed_neutral(self):
        d1 = {"full_bull": False, "full_bear": False, "bull_count": 2, "bear_count": 1}
        h4 = {"full_bull": False, "full_bear": False, "bull_count": 1, "bear_count": 2}
        self.assertEqual(classify_ema_stack(d1, h4), "NEUTRAL")

    def test_etf_consensus(self):
        self.assertEqual(classify_etf({"flow_1d_usd_m": 1, "flow_3d_usd_m": 2, "flow_7d_usd_m": 3}), "LONG")
        self.assertEqual(classify_etf({"flow_1d_usd_m": -1, "flow_3d_usd_m": -2, "flow_7d_usd_m": -3}), "SHORT")
        self.assertEqual(classify_etf({"flow_1d_usd_m": -1, "flow_3d_usd_m": 2, "flow_7d_usd_m": 3}), "NEUTRAL")
        self.assertEqual(classify_etf({"flow_1d_usd_m": 0, "flow_3d_usd_m": 2, "flow_7d_usd_m": 3}), "NEUTRAL")

    def test_derivatives_base_and_conflict_guard(self):
        self.assertEqual(classify_derivatives("PRICE_DOWN_OI_UP_BEARISH_BUILD", -10, 0.4, 0.6), "SHORT")
        self.assertEqual(classify_derivatives("DELEVERAGING", -10, 0.4, 0.6), "SHORT")
        self.assertEqual(classify_derivatives("DELEVERAGING", 10, 0.6, 0.4), "NEUTRAL")
        self.assertEqual(classify_derivatives("PRICE_UP_OI_UP", 10, 0.6, 0.4), "LONG")
        self.assertEqual(classify_derivatives("PRICE_UP_OI_UP", -10, 0.4, 0.6), "NEUTRAL")
        self.assertEqual(classify_derivatives("MIXED", -10, 0.4, 0.6), "NEUTRAL")

    def test_funding_suffix_does_not_change_base(self):
        self.assertEqual(classify_derivatives("PRICE_DOWN_OI_UP_BEARISH_BUILD|FUNDING_EXTREME", -1, 0.4, 0.6), "SHORT")


if __name__ == "__main__":
    unittest.main()
