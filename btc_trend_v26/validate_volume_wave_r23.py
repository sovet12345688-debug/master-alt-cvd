from __future__ import annotations

import argparse
import json
from pathlib import Path

from volume_wave_engine_r23 import build_candidate


def validate() -> dict:
    candidate = build_candidate()
    errors: list[str] = []
    if candidate.get("status") != "SHADOW_CANDIDATE":
        errors.append("candidate status is not SHADOW_CANDIDATE")
    feature = candidate.get("feature") or {}
    if feature.get("availability") != "CURRENT":
        errors.append("feature availability is not CURRENT")
    if feature.get("state") not in {"LONG", "SHORT", "NEUTRAL"}:
        errors.append("feature state outside allowed R2.3 set")
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
    for tf in ("1D", "4H"):
        diag = replay.get(tf) or {}
        if int(diag.get("observations") or 0) <= 0:
            errors.append(f"{tf} replay has no observations")

    return {
        "schema_version": "1.0",
        "validation_id": "BTC_TREND_V26_VOLUME_WAVE_R23_SHADOW_VALIDATION",
        "status": "TECHNICAL_PASS" if not errors else "FAIL",
        "errors": errors,
        "current_state": feature.get("state"),
        "replay_diagnostics": replay,
        "predictive_validation": "OBSERVATION_ONLY_NOT_A_PRODUCTION_PROMOTION_GATE",
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
            "validation_id": "BTC_TREND_V26_VOLUME_WAVE_R23_SHADOW_VALIDATION",
            "status": "FAIL",
            "errors": [f"{type(exc).__name__}: {exc}"],
            "production_approved": False,
            "official_state_write_allowed": False,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "TECHNICAL_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
