import unittest

from top_fastpath_engine import band, map_directional_to_evidence


class TopFastpathTests(unittest.TestCase):
    def test_structure_mapping(self):
        m = {
            "STRONG_SHORT": "VERY_STRONG",
            "SHORT": "STRONG",
            "NEUTRAL": "WEAK",
            "LONG": "NONE",
            "STRONG_LONG": "NONE",
        }
        self.assertEqual(map_directional_to_evidence("STRONG_SHORT", m), "VERY_STRONG")
        self.assertEqual(map_directional_to_evidence("SHORT", m), "STRONG")
        self.assertEqual(map_directional_to_evidence("NEUTRAL", m), "WEAK")
        self.assertEqual(map_directional_to_evidence("LONG", m), "NONE")

    def test_band_edges(self):
        self.assertEqual(band(0), "낮음")
        self.assertEqual(band(39), "낮음")
        self.assertEqual(band(40), "가능성 관찰")
        self.assertEqual(band(59), "가능성 관찰")
        self.assertEqual(band(60), "유의미")
        self.assertEqual(band(75), "강함")
        self.assertEqual(band(85), "매우 강함")


if __name__ == "__main__":
    unittest.main()
