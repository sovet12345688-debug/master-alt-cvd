#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maintenance.sync_master_market_active_score_contract_20260911 import (
    APPROVED_AT,
    CANONICAL,
    CONTRACT,
    ENGINE_ID,
    MARKER,
    OFFICIAL_NEW,
    SCORE_CONTRACT,
    SCORE_OUTPUT,
    SCORE_OUTPUT_REL,
    WATCH_NEW,
    load_json,
    patch_canonical,
    patch_contract,
)


def contract_needs_patch(doc: dict) -> bool:
    active = doc.get("active_score_engine") or {}
    required_output = doc.get("required_output") or {}
    return any([
        doc.get("schema_version") != "1.12",
        active.get("enabled") is not True,
        active.get("approved_at_kst") != APPROVED_AT,
        active.get("engine_id") != ENGINE_ID,
        active.get("current_output") != SCORE_OUTPUT_REL,
        active.get("structural_formula_na_resolved") is not True,
        "| 롱/숏 |" not in required_output.get("footer", ""),
        "| 롱/숏 |" not in required_output.get("watch_footer", ""),
    ])


def apply() -> None:
    canonical = CANONICAL.read_text(encoding="utf-8")
    patched_canonical = patch_canonical(canonical)
    if patched_canonical != canonical:
        CANONICAL.write_text(patched_canonical, encoding="utf-8")

    contract = load_json(CONTRACT)
    if contract_needs_patch(contract):
        original_keys = set(contract)
        patched = patch_contract(contract)
        if not original_keys.issubset(patched):
            raise SystemExit("top-level contract key regression detected")
        CONTRACT.write_text(json.dumps(patched, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate() -> None:
    canonical = CANONICAL.read_text(encoding="utf-8")
    contract = load_json(CONTRACT)
    score_contract = load_json(SCORE_CONTRACT)
    score_output = load_json(SCORE_OUTPUT)

    assert canonical.count(MARKER) == 1
    assert OFFICIAL_NEW in canonical and WATCH_NEW in canonical
    assert "BLOCKED_MISSING_AUTHORITATIVE_FORMULA` is resolved" in canonical

    assert contract["schema_version"] == "1.12"
    active = contract["active_score_engine"]
    assert active["enabled"] is True and active["authorized_by_user"] is True
    assert active["approved_at_kst"] == APPROVED_AT
    assert active["engine_id"] == ENGINE_ID
    assert active["current_output"] == SCORE_OUTPUT_REL
    assert active["structural_formula_na_resolved"] is True
    assert contract["schedule"]["score_refresh_minute"] == 58
    assert "| 롱/숏 |" in contract["required_output"]["footer"]
    assert "| 롱/숏 |" in contract["required_output"]["watch_footer"]

    assert score_contract["status"] == "ACTIVE"
    assert score_contract["engine_id"] == ENGINE_ID
    assert score_output["engine"] == ENGINE_ID
    assert score_output["status"] in ["ACTIVE_OK", "PARTIAL"]
    assert score_output["official_persistence_eligible"] is True
    for key in active["required_current_scores"]:
        score = score_output["scores"][key]["score"]
        assert score is not None and 0 <= score <= 100, (key, score)

    print("MASTER_MARKET_CANONICAL_SCORE_SYNC_V2=PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not args.apply and not args.check:
        parser.error("choose --apply and/or --check")
    if args.apply:
        apply()
    if args.check:
        validate()
