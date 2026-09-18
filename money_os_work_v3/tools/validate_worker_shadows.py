#!/usr/bin/env python3
"""Validate all six isolated Worker Shadow packages and the intentional hold."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from worker_shadow import EXPECTED_SYSTEMS, ROOT, ShadowValidationError, validate_worker


AUDIT = ROOT / "money_os_work_v3/audit/SOURCE_CHAT_PARITY_20260914.json"
SURFACES = ROOT / "money_os_work_v3/work/WORK_SURFACE_SPECS.json"


def main() -> int:
    errors: list[str] = []
    results: dict[str, str] = {}
    try:
        audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"MONEY OS WORKER SHADOWS = FAIL\n- cannot load parity audit: {exc}")
        return 1

    try:
        surfaces = json.loads(SURFACES.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"cannot load Work surface specs: {exc}")
        surfaces = {}

    required_axes = set(audit.get("required_axes", []))
    if len(required_axes) != 10:
        errors.append("parity audit must declare exactly ten axes")
    if set(audit.get("systems", {})) != EXPECTED_SYSTEMS:
        errors.append("parity audit system set mismatch")

    with tempfile.TemporaryDirectory(prefix="money-os-work-v3-shadow-") as temp:
        temp_root = Path(temp)
        for system_id in sorted(EXPECTED_SYSTEMS):
            canonical_mode = (
                "candidate" if system_id in {"market", "youtuber_view"} else "main"
            )
            try:
                receipt = validate_worker(
                    system_id,
                    canonical_mode=canonical_mode,
                    smoke_runtime_root=temp_root / system_id / "runtime",
                )
                results[system_id] = receipt["status"]
                if receipt["status"] != "PASS":
                    errors.append(
                        f"{system_id}: Shadow candidate expected PASS, got {receipt['status']}"
                    )
                actual_stores = {
                    str(path.relative_to(temp_root / system_id / "runtime"))
                    for path in (temp_root / system_id / "runtime").rglob("*")
                    if path.is_dir()
                }
                expected_stores = set(receipt["stores"])
                if not expected_stores.issubset(actual_stores):
                    errors.append(f"{system_id}: smoke runtime stores missing")
            except ShadowValidationError as exc:
                errors.append(f"{system_id}: {exc}")

            system_audit = audit.get("systems", {}).get(system_id, {})
            axes = system_audit.get("axes", {})
            if set(axes) != required_axes:
                errors.append(f"{system_id}: ten-axis parity set mismatch")
            if any(str(value).startswith("FAIL") for value in axes.values()):
                errors.append(f"{system_id}: parity audit contains FAIL")
            manifest_path = (
                ROOT / "money_os_work_v3/systems" / system_id / "manifest.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            worker = manifest.get("worker_shadow", {})
            if worker.get("source_chat") != system_audit.get("source_chat"):
                errors.append(f"{system_id}: source-chat URL differs from parity audit")
            if worker.get("source_chat_sha256") != system_audit.get(
                "extraction", {}
            ).get("sha256"):
                errors.append(f"{system_id}: source-chat digest differs from parity audit")

    try:
        market_main = validate_worker("market", canonical_mode="main")
        if market_main["status"] != "HOLD_CURRENT_MAIN_SOURCE_CHAT_CONFLICT":
            errors.append("market current-main conflict must remain a hard hold")
    except ShadowValidationError as exc:
        errors.append(f"market main hold validation failed: {exc}")

    overall = audit.get("overall", {})
    if overall.get("production_merge_performed") is not False:
        errors.append("audit must keep production merge false")
    if overall.get("production_cutover_performed") is not False:
        errors.append("audit must keep production cutover false")

    surface_items = surfaces.get("surfaces", [])
    if len(surface_items) != 7:
        errors.append("surface spec must contain one controller plus six workers")
    surface_systems = {
        item.get("system_id") for item in surface_items if item.get("system_id")
    }
    if surface_systems != EXPECTED_SYSTEMS:
        errors.append("surface spec worker set mismatch")
    if surfaces.get("production_merge") is not False:
        errors.append("surface spec must keep Production merge false")
    if surfaces.get("production_cutover") is not False:
        errors.append("surface spec must keep Production cutover false")
    if surfaces.get("notifications_enabled") is not False:
        errors.append("surface spec must keep notifications false")
    if any(
        not str(item.get("activation", "")).startswith(
            ("INACTIVE_", "BLOCKED_")
        )
        for item in surface_items
    ):
        errors.append("every Work surface must remain inactive or blocked")

    if errors:
        print("MONEY OS WORKER SHADOWS = FAIL")
        for error in errors:
            print("-", error)
        return 1

    print("MONEY OS WORKER SHADOWS = PASS")
    print(json.dumps(results, ensure_ascii=False, sort_keys=True))
    print("MARKET current main = HOLD_CURRENT_MAIN_SOURCE_CHAT_CONFLICT")
    print("Production merge/cutover = NOT PERFORMED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
