from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from structure_engine_r22 import classify_structure, fetch_completed_ohlc, load_contract

HERE = Path(__file__).resolve().parent


def replay_states(bars, left: int, right: int, max_points: int = 120):
    start = max(left + right + 12, len(bars) - max_points)
    states = []
    failures = 0
    for end in range(start, len(bars) + 1):
        try:
            r = classify_structure(bars[:end], left, right)
            states.append(r["state"])
        except Exception:
            failures += 1
    transitions = sum(1 for a, b in zip(states, states[1:]) if a != b)
    return {
        "observations": len(states),
        "failures": failures,
        "state_counts": dict(Counter(states)),
        "transitions": transitions,
        "transition_rate": (transitions / max(1, len(states) - 1)) if states else None,
        "latest_state": states[-1] if states else None,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="R2.2 structure shadow replay validation")
    p.add_argument("--output", type=Path, default=HERE / "output" / "structure_validation_r22.json")
    args = p.parse_args()

    now = datetime.now(timezone.utc)
    contract = load_contract()
    report = {
        "schema_version": "1.0",
        "engine_id": "BTC_TREND_V26_STRUCTURE_VALIDATION_R22",
        "asof_utc": now.isoformat().replace("+00:00", "Z"),
        "status": "PASS",
        "production_eligible": False,
        "timeframes": {},
    }

    for feature_id, cfg in contract["timeframes"].items():
        interval = str(cfg["interval"])
        minimum = int(cfg["minimum_completed_bars"])
        left = int(cfg["pivot_left"])
        right = int(cfg["pivot_right"])
        try:
            bars = fetch_completed_ohlc(interval, now, minimum)
            baseline = classify_structure(bars, left, right)
            sensitivity = {}
            for span in sorted({max(1, left - 1), left, left + 1}):
                try:
                    sensitivity[str(span)] = classify_structure(bars, span, span)["state"]
                except Exception as exc:
                    sensitivity[str(span)] = f"N/A:{type(exc).__name__}"
            valid_sensitivity = [v for v in sensitivity.values() if not str(v).startswith("N/A:")]
            replay = replay_states(bars, left, right)
            tf_pass = replay["observations"] >= 30 and replay["failures"] == 0
            if not tf_pass:
                report["status"] = "FAIL"
            report["timeframes"][feature_id] = {
                "status": "PASS" if tf_pass else "FAIL",
                "interval": interval,
                "completed_bars": len(bars),
                "pivot_left": left,
                "pivot_right": right,
                "current_state": baseline["state"],
                "current_facts": baseline["facts"],
                "confirmed_high_count": baseline["confirmed_high_count"],
                "confirmed_low_count": baseline["confirmed_low_count"],
                "sensitivity_states": sensitivity,
                "sensitivity_consensus": len(set(valid_sensitivity)) == 1 if valid_sensitivity else False,
                "rolling_replay": replay,
            }
        except Exception as exc:
            report["status"] = "FAIL"
            report["timeframes"][feature_id] = {
                "status": "FAIL",
                "interval": interval,
                "error": f"{type(exc).__name__}: {exc}",
            }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
