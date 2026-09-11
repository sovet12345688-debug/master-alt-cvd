import unittest

from volume_wave_engine_r23 import classify_tf, combine_states


def rows_for(direction: int, current_volume: float = 250.0, current_move: float = 1.0):
    rows = []
    close = 100.0
    for i in range(15):
        o = close
        c = close + (0.4 if i % 2 == 0 else -0.2)
        h = max(o, c) + 1.0
        l = min(o, c) - 1.0
        rows.append({"ts": float(i * 1000), "open": o, "high": h, "low": l, "close": c, "volume": 100.0})
        close = c
    prev = rows[-1]["close"]
    move = abs(current_move) * (1 if direction > 0 else -1)
    o = prev - 0.2 if direction > 0 else prev + 0.2
    c = prev + move
    rows.append({
        "ts": 15000.0,
        "open": o,
        "high": max(o, c) + 1.0,
        "low": min(o, c) - 1.0,
        "close": c,
        "volume": current_volume,
    })
    return rows


class VolumeWaveR23Tests(unittest.TestCase):
    def test_long_from_volume_confirmation(self):
        rec = classify_tf(rows_for(1, current_volume=250.0, current_move=0.5))
        self.assertEqual(rec["state"], "LONG")
        self.assertTrue(rec["volume_confirmed"])
        self.assertTrue(rec["price_body_coherent"])

    def test_short_from_volume_confirmation(self):
        rec = classify_tf(rows_for(-1, current_volume=250.0, current_move=0.5))
        self.assertEqual(rec["state"], "SHORT")
        self.assertTrue(rec["volume_confirmed"])

    def test_wave_energy_can_confirm_without_volume(self):
        rec = classify_tf(rows_for(1, current_volume=50.0, current_move=5.0))
        self.assertFalse(rec["volume_confirmed"])
        self.assertTrue(rec["wave_confirmed"])
        self.assertEqual(rec["state"], "LONG")

    def test_low_volume_low_energy_is_neutral(self):
        rec = classify_tf(rows_for(1, current_volume=50.0, current_move=0.1))
        self.assertFalse(rec["volume_confirmed"])
        self.assertFalse(rec["wave_confirmed"])
        self.assertEqual(rec["state"], "NEUTRAL")

    def test_timeframe_agreement_required(self):
        d1 = {"state": "LONG"}
        h4 = {"state": "SHORT"}
        self.assertEqual(combine_states(d1, h4), "NEUTRAL")
        self.assertEqual(combine_states({"state": "LONG"}, {"state": "LONG"}), "LONG")
        self.assertEqual(combine_states({"state": "SHORT"}, {"state": "SHORT"}), "SHORT")


if __name__ == "__main__":
    unittest.main()
