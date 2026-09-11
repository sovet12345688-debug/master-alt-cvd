from __future__ import annotations

import unittest

from structure_engine_r22 import confirmed_pivots, classify_structure


def make_bars(highs, lows, closes=None):
    closes = closes or [(h + l) / 2 for h, l in zip(highs, lows)]
    out = []
    for i, (h, l, c) in enumerate(zip(highs, lows, closes)):
        o = min(max((h + l) / 2, l), h)
        c = min(max(c, l), h)
        out.append({
            "open_time_ms": i * 1000,
            "close_time_ms": (i + 1) * 1000,
            "open": o,
            "high": float(h),
            "low": float(l),
            "close": float(c),
        })
    return out


class StructureR22Tests(unittest.TestCase):
    def test_confirmed_pivot_requires_right_bars(self):
        highs = [10, 11, 12, 11, 10, 14, 12, 11]
        lows = [8, 8.5, 9, 8.7, 8.2, 10, 9.5, 9]
        bars_before_confirmation = make_bars(highs[:7], lows[:7])
        ph, _ = confirmed_pivots(bars_before_confirmation, left=2, right=2)
        self.assertFalse(any(x["index"] == 5 for x in ph))
        bars_after_confirmation = make_bars(highs, lows)
        ph, _ = confirmed_pivots(bars_after_confirmation, left=2, right=2)
        self.assertTrue(any(x["index"] == 5 for x in ph))

    def test_strong_long_hh_hl_bos(self):
        highs = [10, 12, 11, 13, 12, 14, 13, 15, 14, 16]
        lows = [8, 9, 8.5, 10, 9.5, 11, 10.5, 12, 11, 12]
        closes = [9, 11, 10, 12, 11, 13, 12, 14, 13, 15.5]
        r = classify_structure(make_bars(highs, lows, closes), left=1, right=1)
        self.assertEqual(r["state"], "STRONG_LONG")
        self.assertTrue(r["facts"]["HH"])
        self.assertTrue(r["facts"]["HL"])
        self.assertTrue(r["facts"]["BOS_UP"])

    def test_strong_short_lh_ll_bos(self):
        highs = [18, 17, 17.5, 16, 16.5, 15, 15.5, 14, 14.5, 13.5]
        lows = [16, 15, 15.5, 14, 14.5, 13, 13.5, 12, 12.5, 11]
        closes = [17, 16, 16.5, 15, 15.5, 14, 14.5, 13, 13.5, 11.5]
        r = classify_structure(make_bars(highs, lows, closes), left=1, right=1)
        self.assertEqual(r["state"], "STRONG_SHORT")
        self.assertTrue(r["facts"]["LH"])
        self.assertTrue(r["facts"]["LL"])
        self.assertTrue(r["facts"]["BOS_DOWN"])

    def test_break_priority_can_flip_old_bull_sequence(self):
        highs = [10, 12, 11, 13, 12, 14, 13, 15, 14, 14.5]
        lows = [8, 9, 8.5, 10, 9.5, 11, 10.5, 12, 11, 10]
        closes = [9, 11, 10, 12, 11, 13, 12, 14, 13, 10.2]
        r = classify_structure(make_bars(highs, lows, closes), left=1, right=1)
        self.assertEqual(r["state"], "SHORT")
        self.assertTrue(r["facts"]["BOS_DOWN"])

    def test_mixed_lh_hl_compression_is_neutral(self):
        highs = [13, 15, 14, 14.5, 13.5, 14, 13, 13.5, 13, 13.2]
        lows = [10, 11, 9, 10, 9.5, 10.5, 10, 11, 10.5, 11]
        closes = [11.5, 13, 11.5, 12.5, 11.5, 12.5, 11.5, 12.5, 11.5, 12.5]
        r = classify_structure(make_bars(highs, lows, closes), left=1, right=1)
        self.assertEqual(r["state"], "NEUTRAL")
        self.assertTrue(r["facts"]["LH"])
        self.assertTrue(r["facts"]["HL"])
        self.assertFalse(r["facts"]["BOS_UP"])
        self.assertFalse(r["facts"]["BOS_DOWN"])


if __name__ == "__main__":
    unittest.main()
