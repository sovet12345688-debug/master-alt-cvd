#!/usr/bin/env python3
"""MONEY MASTER OS P1 Source Health.

Normalizes independent collector/workflow health into one compact source-health state.
This is DATA AVAILABILITY metadata only. It never creates market facts, scores, trade
permissions, directions, entries, stops, targets or MASTER conclusions.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "source_health/registry.json"
OUTPUT_PATH = ROOT / "source_health/output/latest.json"
LAST_GOOD_PATH = ROOT / "source_health/state/last_good.json"
EVENTS_DIR = ROOT / "source_health/events"
MASTER_REGISTRY_PATH = ROOT / "money_master_os/registry/MASTER_REGISTRY.json"

BAD = {"STALE", "FAILED", "MISSING"}
STATUS_SCORE = {
    "HEALTHY": 1.0,
    "DEGRADED": 0.60,
    "STALE": 0.20,
    "FAILED": 0.0,
    "MISSING": 0.0,
    "UNKNOWN": 0.40,
}
IMPACT_WEIGHT = {"CORE": 2.0, "OPTIONAL": 1.0, "CONTEXT": 0.25}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def get_field(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def numeric(v: Any) -> float | None:
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def eval_check(state: dict[str, Any], check: dict[str, Any]) -> tuple[bool, str]:
    kind = check.get("type")
    field = check.get("field")
    if kind == "equals":
        actual = get_field(state, field)
        expected = check.get("value")
        return actual == expected, f"{field}={actual!r} expected {expected!r}"
    if kind == "min":
        actual = numeric(get_field(state, field))
        threshold = float(check.get("value"))
        return actual is not None and actual >= threshold, f"{field}={actual} min={threshold}"
    if kind == "empty_dict":
        actual = get_field(state, field)
        return isinstance(actual, dict) and len(actual) == 0, f"{field} must be empty dict"
    if kind == "min_len":
        actual = get_field(state, field)
        minimum = int(check.get("value"))
        ok = isinstance(actual, (list, dict, str)) and len(actual) >= minimum
        return ok, f"len({field})={len(actual) if hasattr(actual, '__len__') else None} min={minimum}"
    if kind == "contains_all":
        actual = get_field(state, field)
        expected = set(check.get("values") or [])
        got = set(actual or []) if isinstance(actual, list) else set()
        return expected.issubset(got), f"{field} missing={sorted(expected-got)}"
    if kind == "min_ratio":
        num = numeric(get_field(state, check.get("numerator")))
        den = numeric(get_field(state, check.get("denominator")))
        threshold = float(check.get("value"))
        ratio = None if num is None or den in (None, 0) else num / den
        return ratio is not None and ratio >= threshold, f"ratio={ratio} min={threshold}"
    if kind == "max_ratio_sum":
        num = numeric(get_field(state, check.get("numerator")))
        vals = [numeric(get_field(state, x)) for x in (check.get("denominator_fields") or [])]
        threshold = float(check.get("value"))
        if num is None or any(v is None for v in vals):
            return False, "ratio inputs missing"
        den = sum(v for v in vals if v is not None)
        ratio = 0.0 if den == 0 else num / den
        return ratio <= threshold, f"ratio={ratio} max={threshold}"
    if kind == "min_sum":
        vals = [numeric(get_field(state, x)) for x in (check.get("fields") or [])]
        threshold = float(check.get("value"))
        if any(v is None for v in vals):
            return False, "sum inputs missing"
        total = sum(v for v in vals if v is not None)
        return total >= threshold, f"sum={total} min={threshold}"
    return False, f"unsupported check type={kind}"


def github_runs(repo: str, workflow_file: str, token: str | None) -> tuple[list[dict[str, Any]], str | None]:
    if not token or not repo:
        return [], "WORKFLOW_API_TOKEN_UNAVAILABLE"
    wf = urllib.parse.quote(workflow_file, safe="")
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{wf}/runs?branch=main&per_page=5"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "money-master-os-source-health",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload.get("workflow_runs") or [], None
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        return [], f"WORKFLOW_API_ERROR:{type(exc).__name__}"


def workflow_health(runs: list[dict[str, Any]], api_error: str | None) -> dict[str, Any]:
    out = {
        "latest_run_id": None,
        "latest_status": None,
        "latest_conclusion": None,
        "latest_event": None,
        "latest_started_at_utc": None,
        "latest_completed_at_utc": None,
        "last_success_at_utc": None,
        "api_error": api_error,
    }
    if not runs:
        return out
    latest = runs[0]
    out.update(
        {
            "latest_run_id": latest.get("id"),
            "latest_status": latest.get("status"),
            "latest_conclusion": latest.get("conclusion"),
            "latest_event": latest.get("event"),
            "latest_started_at_utc": latest.get("run_started_at") or latest.get("created_at"),
            "latest_completed_at_utc": latest.get("updated_at") if latest.get("status") == "completed" else None,
        }
    )
    for run in runs:
        if run.get("status") == "completed" and run.get("conclusion") == "success":
            out["last_success_at_utc"] = run.get("updated_at") or run.get("run_started_at")
            break
    return out


def evaluate_source(source_id: str, cfg: dict[str, Any], now: datetime, repo: str, token: str | None) -> dict[str, Any]:
    reasons: list[str] = []
    details: list[str] = []
    state_path = ROOT / cfg["state_path"] if cfg.get("state_path") else None
    state: dict[str, Any] | None = None

    if state_path is None:
        status = "UNKNOWN"
        reasons.append("STATE_PATH_NOT_CONFIGURED")
    elif not state_path.exists():
        status = "MISSING"
        reasons.append("STATE_FILE_MISSING")
    else:
        state = load_json(state_path)
        if not isinstance(state, dict):
            status = "FAILED"
            reasons.append("STATE_JSON_INVALID")
        else:
            ts = parse_dt(get_field(state, cfg.get("timestamp_field", "last_run_utc")))
            if ts is None:
                status = "DEGRADED"
                reasons.append("STATE_TIMESTAMP_MISSING")
            else:
                age_minutes = max(0.0, (now - ts).total_seconds() / 60.0)
                if age_minutes > float(cfg.get("stale_after_minutes", 180)):
                    status = "STALE"
                    reasons.append("STATE_STALE")
                elif age_minutes > float(cfg.get("warn_after_minutes", 120)):
                    status = "DEGRADED"
                    reasons.append("STATE_AGING")
                else:
                    status = "HEALTHY"
            for check in cfg.get("checks") or []:
                ok, detail = eval_check(state, check)
                if not ok:
                    details.append(detail)
                    reasons.append("QUALITY_CHECK_FAILED")
                    if status == "HEALTHY":
                        status = "DEGRADED"

    runs, api_error = github_runs(repo, cfg.get("workflow_file", ""), token)
    wf = workflow_health(runs, api_error)
    latest_status = wf.get("latest_status")
    latest_conclusion = wf.get("latest_conclusion")
    if api_error:
        reasons.append("WORKFLOW_API_UNAVAILABLE")
    elif not runs:
        reasons.append("WORKFLOW_RUN_MISSING")
        if status == "HEALTHY":
            status = "DEGRADED"
    elif latest_status == "completed" and latest_conclusion not in (None, "success", "skipped"):
        reasons.append("LATEST_WORKFLOW_FAILED")
        status = "FAILED"
    elif latest_status in ("queued", "in_progress", "waiting", "pending"):
        reasons.append("WORKFLOW_RUNNING")

    ts = None
    age_minutes = None
    if isinstance(state, dict):
        ts = parse_dt(get_field(state, cfg.get("timestamp_field", "last_run_utc")))
        if ts is not None:
            age_minutes = max(0.0, (now - ts).total_seconds() / 60.0)

    clean_reasons = []
    for r in reasons:
        if r not in clean_reasons:
            clean_reasons.append(r)
    if status == "HEALTHY" and not clean_reasons:
        clean_reasons = ["OK"]

    return {
        "source_id": source_id,
        "display_name": cfg.get("display_name", source_id),
        "status": status,
        "reason_codes": clean_reasons,
        "quality_details": details[:8],
        "state_path": cfg.get("state_path"),
        "state_timestamp_field": cfg.get("timestamp_field"),
        "state_timestamp_utc": iso(ts),
        "state_age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
        "warn_after_minutes": cfg.get("warn_after_minutes"),
        "stale_after_minutes": cfg.get("stale_after_minutes"),
        "expected_interval_minutes": cfg.get("expected_interval_minutes"),
        "workflow_file": cfg.get("workflow_file"),
        "workflow": wf,
        "impact": cfg.get("impact") or {},
    }


def aggregate_masters(master_ids: list[str], sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for mid in master_ids:
        rows = []
        weighted_total = 0.0
        weighted_score = 0.0
        core_bad = 0
        core_degraded = 0
        non_context_bad = 0
        for sid, src in sources.items():
            impact = (src.get("impact") or {}).get(mid)
            if not impact:
                continue
            status = src.get("status", "UNKNOWN")
            rows.append({"source_id": sid, "impact": impact, "status": status, "reason_codes": src.get("reason_codes", [])})
            weight = IMPACT_WEIGHT.get(impact, 0.25)
            weighted_total += weight
            weighted_score += weight * STATUS_SCORE.get(status, 0.4)
            if impact == "CORE" and status in BAD:
                core_bad += 1
            elif impact == "CORE" and status in {"DEGRADED", "UNKNOWN"}:
                core_degraded += 1
            if impact != "CONTEXT" and status in BAD:
                non_context_bad += 1
        pct = None if weighted_total == 0 else round(100.0 * weighted_score / weighted_total, 1)
        if core_bad:
            availability = "POOR"
        elif core_degraded or non_context_bad:
            availability = "PARTIAL"
        else:
            availability = "GOOD"
        out[mid] = {
            "registered_source_availability": availability,
            "registered_source_health_pct": pct,
            "registered_source_count": len(rows),
            "core_bad": core_bad,
            "non_context_bad": non_context_bad,
            "sources": rows,
            "scope_note": "REGISTERED SHARED/REPOSITORY SOURCES ONLY. This is not MASTER Coverage and is not an analytical score.",
            "policy": "DATA HEALTH ONLY; not direction, score, permission or execution gate",
        }
    return out


def build(repo: str, token: str | None) -> dict[str, Any]:
    reg = load_json(REGISTRY_PATH, {})
    if not isinstance(reg, dict) or reg.get("schema_version") != "1.0":
        raise RuntimeError("invalid source_health/registry.json")
    now = utcnow()
    sources = {
        sid: evaluate_source(sid, cfg, now, repo, token)
        for sid, cfg in (reg.get("sources") or {}).items()
    }
    mreg = load_json(MASTER_REGISTRY_PATH, {}) or {}
    master_ids = list((mreg.get("masters") or {}).keys()) or ["market", "btc_trend", "alt_top100", "alt_final20", "trading"]
    counts = Counter(src["status"] for src in sources.values())
    overall = "HEALTHY"
    if counts.get("FAILED", 0) or counts.get("MISSING", 0) or counts.get("STALE", 0):
        overall = "DEGRADED"
    return {
        "schema_version": "1.0",
        "system": "MONEY_MASTER_OS_SOURCE_HEALTH",
        "evaluated_at_utc": iso(now),
        "repository": repo,
        "overall": overall,
        "counts": {k: counts.get(k, 0) for k in ["HEALTHY", "DEGRADED", "STALE", "FAILED", "MISSING", "UNKNOWN"]},
        "sources": sources,
        "masters": aggregate_masters(master_ids, sources),
        "policy": reg.get("policy"),
    }


def update_last_good(payload: dict[str, Any]) -> dict[str, Any]:
    old = load_json(LAST_GOOD_PATH, {}) or {}
    result = dict(old)
    result.setdefault("schema_version", "1.0")
    result.setdefault("sources", {})
    for sid, src in payload.get("sources", {}).items():
        if src.get("status") == "HEALTHY":
            result["sources"][sid] = {
                "state_timestamp_utc": src.get("state_timestamp_utc"),
                "observed_healthy_at_utc": payload.get("evaluated_at_utc"),
                "state_path": src.get("state_path"),
                "workflow_file": src.get("workflow_file"),
            }
    result["updated_at_utc"] = payload.get("evaluated_at_utc")
    return result


def attach_last_good(payload: dict[str, Any], last_good: dict[str, Any]) -> None:
    for sid, src in payload.get("sources", {}).items():
        src["last_good"] = (last_good.get("sources") or {}).get(sid)


def status_fingerprint(src: dict[str, Any]) -> tuple[Any, ...]:
    return (src.get("status"), tuple(src.get("reason_codes") or []))


def changed_events(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    events = []
    prev_sources = previous.get("sources") or {}
    for sid, src in (current.get("sources") or {}).items():
        prev = prev_sources.get(sid)
        if prev is None or status_fingerprint(prev) != status_fingerprint(src):
            events.append(
                {
                    "time_utc": current.get("evaluated_at_utc"),
                    "source_id": sid,
                    "from": prev.get("status") if isinstance(prev, dict) else None,
                    "to": src.get("status"),
                    "reason_codes": src.get("reason_codes"),
                    "state_timestamp_utc": src.get("state_timestamp_utc"),
                }
            )
    return events


def write_payload(payload: dict[str, Any]) -> None:
    previous = load_json(OUTPUT_PATH, {}) or {}
    last_good = update_last_good(payload)
    attach_last_good(payload, last_good)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_GOOD_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    LAST_GOOD_PATH.write_text(json.dumps(last_good, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    events = changed_events(previous, payload)
    if events:
        EVENTS_DIR.mkdir(parents=True, exist_ok=True)
        dt = parse_dt(payload.get("evaluated_at_utc")) or utcnow()
        path = EVENTS_DIR / f"{dt:%Y-%m}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            for event in events:
                fh.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


def validate_payload(payload: dict[str, Any]) -> list[str]:
    errors = []
    if payload.get("schema_version") != "1.0":
        errors.append("schema_version != 1.0")
    sources = payload.get("sources")
    if not isinstance(sources, dict) or len(sources) < 8:
        errors.append("fewer than 8 registered source results")
    masters = payload.get("masters")
    required = {"market", "btc_trend", "alt_top100", "alt_final20", "trading"}
    if not isinstance(masters, dict) or not required.issubset(set(masters)):
        errors.append("five MASTER health summaries missing")
    for mid, row in (masters or {}).items():
        if "health_pct" in row or "data_availability" in row:
            errors.append(f"{mid}: ambiguous legacy MASTER health field present")
        if "registered_source_health_pct" not in row:
            errors.append(f"{mid}: registered_source_health_pct missing")
        if "scope_note" not in row:
            errors.append(f"{mid}: source-health scope note missing")
    for sid, src in (sources or {}).items():
        if src.get("status") not in STATUS_SCORE:
            errors.append(f"{sid}: invalid status {src.get('status')}")
        if not src.get("reason_codes"):
            errors.append(f"{sid}: reason_codes empty")
    return errors


def self_test() -> None:
    state = {"ok": 9, "total": 10, "failures": {}, "items": [1, 2, 3], "flag": "PASS"}
    tests = [
        ({"type": "min_ratio", "numerator": "ok", "denominator": "total", "value": 0.8}, True),
        ({"type": "empty_dict", "field": "failures"}, True),
        ({"type": "min_len", "field": "items", "value": 3}, True),
        ({"type": "equals", "field": "flag", "value": "PASS"}, True),
    ]
    for check, expected in tests:
        got, _ = eval_check(state, check)
        if got != expected:
            raise SystemExit(f"self-test failed: {check}")
    reg = load_json(REGISTRY_PATH, {})
    if len((reg or {}).get("sources") or {}) < 8:
        raise SystemExit("self-test failed: registry too small")
    print("SOURCE_HEALTH_SELF_TEST=PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["collect", "dry-run", "validate", "self-test"], default="collect")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "sovet12345688-debug/master-alt-cvd"))
    args = parser.parse_args()
    if args.mode == "self-test":
        self_test()
        return
    if args.mode == "validate":
        payload = load_json(OUTPUT_PATH, {}) or {}
        errors = validate_payload(payload)
        if errors:
            print("SOURCE_HEALTH_VALIDATION=FAIL")
            for e in errors:
                print("-", e)
            raise SystemExit(1)
        print("SOURCE_HEALTH_VALIDATION=PASS")
        return
    token = os.environ.get("GITHUB_TOKEN")
    payload = build(args.repo, token)
    errors = validate_payload(payload)
    if errors:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        raise SystemExit("source health build invalid: " + "; ".join(errors))
    if args.mode == "dry-run":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print("SOURCE_HEALTH_DRY_RUN=PASS")
        return
    write_payload(payload)
    print(json.dumps(payload.get("counts"), ensure_ascii=False))
    print("SOURCE_HEALTH_COLLECT=PASS")


if __name__ == "__main__":
    main()
