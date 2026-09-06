from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import run_v26_v03 as v03

core = v03.core


def _accuracy(frame: pd.DataFrame) -> float | None:
    if frame.empty or "correct" not in frame.columns:
        return None
    ev = frame[frame["correct"].notna()]
    if ev.empty:
        return None
    return round(float(ev["correct"].astype(float).mean()) * 100.0, 2)


def confidence_calibration(price_wf: pd.DataFrame, cfg: dict) -> dict:
    if price_wf.empty:
        return {"pass": False, "reason": "no walk-forward rows"}

    ev = price_wf[price_wf["correct"].notna()].copy()
    if ev.empty:
        return {"pass": False, "reason": "no evaluable rows"}

    overall = _accuracy(ev)
    by_grade: dict[str, dict] = {}
    for grade in ("LOW", "MEDIUM", "HIGH"):
        g = ev[ev["confidence_grade"] == grade]
        by_grade[grade] = {
            "count": int(len(g)),
            "accuracy": _accuracy(g),
            "coverage_pct": round(float(len(g)) / float(len(ev)) * 100.0, 2),
            "median_score": round(float(g["confidence"].median()), 1) if len(g) else None,
        }

    rules = cfg.get("confidence_calibration_acceptance", {})
    min_high = int(rules.get("min_high_cases", 10))
    min_medium = int(rules.get("min_medium_cases", 10))
    min_high_gain = float(rules.get("min_high_accuracy_gain_pp", 5.0))
    max_high_coverage = float(rules.get("max_high_coverage_pct", 50.0))
    monotonic_tolerance = float(rules.get("monotonic_tolerance_pp", 2.0))

    high = by_grade["HIGH"]
    medium = by_grade["MEDIUM"]
    low = by_grade["LOW"]

    high_gain = None
    if overall is not None and high["accuracy"] is not None:
        high_gain = round(float(high["accuracy"]) - float(overall), 2)

    high_gate = bool(
        high["count"] >= min_high
        and high_gain is not None
        and high_gain >= min_high_gain
        and high["coverage_pct"] <= max_high_coverage
    )

    medium_vs_low_gate = True
    if medium["count"] >= min_medium and low["count"] >= min_medium:
        if medium["accuracy"] is None or low["accuracy"] is None:
            medium_vs_low_gate = False
        else:
            medium_vs_low_gate = bool(float(medium["accuracy"]) + monotonic_tolerance >= float(low["accuracy"]))

    high_vs_medium_gate = True
    if high["count"] >= min_high and medium["count"] >= min_medium:
        if high["accuracy"] is None or medium["accuracy"] is None:
            high_vs_medium_gate = False
        else:
            high_vs_medium_gate = bool(float(high["accuracy"]) + monotonic_tolerance >= float(medium["accuracy"]))

    nondegenerate_gate = bool(high["count"] < len(ev))
    calibration_pass = bool(high_gate and high_vs_medium_gate and medium_vs_low_gate and nondegenerate_gate)

    return {
        "pass": calibration_pass,
        "evaluable": int(len(ev)),
        "overall_accuracy": overall,
        "by_grade": by_grade,
        "high_accuracy_gain_pp": high_gain,
        "gates": {
            "high_gate": high_gate,
            "high_vs_medium_gate": high_vs_medium_gate,
            "medium_vs_low_gate": medium_vs_low_gate,
            "nondegenerate_gate": nondegenerate_gate,
        },
        "rule": (
            f"HIGH must have >= {min_high} cases, beat overall accuracy by >= {min_high_gain:.1f}pp, "
            f"cover <= {max_high_coverage:.1f}% of evaluable rows, and confidence tiers must be monotonic "
            f"within {monotonic_tolerance:.1f}pp tolerance."
        ),
    }


def current_confidence_sanity(current_price_core: dict) -> dict:
    conf = current_price_core.get("confidence", {})
    share = float(conf.get("majority_share_pct") or 0.0)
    grade = conf.get("grade")
    score = int(conf.get("score") or 0)
    # 66:34-like mixed consensus must never be labeled HIGH.
    mixed_consensus_guard = not (share < 75.0 and grade == "HIGH")
    return {
        "pass": bool(mixed_consensus_guard),
        "score": score,
        "grade": grade,
        "majority_share_pct": share,
        "mixed_consensus_guard": bool(mixed_consensus_guard),
        "rule": "Consensus below 75% cannot be HIGH regardless of raw score.",
    }


def main_v031() -> None:
    # Run the V0.3 price-core + confirmation architecture first.
    v03.main_v03()

    cfg = core.load_config()
    out = core.OUT

    price_wf_path = out / "walk_forward_price_core.csv"
    decision_path = out / "model_decision.json"
    price_current_path = out / "current_price_core.json"

    price_wf = pd.read_csv(price_wf_path, parse_dates=["time"]).set_index("time")
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    current_price_core = json.loads(price_current_path.read_text(encoding="utf-8"))

    calibration = confidence_calibration(price_wf, cfg)
    sanity = current_confidence_sanity(current_price_core)
    confirmation = decision.get("acceptance", {})
    confirmation_pass = bool(confirmation.get("utility_pass"))

    master_ready = bool(calibration.get("pass") and sanity.get("pass") and confirmation_pass)

    final = {
        "engine": "BTC_HISTORICAL_REGIME_OUTCOME_V0_3_1",
        "schema_version": "0.3.1",
        "architecture": "PRICE_CORE_WITH_ONCHAIN_MACRO_CONFIRMATION_AND_CALIBRATED_CONFIDENCE",
        "current_price_core": {
            "direction_case_share": current_price_core.get("direction_case_share"),
            "confidence": current_price_core.get("confidence"),
        },
        "confidence_sanity": sanity,
        "confidence_calibration": calibration,
        "confirmation_acceptance": confirmation,
        "master_readiness": "PASS" if master_ready else "FAIL",
        "master_integration": "FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL",
        "next_step": (
            "BUILD_MODERN_DERIVATIVES_LAYER_B"
            if master_ready
            else "KEEP_AS_RESEARCH_ONLY_AND_FIX_FAILED_GATE_BEFORE_LAYER_B"
        ),
    }

    (out / "v031_validation_summary.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(final, ensure_ascii=False))


if __name__ == "__main__":
    main_v031()
