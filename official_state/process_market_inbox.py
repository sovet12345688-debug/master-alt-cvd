#!/usr/bin/env python3
"""Process MASTER MARKET OFFICIAL state inbox files.

This bridge never calculates scores or market direction. It only persists an
already-completed, actual MASTER MARKET OFFICIAL payload. WATCH/manual
non-OFFICIAL payloads are rejected. The four locked core scores must be present
as actual current-run numbers; prior values are never copied to fill N/A.
"""
from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "official_state/inbox/market"
REJECTED = ROOT / "official_state/rejected/market"
PUBLISHER = ROOT / "official_state/publish_official_state.py"
HISTORY = ROOT / "state/master_market_official_history.csv"
CORE_KEYS = (
    "market_positive",
    "liquidity_lead",
    "crypto_money_inflow",
    "alt_money_inflow",
)
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
OFFICIAL_HOURS_KST = {1, 5, 9, 13, 17, 21}
KST = timezone(timedelta(hours=9))


def load_json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("payload must be a JSON object")
    return obj


def parse_dt(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("executed_kst must be an offset-aware ISO8601 string")
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ValueError("executed_kst must be offset-aware")
    return dt


def is_number_0_100(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= float(value) <= 100
    )


def precheck(state: dict[str, Any]) -> None:
    if state.get("master_id") != "market":
        raise ValueError("master_id must be market")
    if state.get("state_status") != "STORED":
        raise ValueError("state_status must be STORED")
    if state.get("source_path") != "master_prompts/master_market_v1_2_current.md":
        raise ValueError("source_path must point to MASTER MARKET canonical")
    if state.get("contract_path") != "state/master_market_v1_2_contract.json":
        raise ValueError("contract_path must point to MASTER MARKET contract")

    run = state.get("run") or {}
    if run.get("run_type") != "OFFICIAL":
        raise ValueError("run.run_type must be OFFICIAL; WATCH cannot persist")
    run_id = run.get("run_id")
    if not isinstance(run_id, str) or not run_id.startswith("MMARKET-V12-") or not run_id.endswith("-KST"):
        raise ValueError("run_id must match MMARKET-V12-...-KST policy")

    dt = parse_dt(run.get("executed_kst")).astimezone(KST)
    if dt.hour not in OFFICIAL_HOURS_KST:
        raise ValueError(f"executed_kst hour {dt.hour:02d} is not an OFFICIAL hour")
    if dt.utcoffset() != timedelta(hours=9):
        raise ValueError("executed_kst must resolve to Asia/Seoul UTC+09:00")

    core = state.get("core_state")
    if not isinstance(core, dict):
        raise ValueError("core_state must be an object")
    missing = [k for k in CORE_KEYS if not is_number_0_100(core.get(k))]
    if missing:
        raise ValueError(
            "current-run core scores required; N/A/prior-copy forbidden: "
            + ", ".join(missing)
        )

    dq = state.get("data_quality") or {}
    if not is_number_0_100(dq.get("coverage_pct")):
        raise ValueError("data_quality.coverage_pct must be numeric 0..100")
    confidence = dq.get("confidence")
    if not isinstance(confidence, str) or not confidence.strip():
        raise ValueError("data_quality.confidence is required")

    decision = state.get("decision") or {}
    if decision.get("direction") not in {"LONG", "SHORT"}:
        raise ValueError("decision.direction must be LONG or SHORT")
    if not isinstance(decision.get("risk_veto"), list):
        raise ValueError("decision.risk_veto must be a list")

    lineage = state.get("lineage") or {}
    if lineage.get("state_origin") != "ACTUAL_STORED_RUN":
        raise ValueError("lineage.state_origin must be ACTUAL_STORED_RUN")
    if lineage.get("canonical_identity_verified") is not True:
        raise ValueError("canonical_identity_verified must be true")
    if lineage.get("cross_master_decision_dependency") is not False:
        raise ValueError("cross_master_decision_dependency must be false")

    privacy = state.get("privacy") or {}
    if privacy.get("contains_personal_account_data") is not False:
        raise ValueError("personal account data is forbidden")
    if privacy.get("public_repository_safe") is not True:
        raise ValueError("public_repository_safe must be true")


def read_history() -> list[dict[str, str]]:
    if not HISTORY.exists():
        raise ValueError("official score history file is missing")
    with HISTORY.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != HISTORY_FIELDS:
            raise ValueError(f"official score history schema mismatch: {reader.fieldnames}")
        return list(reader)


def history_row(state: dict[str, Any]) -> dict[str, str]:
    run = state["run"]
    core = state["core_state"]
    dq = state["data_quality"]
    decision = state["decision"]
    dt = parse_dt(run["executed_kst"]).astimezone(KST)
    return {
        "run_id": str(run["run_id"]),
        "executed_kst": dt.strftime("%Y-%m-%d %H:%M KST"),
        "market_positive": str(core["market_positive"]),
        "liquidity_lead": str(core["liquidity_lead"]),
        "crypto_money_inflow": str(core["crypto_money_inflow"]),
        "alt_money_inflow": str(core["alt_money_inflow"]),
        "coverage": str(dq["coverage_pct"]),
        "confidence": str(dq["confidence"]),
        "direction": str(decision["direction"]),
        "risk_veto": "+".join(str(x) for x in decision.get("risk_veto") or []),
    }


def validate_history_order(state: dict[str, Any], rows: list[dict[str, str]]) -> None:
    rid = (state.get("run") or {}).get("run_id")
    if any(r.get("run_id") == rid for r in rows):
        raise ValueError(f"duplicate run_id in official score history: {rid}")
    if not rows:
        return
    last_text = rows[-1].get("executed_kst", "")
    last_dt = datetime.strptime(last_text, "%Y-%m-%d %H:%M KST").replace(tzinfo=KST)
    new_dt = parse_dt((state.get("run") or {}).get("executed_kst")).astimezone(KST)
    if new_dt <= last_dt:
        raise ValueError(
            f"new OFFICIAL run must be newer than score history: {new_dt.isoformat()} <= {last_dt.isoformat()}"
        )


def append_history(state: dict[str, Any]) -> None:
    rows = read_history()
    validate_history_order(state, rows)
    with HISTORY.open("a", encoding="utf-8", newline="") as fh:
        csv.DictWriter(fh, fieldnames=HISTORY_FIELDS).writerow(history_row(state))


def reject(path: Path, reason: str) -> None:
    REJECTED.mkdir(parents=True, exist_ok=True)
    dest = REJECTED / path.name
    if dest.exists():
        dest = REJECTED / f"{path.stem}.duplicate-reject{path.suffix}"
    shutil.move(str(path), str(dest))
    dest.with_suffix(dest.suffix + ".error.txt").write_text(
        reason.strip() + "\n", encoding="utf-8"
    )
    print(f"MARKET_BRIDGE_REJECTED file={dest.relative_to(ROOT)} reason={reason}")


def run_publisher(path: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, str(PUBLISHER), "--mode", "publish", "--input", str(path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    output = "\n".join(x for x in (proc.stdout.strip(), proc.stderr.strip()) if x).strip()
    return proc.returncode == 0, output or f"publisher exit={proc.returncode}"


def publish(path: Path) -> tuple[bool, str]:
    try:
        state = load_json(path)
        precheck(state)
        validate_history_order(state, read_history())
    except Exception as exc:
        return False, f"PRECHECK_FAIL: {exc}"

    ok, message = run_publisher(path)
    if not ok:
        return False, message

    try:
        append_history(state)
    except Exception as exc:
        return False, f"HISTORY_APPEND_FAIL_AFTER_PUBLISH: {exc}"
    return True, message


def main() -> int:
    INBOX.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in INBOX.glob("*.json") if p.is_file())
    if not files:
        print("MARKET_BRIDGE=NO_INPUT")
        return 0

    published = 0
    rejected = 0
    for path in files:
        ok, message = publish(path)
        if ok:
            path.unlink()
            published += 1
            print(f"MARKET_BRIDGE_PUBLISHED file={path.name} {message}")
        else:
            reject(path, message)
            rejected += 1

    print(f"MARKET_BRIDGE_SUMMARY published={published} rejected={rejected}")
    return 1 if rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
