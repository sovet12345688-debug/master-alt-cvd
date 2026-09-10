from __future__ import annotations

import argparse
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SCORE_CONTRACT_PATH = HERE / "score_contract.json"


class ScoreValidationError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise ScoreValidationError(f"JSON root must be object: {path}")
    return obj


def coverage_grade(pct: Decimal) -> str:
    if pct >= Decimal("85"):
        return "A"
    if pct >= Decimal("70"):
        return "B"
    if pct >= Decimal("50"):
        return "C"
    return "LOW"


def round_half_up_int(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def validate_current_record(feature_id: str, record: dict[str, Any], valid_states: set[str]) -> None:
    state = record.get("state")
    if state not in valid_states:
        raise ScoreValidationError(f"{feature_id}: invalid CURRENT state {state!r}")
    for field in ("feature_timestamp", "source_timestamps", "lineage"):
        if field not in record or record[field] in (None, "", [], {}):
            raise ScoreValidationError(f"{feature_id}: CURRENT record missing required {field}")


def compute_trend_score(feature_payload: dict[str, Any], contract: dict[str, Any] | None = None) -> dict[str, Any]:
    c = contract or load_json(SCORE_CONTRACT_PATH)
    if feature_payload.get("asof_utc") in (None, ""):
        raise ScoreValidationError("input missing asof_utc")
    features = feature_payload.get("features")
    if not isinstance(features, dict):
        raise ScoreValidationError("input features must be an object")

    weights_raw = c.get("trend_weights")
    states_raw = c.get("directional_state_values")
    if not isinstance(weights_raw, dict) or not isinstance(states_raw, dict):
        raise ScoreValidationError("score contract missing trend_weights or directional_state_values")

    weights = {k: Decimal(str(v)) for k, v in weights_raw.items()}
    state_values = {k: Decimal(str(v)) for k, v in states_raw.items()}
    valid_states = set(state_values)
    usable = set(c["availability_policy"]["usable"])
    excluded = set(c["availability_policy"]["excluded_and_renormalized"])

    total_weight = sum(weights.values(), Decimal("0"))
    contract_total = Decimal(str(c.get("weight_total", 100)))
    if total_weight != contract_total:
        raise ScoreValidationError(f"trend weight total mismatch: {total_weight} != {contract_total}")

    valid_weight = Decimal("0")
    weighted_sum = Decimal("0")
    components: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []

    for feature_id, weight in weights.items():
        record = features.get(feature_id)
        if record is None:
            unavailable.append({
                "feature_id": feature_id,
                "weight": int(weight),
                "reason": "FEATURE_RECORD_MISSING",
            })
            continue
        if not isinstance(record, dict):
            raise ScoreValidationError(f"{feature_id}: feature record must be object")

        availability = record.get("availability")
        if availability in usable:
            validate_current_record(feature_id, record, valid_states)
            state = str(record["state"])
            state_value = state_values[state]
            contribution = weight * state_value
            valid_weight += weight
            weighted_sum += contribution
            components.append({
                "feature_id": feature_id,
                "weight": int(weight),
                "availability": availability,
                "state": state,
                "state_value": float(state_value),
                "weighted_contribution": float(contribution),
                "feature_timestamp": record.get("feature_timestamp"),
                "source_timestamps": record.get("source_timestamps"),
                "lineage": record.get("lineage"),
            })
        elif availability in excluded:
            unavailable.append({
                "feature_id": feature_id,
                "weight": int(weight),
                "reason": availability,
            })
        else:
            raise ScoreValidationError(f"{feature_id}: invalid availability {availability!r}")

    coverage_pct = (valid_weight / total_weight * Decimal("100")) if total_weight else Decimal("0")
    grade = coverage_grade(coverage_pct)

    base = {
        "schema_version": "1.0",
        "engine_id": "BTC_TREND_V26_TREND_SCORE_ENGINE_R2",
        "asof_utc": feature_payload["asof_utc"],
        "coverage_pct": float(coverage_pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "coverage_grade": grade,
        "valid_weight": int(valid_weight),
        "components": components,
        "unavailable_features": unavailable,
        "strong_confirmation_coverage_met": coverage_pct >= Decimal(str(c["coverage"]["strong_confirmation_min"])),
        "calibrated_probability": False,
        "entry_signal": False,
        "production_eligible": False,
    }

    if valid_weight == 0:
        return {
            **base,
            "status": "SCORE_UNAVAILABLE",
            "directional_score": None,
            "long": None,
            "short": None,
            "trend_strength": None,
            "dominant_state": None,
        }

    directional = weighted_sum / valid_weight
    if directional < Decimal("-1") or directional > Decimal("1"):
        raise ScoreValidationError(f"directional score out of range: {directional}")

    long_raw = Decimal("50") * (Decimal("1") + directional)
    long_display = round_half_up_int(long_raw)
    long_display = max(0, min(100, long_display))
    short_display = 100 - long_display
    strength = abs(long_display - short_display)

    gap_min = int(c["dominance"]["dominant_gap_points_min"])
    if long_display - short_display >= gap_min:
        dominant = "LONG"
    elif short_display - long_display >= gap_min:
        dominant = "SHORT"
    else:
        dominant = "NEUTRAL"

    return {
        **base,
        "status": "OK",
        "directional_score": float(directional.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)),
        "long": long_display,
        "short": short_display,
        "trend_strength": strength,
        "dominant_state": dominant,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="MASTER BTC TREND V2.6 R2 deterministic trend score engine")
    p.add_argument("input", type=Path, help="feature payload JSON")
    p.add_argument("--output", type=Path, default=None, help="optional score output JSON")
    args = p.parse_args()

    try:
        payload = load_json(args.input)
        result = compute_trend_score(payload)
    except (OSError, json.JSONDecodeError, ScoreValidationError) as exc:
        result = {
            "schema_version": "1.0",
            "engine_id": "BTC_TREND_V26_TREND_SCORE_ENGINE_R2",
            "status": "VALIDATION_FAIL",
            "error": str(exc),
            "production_eligible": False,
        }
        text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
        else:
            print(text, end="")
        return 2

    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
