#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCORES = ROOT / "market_scoring/output/latest_scores.json"
HISTORY = ROOT / "state/master_market_score_history.csv"
KST = timezone(timedelta(hours=9))
OFFICIAL_HOURS = {1, 5, 9, 13, 17, 21}
FIELDS = [
    "run_id",
    "scheduled_kst",
    "score_generated_at_utc",
    "market_positive",
    "liquidity_lead",
    "crypto_money_inflow",
    "alt_money_inflow",
    "overall_coverage_pct",
    "status",
]


def load_json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must be a JSON object")
    return obj


def parse_utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("generated_at_utc missing")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def target_official_slot(generated_utc: datetime) -> datetime | None:
    generated_kst = generated_utc.astimezone(KST)
    # Scheduled scorer runs at :58, but GitHub can start a scheduled job a few
    # minutes late. Accept a narrow fail-closed window around the top of hour.
    if generated_kst.minute >= 45:
        target = (generated_kst + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    elif generated_kst.minute <= 15:
        target = generated_kst.replace(minute=0, second=0, microsecond=0)
    else:
        return None
    return target if target.hour in OFFICIAL_HOURS else None


def score_value(doc: dict[str, Any], key: str) -> float:
    row = (doc.get("scores") or {}).get(key) or {}
    value = row.get("score")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"score missing/non-numeric: {key}")
    value = float(value)
    if not 0 <= value <= 100:
        raise ValueError(f"score out of range: {key}={value}")
    return value


def ensure_header() -> None:
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    if not HISTORY.exists():
        HISTORY.write_text(",".join(FIELDS) + "\n", encoding="utf-8")
        return
    with HISTORY.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
    if header != FIELDS:
        raise ValueError(f"score history schema mismatch: {header}")


def existing_run_ids() -> set[str]:
    ensure_header()
    with HISTORY.open("r", encoding="utf-8", newline="") as fh:
        return {row.get("run_id", "") for row in csv.DictReader(fh) if row.get("run_id")}


def main() -> int:
    doc = load_json(SCORES)
    if doc.get("engine") != "MASTER_MARKET_SCORE_ENGINE_V1":
        raise SystemExit("SCORE_HISTORY_BLOCKED_ENGINE")
    if doc.get("status") not in {"ACTIVE_OK", "PARTIAL"}:
        raise SystemExit(f"SCORE_HISTORY_BLOCKED_STATUS={doc.get('status')}")
    if doc.get("official_persistence_eligible") is not True:
        raise SystemExit("SCORE_HISTORY_BLOCKED_INELIGIBLE")

    generated_utc = parse_utc(doc.get("generated_at_utc"))
    target = target_official_slot(generated_utc)
    if target is None:
        print("MASTER_MARKET_SCORE_HISTORY=SKIP_NON_OFFICIAL_SLOT")
        return 0

    run_id = f"MMARKET-V12-{target.strftime('%Y%m%d-%H00')}-KST"
    if run_id in existing_run_ids():
        print(f"MASTER_MARKET_SCORE_HISTORY=SKIP_DUPLICATE run_id={run_id}")
        return 0

    coverage = doc.get("overall_coverage_pct")
    if not isinstance(coverage, (int, float)) or isinstance(coverage, bool) or not 0 <= float(coverage) <= 100:
        raise SystemExit("SCORE_HISTORY_BLOCKED_COVERAGE")

    row = {
        "run_id": run_id,
        "scheduled_kst": target.strftime("%Y-%m-%d %H:%M KST"),
        "score_generated_at_utc": generated_utc.isoformat().replace("+00:00", "Z"),
        "market_positive": score_value(doc, "market_positive"),
        "liquidity_lead": score_value(doc, "liquidity_lead"),
        "crypto_money_inflow": score_value(doc, "crypto_money_inflow"),
        "alt_money_inflow": score_value(doc, "alt_money_inflow"),
        "overall_coverage_pct": float(coverage),
        "status": str(doc.get("status")),
    }
    with HISTORY.open("a", encoding="utf-8", newline="") as fh:
        csv.DictWriter(fh, fieldnames=FIELDS).writerow(row)

    print(f"MASTER_MARKET_SCORE_HISTORY=APPENDED run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
