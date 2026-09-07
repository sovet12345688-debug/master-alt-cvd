from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from r13_engine import R13Engine, _sha256, CONFIG_PATH


class R13FrozenContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e = R13Engine()

    def open_daily(self, direction="long"):
        return {
            "TRANSITION_CONFLICT": False,
            "severe_risk_veto": False,
            "FALSE_BOTTOM_RISK": False,
            "FALSE_TOP_RISK": False,
            "long_overextended": False,
            "short_overextended": False,
            "up_transition": direction == "long",
            "down_transition": direction == "short",
        }

    def long_ignition_candidate(self):
        return {
            "open": 96.0, "high": 102.0, "low": 95.5, "close": 101.0,
            "ema20": 98.0, "vol_ratio20": 1.20, "taker_imb": 0.10,
            "prior_high3": 99.0, "prior_low3": 94.0, "atr14_1h": 2.0,
        }

    def long_ignition_next(self, close=102.0):
        return {"open": 101.0, "high": max(103.0, close), "low": 99.0, "close": close}

    def short_ignition_candidate(self):
        return {
            "open": 104.0, "high": 104.5, "low": 98.0, "close": 99.0,
            "ema20": 101.0, "vol_ratio20": 1.10, "taker_imb": -0.10,
            "prior_high3": 106.0, "prior_low3": 100.0, "atr14_1h": 2.0,
        }

    def short_ignition_next(self):
        return {"open": 99.0, "high": 100.2, "low": 98.0, "close": 98.5}

    def long_retest_candidate(self):
        return {
            "open": 91.0, "high": 96.0, "low": 89.0, "close": 95.5,
            "ema20": 93.0, "vol_ratio20": 1.20, "taker_imb": 0.10,
            "prior_high3": 94.0, "prior_low3": 88.0, "atr14_1h": 2.0,
        }

    def long_retest_next(self):
        return {"open": 95.5, "high": 97.0, "low": 90.0, "close": 96.0}

    def test_00_frozen_hash_exact(self):
        self.assertEqual(_sha256(CONFIG_PATH), "b417282c66bec49c1c5de672f8317635508eba1dd05f538074a1508f14217fa9")

    def test_01_frozen_thresholds_exact(self):
        cfg = self.e.cfg
        self.assertEqual(cfg["entry_routes"]["RETEST"]["reaction_score_min"], 65)
        self.assertEqual(cfg["entry_routes"]["RETEST"]["independent_reasons_min"], 2)
        self.assertEqual(cfg["entry_routes"]["RETEST"]["nonchase_daily_atr_max"], 1.5)
        self.assertEqual(cfg["entry_routes"]["IGNITION"]["ignition_quality_min"], 70)
        self.assertEqual(cfg["entry_routes"]["IGNITION"]["independent_reasons_min"], 3)
        self.assertEqual(cfg["entry_routes"]["IGNITION"]["nonchase_daily_atr_max"], 0.75)
        self.assertEqual(cfg["entry_routes"]["IGNITION"]["breakout_persistence_buffer_h1_atr"], 0.25)
        self.assertEqual(cfg["entry_routes"]["RETEST"]["fixed_rr"], 3.0)
        self.assertEqual(cfg["entry_routes"]["IGNITION"]["fixed_rr"], 3.0)
        self.assertEqual(cfg["entry_routes"]["RETEST"]["max_stop_distance_pct"], 0.15)
        self.assertEqual(cfg["entry_routes"]["IGNITION"]["max_stop_distance_pct"], 0.15)

    def test_02_stage_policy(self):
        self.assertTrue(self.e.execution_eligible("LR", 1))
        self.assertTrue(self.e.execution_eligible("SR", 1))
        self.assertFalse(self.e.execution_eligible("LC", 1))
        self.assertFalse(self.e.execution_eligible("SC", 1))
        self.assertTrue(self.e.execution_eligible("LC", 2))
        self.assertTrue(self.e.execution_eligible("SC", 2))

    def test_03_risk_states(self):
        d = self.open_daily()
        self.assertEqual(self.e.risk_state(d), "OPEN")
        d["long_overextended"] = True
        self.assertEqual(self.e.risk_state(d), "CAUTION")
        d["TRANSITION_CONFLICT"] = True
        self.assertEqual(self.e.risk_state(d), "BLOCK")
        d = self.open_daily(); d["severe_risk_veto"] = True
        self.assertEqual(self.e.risk_state(d), "BLOCK")

    def test_04_route_matrix(self):
        self.assertTrue(self.e.route_allowed("RETEST", "OPEN"))
        self.assertTrue(self.e.route_allowed("RETEST", "CAUTION"))
        self.assertFalse(self.e.route_allowed("RETEST", "BLOCK"))
        self.assertTrue(self.e.route_allowed("IGNITION", "OPEN"))
        self.assertFalse(self.e.route_allowed("IGNITION", "CAUTION"))
        self.assertFalse(self.e.route_allowed("IGNITION", "BLOCK"))

    def test_05_long_ignition_pass(self):
        r = self.e.evaluate_pair(
            engine="LR", stage=2, direction="long", daily=self.open_daily("long"),
            candidate=self.long_ignition_candidate(), nxt=self.long_ignition_next(),
            zone_lo=90.0, zone_hi=95.0, daily_atr=10.0,
        )
        self.assertEqual((r.action, r.route, r.reason), ("ENTER", "IGNITION", "IGNITION_PASS"))
        self.assertEqual(r.quality_score, 100.0)
        self.assertEqual(r.independent_reasons, 6)
        self.assertAlmostEqual(r.entry, 102.0)
        self.assertAlmostEqual(r.stop, 93.5)
        self.assertAlmostEqual(r.risk, 8.5)
        self.assertAlmostEqual(r.target, 127.5)

    def test_06_short_ignition_pass(self):
        r = self.e.evaluate_pair(
            engine="SR", stage=2, direction="short", daily=self.open_daily("short"),
            candidate=self.short_ignition_candidate(), nxt=self.short_ignition_next(),
            zone_lo=105.0, zone_hi=110.0, daily_atr=10.0,
        )
        self.assertEqual((r.action, r.route), ("ENTER", "IGNITION"))
        self.assertEqual(r.quality_score, 100.0)
        self.assertAlmostEqual(r.entry, 98.5)
        self.assertAlmostEqual(r.stop, 106.5)
        self.assertAlmostEqual(r.target, 74.5)

    def test_07_ignition_nonchase_boundary(self):
        self.assertTrue(self.e.nonchase(102.5, "long", 90.0, 95.0, 10.0, "IGNITION"))
        self.assertFalse(self.e.nonchase(102.500001, "long", 90.0, 95.0, 10.0, "IGNITION"))
        self.assertTrue(self.e.nonchase(97.5, "short", 105.0, 110.0, 10.0, "IGNITION"))
        self.assertFalse(self.e.nonchase(97.499999, "short", 105.0, 110.0, 10.0, "IGNITION"))

    def test_08_retest_nonchase_boundary(self):
        self.assertTrue(self.e.nonchase(110.0, "long", 90.0, 95.0, 10.0, "RETEST"))
        self.assertFalse(self.e.nonchase(110.000001, "long", 90.0, 95.0, 10.0, "RETEST"))

    def test_09_retest_pass_open(self):
        r = self.e.evaluate_pair(
            engine="LR", stage=1, direction="long", daily=self.open_daily("long"),
            candidate=self.long_retest_candidate(), nxt=self.long_retest_next(),
            zone_lo=90.0, zone_hi=95.0, daily_atr=10.0,
        )
        self.assertEqual((r.action, r.route), ("ENTER", "RETEST"))
        self.assertEqual(r.quality_score, 100.0)
        self.assertAlmostEqual(r.entry, 96.0)
        self.assertAlmostEqual(r.stop, 86.5)
        self.assertAlmostEqual(r.target, 124.5)

    def test_10_retest_pass_caution_but_ignition_forbidden(self):
        d = self.open_daily("long"); d["long_overextended"] = True
        r = self.e.evaluate_pair(
            engine="LR", stage=1, direction="long", daily=d,
            candidate=self.long_retest_candidate(), nxt=self.long_retest_next(),
            zone_lo=90.0, zone_hi=95.0, daily_atr=10.0,
        )
        self.assertEqual((r.action, r.route, r.risk_state), ("ENTER", "RETEST", "CAUTION"))
        r2 = self.e.evaluate_pair(
            engine="LR", stage=1, direction="long", daily=d,
            candidate=self.long_ignition_candidate(), nxt=self.long_ignition_next(),
            zone_lo=90.0, zone_hi=95.0, daily_atr=10.0,
        )
        self.assertEqual((r2.action, r2.route, r2.reason), ("WAIT", None, "NO_ALLOWED_ROUTE"))

    def test_11_block_forbids_all_routes(self):
        d = self.open_daily("long"); d["TRANSITION_CONFLICT"] = True
        r = self.e.evaluate_pair(
            engine="LR", stage=2, direction="long", daily=d,
            candidate=self.long_retest_candidate(), nxt=self.long_retest_next(),
            zone_lo=90.0, zone_hi=95.0, daily_atr=10.0,
        )
        self.assertEqual((r.action, r.risk_state, r.reason), ("WAIT", "BLOCK", "RISK_STATE_BLOCK"))

    def test_12_continuation_stage1_watch_only(self):
        r = self.e.evaluate_pair(
            engine="LC", stage=1, direction="long", daily=self.open_daily("long"),
            candidate=self.long_ignition_candidate(), nxt=self.long_ignition_next(),
            zone_lo=90.0, zone_hi=95.0, daily_atr=10.0,
        )
        self.assertEqual((r.action, r.reason), ("WATCH", "ENGINE_STAGE_WATCH_ONLY"))

    def test_13_low_ignition_quality_fails(self):
        c = self.long_ignition_candidate()
        c.update({"close": 98.0, "high": 99.0, "open": 98.5, "ema20": 99.0, "vol_ratio20": 0.8, "taker_imb": -0.1})
        r = self.e.evaluate_pair(
            engine="LR", stage=2, direction="long", daily=self.open_daily("long"),
            candidate=c, nxt=self.long_ignition_next(99.0), zone_lo=90.0, zone_hi=95.0, daily_atr=10.0,
        )
        self.assertEqual((r.action, r.route, r.reason), ("WAIT", "IGNITION", "IGNITION_FAILED"))
        self.assertLess(r.quality_score, 70.0)

    def test_14_same_bar_tp_sl_is_ambiguous(self):
        outcome, rr = self.e.resolve_fixed_rr_leg(
            [{"high": 131.0, "low": 90.0}], "long", stop=93.5, target=127.5
        )
        self.assertEqual(outcome, "AMBIGUOUS_SAME_BAR")
        self.assertIsNone(rr)

    def test_15_state_machine_no_auto_4h_no_third_1d_leg(self):
        sm = self.e.state_machine_contract()
        self.assertFalse(sm["four_hour_add"]["automatic_add"])
        self.assertTrue(sm["four_hour_add"]["independent_confirmation_required"])
        self.assertFalse(sm["four_hour_add"]["third_allocation_leg_on_1d"])
        self.assertEqual(sm["one_day_allocation"], "LOG_ONLY_NO_THIRD_LEG")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(R13FrozenContractTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps({
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "contract_test": "PASS" if result.wasSuccessful() else "FAIL",
        "oos_replay_performed": False,
    }, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.wasSuccessful() else 1)
