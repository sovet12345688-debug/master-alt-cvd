from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from feature_engine import build_live_features
from structure_engine_r22 import build_structure_features

HERE = Path(__file__).resolve().parent
STRUCTURE_IDS = ["STRUCTURE_1D", "STRUCTURE_4H", "STRUCTURE_1W"]


def build_live_features_r22(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    payload = build_live_features(now)
    if payload.get("status") == "VALIDATION_FAIL":
        payload["engine_id"] = "BTC_TREND_V26_FEATURE_ENGINE_R22"
        payload["schema_version"] = "1.3"
        payload["production_eligible"] = False
        payload["official_state_write_allowed"] = False
        return payload

    structure = build_structure_features(now)
    features = payload.setdefault("features", {})
    for fid in STRUCTURE_IDS:
        if fid in structure:
            features[fid] = structure[fid]

    current_count = sum(
        1 for record in features.values()
        if isinstance(record, dict) and record.get("availability") == "CURRENT"
    )
    current_weight = 0
    weights = {
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
    for fid, weight in weights.items():
        rec = features.get(fid)
        if isinstance(rec, dict) and rec.get("availability") == "CURRENT":
            current_weight += weight

    payload.update({
        "schema_version": "1.3",
        "engine_id": "BTC_TREND_V26_FEATURE_ENGINE_R22",
        "status": "OK_SHADOW" if current_count else "FEATURES_UNAVAILABLE",
        "current_feature_count": current_count,
        "current_weight": current_weight,
        "structure_engine": {
            "id": "BTC_TREND_V26_STRUCTURE_R22",
            "mode": "SHADOW_ONLY",
            "production_eligible": False,
            "official_state_write_allowed": False,
        },
        "production_eligible": False,
        "official_state_write_allowed": False,
    })
    return payload


def main() -> int:
    p = argparse.ArgumentParser(description="MASTER BTC TREND V2.6 R2.2 structure shadow feature engine")
    p.add_argument("--output", type=Path, default=HERE / "output" / "latest_features_r22.json")
    args = p.parse_args()
    try:
        payload = build_live_features_r22()
    except Exception as exc:
        payload = {
            "schema_version": "1.3",
            "engine_id": "BTC_TREND_V26_FEATURE_ENGINE_R22",
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
