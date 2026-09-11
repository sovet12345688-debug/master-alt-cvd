import unittest

from bottom_fastpath_engine import band, capitulation_state, map_directional_to_evidence


class BottomFastpathTests(unittest.TestCase):
    def test_band_edges(self):
        self.assertEqual(band(39), "낮음")
        self.assertEqual(band(40), "가능성 관찰")
        self.assertEqual(band(60), "유의미")
        self.assertEqual(band(75), "강함")
        self.assertEqual(band(85), "매우 강함")

    def test_structure_mapping(self):
        mapping = {"STRONG_LONG":"VERY_STRONG","LONG":"STRONG","NEUTRAL":"WEAK","SHORT":"NONE","STRONG_SHORT":"NONE"}
        self.assertEqual(map_directional_to_evidence("STRONG_LONG", mapping), "VERY_STRONG")
        self.assertEqual(map_directional_to_evidence("SHORT", mapping), "NONE")

    def test_capitulation_state_all_flags(self):
        bars = []
        price = 100.0
        for i in range(80):
            o = price
            c = price * (0.995 if i < 70 else 0.98)
            h = max(o, c) * 1.002
            l = min(o, c) * (0.94 if i == 75 else 0.998)
            v = 100.0 if i != 76 else 1200.0
            bars.append({"open_time_ms": i*3600000, "close_time_ms": (i+1)*3600000, "open": o, "high": h, "low": l, "close": c, "volume": v})
            price = c
        state, details = capitulation_state(bars)
        self.assertIn(state, {"MODERATE","STRONG","VERY_STRONG"})
        self.assertGreaterEqual(details["flag_count"], 2)


if __name__ == "__main__":
    unittest.main()
