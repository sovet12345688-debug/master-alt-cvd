from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from worker_shadow import EXPECTED_SYSTEMS, ROOT, validate_worker  # noqa: E402


class WorkerShadowTests(unittest.TestCase):
    def test_all_six_shadow_candidates_boot(self) -> None:
        for system_id in sorted(EXPECTED_SYSTEMS):
            mode = "candidate" if system_id in {"market", "youtuber_view"} else "main"
            with self.subTest(system_id=system_id):
                self.assertEqual(validate_worker(system_id, mode)["status"], "PASS")

    def test_market_current_main_remains_blocked(self) -> None:
        receipt = validate_worker("market", "main")
        self.assertEqual(receipt["status"], "HOLD_CURRENT_MAIN_SOURCE_CHAT_CONFLICT")
        self.assertFalse(receipt["production_cutover"])

    def test_runtime_materialization_is_system_local(self) -> None:
        with tempfile.TemporaryDirectory(prefix="money-os-shadow-test-") as temp:
            destination = Path(temp) / "trading" / "runtime"
            receipt = validate_worker(
                "trading", "main", smoke_runtime_root=destination
            )
            for store in receipt["stores"]:
                self.assertTrue((destination / store).is_dir())
            saved = json.loads(
                (destination / "shadow_bootstrap_receipt.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(saved["system_id"], "trading")
            self.assertEqual(saved["cross_system_reads"], 0)
            self.assertEqual(saved["cross_system_writes"], 0)

    def test_parity_audit_has_exact_ten_axes_for_six_systems(self) -> None:
        audit_path = ROOT / "money_os_work_v3/audit/SOURCE_CHAT_PARITY_20260914.json"
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        axes = set(audit["required_axes"])
        self.assertEqual(len(axes), 10)
        self.assertEqual(set(audit["systems"]), EXPECTED_SYSTEMS)
        for system in audit["systems"].values():
            self.assertEqual(set(system["axes"]), axes)

    def test_all_activation_and_cutover_flags_are_off(self) -> None:
        for system_id in sorted(EXPECTED_SYSTEMS):
            contract_path = (
                ROOT
                / "money_os_work_v3/systems"
                / system_id
                / "runtime/RUNTIME_CONTRACT.json"
            )
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            shadow = contract["shadow"]
            self.assertFalse(shadow["notifications_enabled"])
            self.assertFalse(shadow["production_writes_enabled"])
            self.assertFalse(shadow["schedule_activation_enabled"])
            self.assertFalse(shadow["cutover_enabled"])

    def test_manifest_source_evidence_matches_audit(self) -> None:
        audit_path = ROOT / "money_os_work_v3/audit/SOURCE_CHAT_PARITY_20260914.json"
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        for system_id in sorted(EXPECTED_SYSTEMS):
            manifest_path = (
                ROOT
                / "money_os_work_v3/systems"
                / system_id
                / "manifest.json"
            )
            worker = json.loads(manifest_path.read_text(encoding="utf-8"))[
                "worker_shadow"
            ]
            system_audit = audit["systems"][system_id]
            self.assertEqual(worker["source_chat"], system_audit["source_chat"])
            self.assertEqual(
                worker["source_chat_sha256"], system_audit["extraction"]["sha256"]
            )


if __name__ == "__main__":
    unittest.main()
