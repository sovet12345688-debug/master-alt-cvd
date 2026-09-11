from __future__ import annotations

import argparse
import json
from pathlib import Path

from volume_wave_engine_r24 import build_candidate

MIN_1D_SIGNALS = 25
MIN_1D_HIT_RATE = 53.0
MIN_COMBINED_SIGNALS = 12
MIN_COMBINED_HIT_RATE = 53.0


def positive(x) -> bool:
    return x is not None and float(x) > 0.0


def validate() -> dict:
    candidate = build_candidate()
    errors: list[str] = []
    if candidate.get("status") != "SHADOW_CANDIDATE":
        errors.append("candidate status is not SHADOW_CANDIDATE")

    feature = candidate.get("feature") or {}
    decision_status = feature.get("decision_status")
    availability = feature.get("availability")
    state = feature.get("state")

    if decision_status not in {"CURRENT", "INSUFFICIENT_EVIDENCE"}:
        errors.append("invalid decision_status")
    if decision_status == "CURRENT":
        if availability != "CURRENT":
            errors.append("CURRENT decision must have CURRENT availability")
        if state not in {"LONG", "SHORT", "NEUTRAL"}:
            errors.append("CURRENT decision must have valid directional state")
    else:
        if availability == "CURRENT":
            errors.append("INSUFFICIENT_EVIDENCE must never be CURRENT")
        if state is not None:
            errors.append("INSUFFICIENT_EVIDENCE must not invent NEUTRAL/directional state")

    lineage = feature.get("lineage") or {}
    if lineage.get("completed_candle_only") is not True:
        errors.append("completed-candle lineage missing")
    if lineage.get("strong_states_allowed") is not False:
        errors.append("strong states must remain disabled")
    if candidate.get("production_approved") is not False:
        errors.append("production_approved must be false")
    if candidate.get("official_state_write_allowed") is not False:
        errors.append("official_state_write_allowed must be false")

    replay = candidate.get("replay_diagnostics") or {}
    for key in ("1D", "4H"):
        diag = replay.get(key) or {}
        if int(diag.get("observations") or 0) <= 0:
            errors.append(f"{key} replay has no observations")

    one_d = replay.get("1D") or {}
    strict = replay.get("A_STRICT_COMBINED") or {}
    primary = replay.get("B_1D_PRIMARY_COMBINED") or {}

    performance_checks = {
        "1d_signal_count": int(one_d.get("signals") or 0) >= MIN_1D_SIGNALS,
        "1d_hit_rate": one_d.get("hit_rate_pct") is not None and float(one_d["hit_rate_pct"]) >= MIN_1D_HIT_RATE,
        "1d_positive_signed_return": positive(one_d.get("avg_signed_forward_return_pct")),
        "combined_signal_count": int(primary.get("signals") or 0) >= MIN_COMBINED_SIGNALS,
        "combined_hit_rate": primary.get("hit_rate_pct") is not None and float(primary["hit_rate_pct"]) >= MIN_COMBINED_HIT_RATE,
        "combined_positive_signed_return": positive(primary.get("avg_signed_forward_return_pct")),
    }
    performance_gate = "SHADOW_GATE_PASS" if all(performance_checks.values()) else "HOLD"

    semantic_checks = {
        "insufficient_not_current": decision_status != "INSUFFICIENT_EVIDENCE" or availability != "CURRENT",
        "insufficient_not_neutral": decision_status != "INSUFFICIENT_EVIDENCE" or state is None,
        "production_locked": candidate.get("production_approved") is False and candidate.get("official_state_write_allowed") is False,
    }

    return {
        "schema_version": "1.0",
        "validation_id": "BTC_TREND_V26_VOLUME_WAVE_R24_SHADOW_VALIDATION",
        "status": "TECHNICAL_PASS" if not errors else "FAIL",
        "errors": errors,
        "current_decision_status": decision_status,
        "current_state": state,
        "current_reason": feature.get("decision_reason"),
        "semantic_checks": semantic_checks,
        "performance_gate": performance_gate,
        "performance_thresholds_predeclared": {
            "min_1d_signals": MIN_1D_SIGNALS,
            "min_1d_hit_rate_pct": MIN_1D_HIT_RATE,
            "min_combined_signals": MIN_COMBINED_SIGNALS,
            "min_combined_hit_rate_pct": MIN_COMBINED_HIT_RATE,
            "average_signed_forward_return_must_be_positive": True,
            "4h_standalone_is_diagnostic_only": True,
        },
        "performance_checks": performance_checks,
        "replay_diagnostics": replay,
        "ab_summary": {
            "A_STRICT": strict,
            "B_1D_PRIMARY": primary,
        },
        "predictive_validation": "SHADOW_ONLY_NOT_AUTO_PROMOTION",
        "production_approved": False,
        "official_state_write_allowed": False,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    try:
        result = validate()
    except Exception as exc:
        result = {
            "schema_version": "1.0",
            "validation_id": "BTC_TREND_V26_VOLUME_WAVE_R24_SHADOW_VALIDATION",
            "status": "FAIL",
            "errors": [f"{type(exc).__name__}: {exc}"],
            "performance_gate": "HOLD",
            "production_approved": False,
            "official_state_write_allowed": False,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "TECHNICAL_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
