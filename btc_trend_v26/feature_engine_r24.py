from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from feature_engine_r22 import build_live_features_r22
from volume_wave_engine_r24 import build_candidate

HERE = Path(__file__).resolve().parent
WEIGHTS = {
    "STRUCTURE_1D": 22,
    "STRUCTURE_4H": 10,
    "STRUCTURE_1W": 8,
    "VOLUME_WAVE": 15,
    "EMA_MA_STACK": 10,
    "SPOT_ETF_FLOW": 10,
    "DERIVATIVES_STATE": 8,
    "VALUE_OVEREXTENSION": 7,
    "LIQUIDITY_MACRO": 10,
}


def build_live_features_r24(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    payload = build_live_features_r22(now)
    payload["schema_version"] = "1.5"
    payload["engine_id"] = "BTC_TREND_V26_FEATURE_ENGINE_R24"
    payload["production_eligible"] = False
    payload["official_state_write_allowed"] = False

    features = payload.setdefault("features", {})
    baseline_vw = features.get("VOLUME_WAVE")
    try:
        candidate = build_candidate(now)
        feature = candidate.get("feature") or {}
        inject = candidate.get("status") == "SHADOW_CANDIDATE" and feature.get("availability") == "CURRENT"
        if inject:
            features["VOLUME_WAVE"] = feature
        payload["volume_wave_shadow"] = {
            "engine_id": candidate.get("engine_id"),
            "status": candidate.get("status"),
            "decision_status": feature.get("decision_status"),
            "decision_reason": feature.get("decision_reason"),
            "candidate_state": feature.get("state"),
            "injected_as_current": bool(inject),
            "baseline_availability_preserved": None if inject else (baseline_vw or {}).get("availability"),
            "ab_current": candidate.get("ab_current"),
            "replay_diagnostics": candidate.get("replay_diagnostics"),
            "production_approved": False,
            "official_state_write_allowed": False,
        }
    except Exception as exc:
        payload["volume_wave_shadow"] = {
            "status": "VALIDATION_FAIL",
            "error": f"{type(exc).__name__}: {exc}",
            "injected_as_current": False,
            "baseline_availability_preserved": (baseline_vw or {}).get("availability"),
            "production_approved": False,
            "official_state_write_allowed": False,
        }

    current_count = sum(
        1 for rec in features.values()
        if isinstance(rec, dict) and rec.get("availability") == "CURRENT"
    )
    current_weight = sum(
        weight for fid, weight in WEIGHTS.items()
        if isinstance(features.get(fid), dict) and features[fid].get("availability") == "CURRENT"
    )
    payload["current_feature_count"] = current_count
    payload["current_weight"] = current_weight
    payload["status"] = "OK_SHADOW" if current_count else "FEATURES_UNAVAILABLE"
    return payload


def main() -> int:
    p = argparse.ArgumentParser(description="MASTER BTC TREND V2.6 R2.4 feature engine with evidence-aware Volume/Wave shadow")
    p.add_argument("--output", type=Path, default=HERE / "output" / "latest_features_r24.json")
    args = p.parse_args()
    try:
        payload = build_live_features_r24()
    except Exception as exc:
        payload = {
            "schema_version": "1.5",
            "engine_id": "BTC_TREND_V26_FEATURE_ENGINE_R24",
            "status": "VALIDATION_FAIL",
            "error": f"{type(exc).__name__}: {exc}",
            "production_eligible": False,
            "official_state_write_allowed": False,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") != "VALIDATION_FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
