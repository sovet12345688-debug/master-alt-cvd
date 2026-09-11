import unittest

from volume_wave_engine_r24 import classify_tf, combine_primary, combine_strict


def rows_for(direction: int, current_volume: float = 250.0, current_move: float = 1.0, body_scale: float = 1.0):
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
    c = prev + move
    body = abs(move) * body_scale
    o = c - body if direction > 0 else c + body
    rows.append({
        "ts": 15000.0,
        "open": o,
        "high": max(o, c) + 1.0,
        "low": min(o, c) - 1.0,
        "close": c,
        "volume": current_volume,
    })
    return rows


class VolumeWaveR24Tests(unittest.TestCase):
    def test_directional_long(self):
        rec = classify_tf(rows_for(1, current_volume=250.0, current_move=0.7))
        self.assertEqual(rec["evidence_class"], "DIRECTIONAL")
        self.assertEqual(rec["state"], "LONG")

    def test_directional_short(self):
        rec = classify_tf(rows_for(-1, current_volume=250.0, current_move=0.7))
        self.assertEqual(rec["evidence_class"], "DIRECTIONAL")
        self.assertEqual(rec["state"], "SHORT")

    def test_low_volume_low_energy_is_insufficient_not_neutral(self):
        rec = classify_tf(rows_for(1, current_volume=30.0, current_move=0.05, body_scale=0.1))
        self.assertEqual(rec["evidence_class"], "INSUFFICIENT_EVIDENCE")
        self.assertIsNone(rec["state"])

    def test_adequate_participation_low_energy_can_be_true_neutral(self):
        rec = classify_tf(rows_for(1, current_volume=100.0, current_move=0.05, body_scale=0.1))
        self.assertEqual(rec["evidence_class"], "TRUE_NEUTRAL")
        self.assertEqual(rec["state"], "NEUTRAL")

    def test_strict_disagreement_is_not_neutral(self):
        d1 = {"evidence_class": "DIRECTIONAL", "state": "LONG", "directional_quality": 2}
        h4 = {"evidence_class": "DIRECTIONAL", "state": "SHORT", "directional_quality": 2}
        out = combine_strict(d1, h4)
        self.assertEqual(out["decision_status"], "INSUFFICIENT_EVIDENCE")
        self.assertIsNone(out["state"])

    def test_primary_needs_real_4h_confirmation(self):
        d1 = {"evidence_class": "DIRECTIONAL", "state": "LONG", "directional_quality": 3}
        h4 = {"evidence_class": "INSUFFICIENT_EVIDENCE", "state": None, "directional_quality": 0}
        out = combine_primary(d1, h4)
        self.assertEqual(out["decision_status"], "INSUFFICIENT_EVIDENCE")

    def test_primary_allows_high_quality_1d_with_true_neutral_4h(self):
        d1 = {"evidence_class": "DIRECTIONAL", "state": "LONG", "directional_quality": 2}
        h4 = {"evidence_class": "TRUE_NEUTRAL", "state": "NEUTRAL", "directional_quality": 0}
        out = combine_primary(d1, h4)
        self.assertEqual(out["decision_status"], "CURRENT")
        self.assertEqual(out["state"], "LONG")

    def test_primary_rejects_weak_1d_with_true_neutral_4h(self):
        d1 = {"evidence_class": "DIRECTIONAL", "state": "LONG", "directional_quality": 1}
        h4 = {"evidence_class": "TRUE_NEUTRAL", "state": "NEUTRAL", "directional_quality": 0}
        out = combine_primary(d1, h4)
        self.assertEqual(out["decision_status"], "INSUFFICIENT_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
