#!/usr/bin/env python3
"""Validate MASTER MARKET score-engine provenance and activation safety.

This validator intentionally does not calculate scores. It prevents the four
locked scores from being silently activated with invented, back-solved, equal,
or last-known fallback weights.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "market_scoring/score_engine_contract.json"
CONTRACT = ROOT / "state/master_market_v1_2_contract.json"
CANONICAL = ROOT / "master_prompts/master_market_v1_2_current.md"
HISTORY = ROOT / "state/master_market_official_history.csv"
LOCKED = [
    "market_positive",
    "liquidity_lead",
    "crypto_money_inflow",
    "alt_money_inflow",
]
HISTORY_FIELDS = [
    "run_id",
    "executed_kst",
    "market_positive",
    "liquidity_lead",
    "crypto_money_inflow",
    "alt_money_inflow",
    "coverage",
    "confidence",
    "direction",
    "risk_veto",
]


def load(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must be a JSON object")
    return obj


def validate() -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    if not CANONICAL.exists():
        errors.append("canonical prompt missing")
    if not CONTRACT.exists():
        errors.append("machine contract missing")
    if not SPEC.exists():
        errors.append("score engine contract missing")
        return errors, {}

    spec = load(SPEC)
    contract = load(CONTRACT) if CONTRACT.exists() else {}

    if spec.get("locked_scores") != LOCKED:
        errors.append("locked_scores mismatch")
    thresholds = ((contract.get("required_scores") or {}).get("BTC_Liquidity_Lead_Index") or {}).get("fixed_thresholds")
    if thresholds != [55, 65, 75]:
        errors.append("canonical contract liquidity thresholds are not 55/65/75")
    if (spec.get("locked_thresholds") or {}).get("liquidity_lead") != [55, 65, 75]:
        errors.append("score engine threshold lock mismatch")

    forbidden = set(spec.get("forbidden_fallbacks") or [])
    for phrase in (
        "copy last known score into current score",
        "treat N/A as zero",
        "regress or back-solve weights from historical score rows",
        "invent equal weights",
    ):
        if phrase not in forbidden:
            errors.append("missing forbidden fallback: " + phrase)

    if not HISTORY.exists():
        errors.append("official score history missing")
    else:
        with HISTORY.open("r", encoding="utf-8", newline="") as fh:
            fields = csv.DictReader(fh).fieldnames
        if fields != HISTORY_FIELDS:
            errors.append(f"official score history schema mismatch: {fields}")

    status = spec.get("status")
    if status == "ACTIVE":
        formula = spec.get("formula_spec")
        approval = spec.get("activation_approval") or {}
        if not isinstance(formula, dict):
            errors.append("ACTIVE score engine requires formula_spec")
        else:
            missing = [k for k in LOCKED if k not in formula]
            if missing:
                errors.append("ACTIVE formula_spec missing: " + ", ".join(missing))
        if approval.get("explicit_user_approval") is not True:
            errors.append("ACTIVE score engine requires explicit_user_approval=true")
        if not isinstance(approval.get("approved_at_kst"), str):
            errors.append("ACTIVE score engine requires approved_at_kst")
    elif status != "BLOCKED_MISSING_AUTHORITATIVE_FORMULA":
        errors.append("unknown score engine status")

    return errors, spec


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-active", action="store_true")
    args = parser.parse_args()

    errors, spec = validate()
    if errors:
        print("MASTER_MARKET_SCORE_ENGINE_GUARD=FAIL")
        for e in errors:
            print("-", e)
        return 1

    status = spec.get("status")
    print(f"MASTER_MARKET_SCORE_ENGINE_GUARD=PASS status={status}")
    if args.require_active and status != "ACTIVE":
        print("MASTER_MARKET_SCORE_ENGINE_ACTIVE=BLOCKED_MISSING_AUTHORITATIVE_FORMULA")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
