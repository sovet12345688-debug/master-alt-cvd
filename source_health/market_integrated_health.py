#!/usr/bin/env python3
"""MASTER MARKET integrated operational health board.

This is a diagnostic layer inside MONEY MASTER OS Source Health. It does not create
market facts, scores, directions, permissions, entries, stops or targets.

Four user-facing operational states only:
- CURRENT OK
- HISTORY RECOVERING
- STALE
- FAILED
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE_HEALTH_PATH = ROOT / "source_health/output/latest.json"
JSON_OUT = ROOT / "source_health/output/master_market_integrated_health.json"
MD_OUT = ROOT / "source_health/output/master_market_integrated_health.md"

DATA_VAULT_STATE = ROOT / "market_vault/state/vault_state.json"
STABLECOIN_OUT = ROOT / "market_vault/output/latest_stablecoin_windows.json"
DERIV_STATE = ROOT / "derivatives/state/collector_state.json"
ACTOR_OUT = ROOT / "market_vault/output/latest_actor_flows.json"
YEN_OUT = ROOT / "market_yen_carry/output/latest_yen_carry.json"

STATUSES = ("CURRENT OK", "HISTORY RECOVERING", "STALE", "FAILED")
RANK = {name: i for i, name in enumerate(STATUSES)}
ICONS = {
    "CURRENT OK": "🟢",
    "HISTORY RECOVERING": "🟡",
    "STALE": "🟠",
    "FAILED": "🔴",
}


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


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def age_minutes(now: datetime, value: Any) -> float | None:
    dt = parse_dt(value)
    if dt is None:
        return None
    return max(0.0, (now - dt).total_seconds() / 60.0)


def component(
    component_id: str,
    display_name: str,
    status: str,
    *,
    timestamp_utc: str | None,
    age_min: float | None,
    history_status: str | None,
    reasons: list[str],
    next_action: str,
    source_path: str,
    auto_recovery_expected: bool,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in RANK:
        raise ValueError(f"invalid integrated health status: {status}")
    return {
        "component_id": component_id,
        "display_name": display_name,
        "status": status,
        "icon": ICONS[status],
        "timestamp_utc": timestamp_utc,
        "age_minutes": round(age_min, 1) if age_min is not None else None,
        "history_status": history_status,
        "reason_codes": reasons or ["OK"],
        "next_action": next_action,
        "auto_recovery_expected": auto_recovery_expected,
        "source_path": source_path,
        "details": details or {},
    }


def evaluate_data_vault(now: datetime) -> dict[str, Any]:
    data = load_json(DATA_VAULT_STATE)
    path = "market_vault/state/vault_state.json"
    if data is None:
        return component("data_vault", "Data Vault", "FAILED", timestamp_utc=None, age_min=None, history_status=None, reasons=["STATE_MISSING_OR_INVALID"], next_action="Data Vault workflow/state 생성 여부 확인 후 수동복구", source_path=path, auto_recovery_expected=False)

    ts = data.get("last_run_utc")
    age = age_minutes(now, ts)
    if age is None:
        return component("data_vault", "Data Vault", "FAILED", timestamp_utc=None, age_min=None, history_status=data.get("history_status"), reasons=["TIMESTAMP_MISSING"], next_action="collector timestamp 기록 로직 확인", source_path=path, auto_recovery_expected=False)
    if age > 150:
        return component("data_vault", "Data Vault", "STALE", timestamp_utc=iso(parse_dt(ts)), age_min=age, history_status=data.get("history_status"), reasons=["CURRENT_SNAPSHOT_STALE"], next_action="다음 자동수집 확인; 지속 시 Data Vault workflow 최신 실패 step 점검", source_path=path, auto_recovery_expected=True)

    failures = data.get("failures")
    coverage = data.get("mandatory_coverage_pct")
    current_ok = data.get("current_collection_status") == "OK" and isinstance(coverage, (int, float)) and coverage >= 80 and isinstance(failures, dict) and not failures
    if not current_ok:
        return component("data_vault", "Data Vault", "FAILED", timestamp_utc=iso(parse_dt(ts)), age_min=age, history_status=data.get("history_status"), reasons=["CURRENT_COLLECTION_FAILED"], next_action="mandatory coverage/failures 확인 후 collector 또는 원천 복구", source_path=path, auto_recovery_expected=True, details={"mandatory_coverage_pct": coverage, "failures": failures})

    hstatus = str(data.get("history_status") or "UNKNOWN")
    issues = list(data.get("history_issues") or [])
    if hstatus != "OK" or bool(data.get("history_recovery_mode")):
        return component("data_vault", "Data Vault", "HISTORY RECOVERING", timestamp_utc=iso(parse_dt(ts)), age_min=age, history_status=hstatus, reasons=["CURRENT_OK_HISTORY_INCOMPLETE"], next_action="현재값은 사용; 실제 동일원천 history 자동 축적 대기, backfill 금지", source_path=path, auto_recovery_expected=True, details={"history_issues": issues, "recent_timestamp_count_26h": data.get("history_recent_timestamp_count_26h"), "history_24h_comparison_count": data.get("history_24h_comparison_count")})

    return component("data_vault", "Data Vault", "CURRENT OK", timestamp_utc=iso(parse_dt(ts)), age_min=age, history_status="OK", reasons=["OK"], next_action="조치 없음", source_path=path, auto_recovery_expected=True)


def evaluate_stablecoin(now: datetime) -> dict[str, Any]:
    data = load_json(STABLECOIN_OUT)
    path = "market_vault/output/latest_stablecoin_windows.json"
    if data is None:
        return component("stablecoin_windows", "Stablecoin Windows", "FAILED", timestamp_utc=None, age_min=None, history_status=None, reasons=["OUTPUT_MISSING_OR_INVALID"], next_action="Data Vault/output adapter 실행 여부 확인", source_path=path, auto_recovery_expected=True)

    ts = data.get("generated_at_utc")
    age = age_minutes(now, ts)
    if age is None:
        return component("stablecoin_windows", "Stablecoin Windows", "FAILED", timestamp_utc=None, age_min=None, history_status=None, reasons=["TIMESTAMP_MISSING"], next_action="adapter timestamp 생성 로직 확인", source_path=path, auto_recovery_expected=False)
    if age > 150:
        return component("stablecoin_windows", "Stablecoin Windows", "STALE", timestamp_utc=iso(parse_dt(ts)), age_min=age, history_status=None, reasons=["OUTPUT_STALE"], next_action="Data Vault/output adapter 다음 자동수집 확인", source_path=path, auto_recovery_expected=True)

    metrics = data.get("metrics") or []
    failures = data.get("failures") or []
    if not isinstance(metrics, list) or not metrics or failures or any(not isinstance(m, dict) or m.get("status") != "OK" or m.get("current_value") is None for m in metrics):
        return component("stablecoin_windows", "Stablecoin Windows", "FAILED", timestamp_utc=iso(parse_dt(ts)), age_min=age, history_status=None, reasons=["CURRENT_METRIC_FAILED"], next_action="Stablecoin current metric/failures 확인 후 adapter 또는 원천 복구", source_path=path, auto_recovery_expected=True, details={"failures": failures})

    missing: list[str] = []
    for m in metrics:
        name = str(m.get("metric") or "UNKNOWN")
        for key, label in (("vs_1d", "1D"), ("vs_3d", "3D"), ("vs_5d", "5D"), ("vs_7d", "7D"), ("vs_20d", "20D")):
            if m.get(key) is None:
                missing.append(f"{name}:{label}")
    if missing:
        return component("stablecoin_windows", "Stablecoin Windows", "HISTORY RECOVERING", timestamp_utc=iso(parse_dt(ts)), age_min=age, history_status="PARTIAL_WINDOWS", reasons=["CURRENT_OK_COMPARISON_HISTORY_INCOMPLETE"], next_action="현재 공급량은 사용; 누락 비교창은 실제 history 축적 대기", source_path=path, auto_recovery_expected=True, details={"missing_windows": missing})

    return component("stablecoin_windows", "Stablecoin Windows", "CURRENT OK", timestamp_utc=iso(parse_dt(ts)), age_min=age, history_status="OK", reasons=["OK"], next_action="조치 없음", source_path=path, auto_recovery_expected=True)


def evaluate_derivatives(now: datetime) -> dict[str, Any]:
    data = load_json(DERIV_STATE)
    path = "derivatives/state/collector_state.json"
    if data is None:
        return component("derivatives", "Derivatives", "FAILED", timestamp_utc=None, age_min=None, history_status=None, reasons=["STATE_MISSING_OR_INVALID"], next_action="Derivatives workflow/state 확인 후 수동복구", source_path=path, auto_recovery_expected=False)

    ts = data.get("last_run_utc")
    age = age_minutes(now, ts)
    if age is None:
        return component("derivatives", "Derivatives", "FAILED", timestamp_utc=None, age_min=None, history_status=data.get("history_status"), reasons=["TIMESTAMP_MISSING"], next_action="collector timestamp 기록 로직 확인", source_path=path, auto_recovery_expected=False)
    if age > 150:
        return component("derivatives", "Derivatives", "STALE", timestamp_utc=iso(parse_dt(ts)), age_min=age, history_status=data.get("history_status"), reasons=["CURRENT_SNAPSHOT_STALE"], next_action="다음 자동수집 확인; 지속 시 Derivatives workflow 점검", source_path=path, auto_recovery_expected=True)

    universe = data.get("universe_count")
    ok_count = data.get("ok_count")
    ratio_ok = isinstance(universe, int) and universe > 0 and isinstance(ok_count, int) and ok_count / universe >= 0.8
    if data.get("current_collection_status") != "OK" or not ratio_ok:
        return component("derivatives", "Derivatives", "FAILED", timestamp_utc=iso(parse_dt(ts)), age_min=age, history_status=data.get("history_status"), reasons=["CURRENT_COLLECTION_FAILED"], next_action="OI/Funding/Microstructure CURRENT_QA 실패 원인 확인", source_path=path, auto_recovery_expected=True, details={"universe_count": universe, "ok_count": ok_count, "na_count": data.get("na_count")})

    missing = data.get("missing_exact_windows") or {}
    history_recovering = str(data.get("history_status") or "") != "OK" or bool(data.get("history_recovery_mode")) or any(isinstance(v, (int, float)) and v > 0 for v in missing.values())
    if history_recovering:
        return component("derivatives", "Derivatives", "HISTORY RECOVERING", timestamp_utc=iso(parse_dt(ts)), age_min=age, history_status=str(data.get("history_status") or "RECOVERING"), reasons=["CURRENT_OK_EXACT_WINDOWS_INCOMPLETE"], next_action="현재 파생값은 사용; exact 1H/4H/24H history 자동 축적 대기", source_path=path, auto_recovery_expected=True, details={"missing_exact_windows": missing, "universe_count": universe, "ok_count": ok_count})

    return component("derivatives", "Derivatives", "CURRENT OK", timestamp_utc=iso(parse_dt(ts)), age_min=age, history_status="OK", reasons=["OK"], next_action="조치 없음", source_path=path, auto_recovery_expected=True)


def evaluate_actor(now: datetime) -> dict[str, Any]:
    data = load_json(ACTOR_OUT)
    path = "market_vault/output/latest_actor_flows.json"
    if data is None:
        return component("actor_retail", "Actor Retail", "FAILED", timestamp_utc=None, age_min=None, history_status=None, reasons=["OUTPUT_MISSING_OR_INVALID"], next_action="Actor workflow/output adapter 확인", source_path=path, auto_recovery_expected=True)

    ts = data.get("generated_at_utc")
    age = age_minutes(now, ts)
    if age is None:
        return component("actor_retail", "Actor Retail", "FAILED", timestamp_utc=None, age_min=None, history_status=None, reasons=["TIMESTAMP_MISSING"], next_action="Actor output timestamp 생성 로직 확인", source_path=path, auto_recovery_expected=False)
    if age > 120:
        return component("actor_retail", "Actor Retail", "STALE", timestamp_utc=iso(parse_dt(ts)), age_min=age, history_status=None, reasons=["ADAPTER_OUTPUT_STALE"], next_action="Actor/Data Vault workflow 다음 자동수집 확인", source_path=path, auto_recovery_expected=True)

    overall = str(data.get("retail_proxy_status") or "")
    retail = data.get("retail_proxy") or {}
    current_statuses = [str((retail.get(c) or {}).get("current_status") or "") for c in ("BTC", "ETH")]
    funding_statuses = [str((retail.get(c) or {}).get("funding_status") or "") for c in ("BTC", "ETH")]
    if overall == "STALE_UPSTREAM" or "STALE_UPSTREAM" in current_statuses or "STALE_UPSTREAM" in funding_statuses:
        return component("actor_retail", "Actor Retail", "STALE", timestamp_utc=iso(parse_dt(ts)), age_min=age, history_status=None, reasons=["STALE_UPSTREAM"], next_action="Derivatives upstream freshness 확인; 다음 Actor 자동수집에서 재검증", source_path=path, auto_recovery_expected=True)
    if overall != "OK" or any(s != "OK" for s in current_statuses):
        return component("actor_retail", "Actor Retail", "FAILED", timestamp_utc=iso(parse_dt(ts)), age_min=age, history_status=None, reasons=["CURRENT_RETAIL_PROXY_FAILED"], next_action="Actor retail current_status/upstream 누락 확인", source_path=path, auto_recovery_expected=True, details={"retail_proxy_status": overall, "current_statuses": current_statuses, "funding_statuses": funding_statuses, "warnings": data.get("warnings") or []})

    missing: list[str] = []
    for coin in ("BTC", "ETH"):
        row = retail.get(coin) or {}
        for key, label in (("1d", "1D"), ("3d", "3D"), ("7d", "7D")):
            if row.get(key) is None:
                missing.append(f"{coin}:{label}")
    if missing:
        return component("actor_retail", "Actor Retail", "HISTORY RECOVERING", timestamp_utc=iso(parse_dt(ts)), age_min=age, history_status="PARTIAL_WINDOWS", reasons=["CURRENT_OK_COMPARISON_HISTORY_INCOMPLETE"], next_action="현재 Retail proxy는 사용; 누락 1D/3D/7D는 동일원천 history 축적 대기", source_path=path, auto_recovery_expected=True, details={"missing_windows": missing, "retail_proxy_status": overall})

    return component("actor_retail", "Actor Retail", "CURRENT OK", timestamp_utc=iso(parse_dt(ts)), age_min=age, history_status="OK", reasons=["OK"], next_action="조치 없음", source_path=path, auto_recovery_expected=True)


def evaluate_yen(now: datetime) -> dict[str, Any]:
    data = load_json(YEN_OUT)
    path = "market_yen_carry/output/latest_yen_carry.json"
    if data is None:
        return component("yen_carry", "Yen Carry", "FAILED", timestamp_utc=None, age_min=None, history_status=None, reasons=["OUTPUT_MISSING_OR_INVALID"], next_action="Yen Carry daily workflow/output 확인", source_path=path, auto_recovery_expected=True)

    ts = data.get("generated_at_utc")
    age = age_minutes(now, ts)
    if age is None:
        return component("yen_carry", "Yen Carry", "FAILED", timestamp_utc=None, age_min=None, history_status=None, reasons=["TIMESTAMP_MISSING"], next_action="Yen Carry output timestamp 생성 로직 확인", source_path=path, auto_recovery_expected=False)
    if age > 4320:
        return component("yen_carry", "Yen Carry", "STALE", timestamp_utc=iso(parse_dt(ts)), age_min=age, history_status=None, reasons=["DAILY_OUTPUT_STALE"], next_action="08:35 KST daily workflow 실행 여부와 원천 publication 확인", source_path=path, auto_recovery_expected=True)

    source_stale = []
    for name, src in (data.get("sources") or {}).items():
        if not isinstance(src, dict) or src.get("status") == "error":
            continue
        a = src.get("source_age_days")
        m = src.get("freshness_max_age_days")
        if isinstance(a, int) and isinstance(m, int) and a > m:
            source_stale.append(name)
    errors = data.get("errors") or []
    if source_stale or any("stale" in str(e).lower() for e in errors):
        return component("yen_carry", "Yen Carry", "STALE", timestamp_utc=iso(parse_dt(ts)), age_min=age, history_status=None, reasons=["UPSTREAM_SOURCE_STALE"], next_action="stale 원천의 다음 공식 publication 후 재수집", source_path=path, auto_recovery_expected=True, details={"stale_sources": source_stale, "errors": errors})
    if data.get("status") != "ok" or data.get("coverage_pct") != 100 or errors:
        return component("yen_carry", "Yen Carry", "FAILED", timestamp_utc=iso(parse_dt(ts)), age_min=age, history_status=None, reasons=["CURRENT_COLLECTION_PARTIAL_OR_FAILED"], next_action="실패 source/parser 확인 후 다음 daily 또는 수동복구", source_path=path, auto_recovery_expected=True, details={"status": data.get("status"), "coverage_pct": data.get("coverage_pct"), "errors": errors})

    return component("yen_carry", "Yen Carry", "CURRENT OK", timestamp_utc=iso(parse_dt(ts)), age_min=age, history_status="OK", reasons=["OK"], next_action="조치 없음", source_path=path, auto_recovery_expected=True, details={"coverage_pct": data.get("coverage_pct"), "source_freshness_days": data.get("source_freshness_days")})


def map_source_health_status(src: dict[str, Any]) -> str:
    raw = str(src.get("status") or "UNKNOWN")
    if raw == "HEALTHY":
        return "CURRENT OK"
    if raw in ("DEGRADED", "STALE"):
        return "STALE"
    return "FAILED"


def related_market_alerts(source_health: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not source_health:
        return [{"source_id": "central_source_health", "display_name": "Central Source Health", "impact": "CORE", "status": "FAILED", "reason_codes": ["SOURCE_HEALTH_LATEST_MISSING"]}]
    excluded = {"market_data_vault", "derivatives"}
    alerts = []
    for sid, src in (source_health.get("sources") or {}).items():
        if sid in excluded or not isinstance(src, dict):
            continue
        impact = (src.get("impact") or {}).get("market")
        if not impact:
            continue
        mapped = map_source_health_status(src)
        if mapped == "CURRENT OK":
            continue
        alerts.append({
            "source_id": sid,
            "display_name": src.get("display_name", sid),
            "impact": impact,
            "status": mapped,
            "raw_source_health_status": src.get("status"),
            "reason_codes": src.get("reason_codes") or [],
            "state_age_minutes": src.get("state_age_minutes"),
            "workflow_file": src.get("workflow_file"),
        })
    return alerts


def worst_status(statuses: list[str]) -> str:
    valid = [s for s in statuses if s in RANK]
    return max(valid, key=lambda s: RANK[s]) if valid else "FAILED"


def build() -> dict[str, Any]:
    now = utcnow()
    source_health = load_json(SOURCE_HEALTH_PATH)
    components = [
        evaluate_data_vault(now),
        evaluate_stablecoin(now),
        evaluate_derivatives(now),
        evaluate_actor(now),
        evaluate_yen(now),
    ]
    alerts = related_market_alerts(source_health)
    # CORE related-source alerts affect the board's operational overall state.
    # OPTIONAL/CONTEXT alerts remain visible but do not change the overall badge.
    overall_inputs = [c["status"] for c in components]
    overall_inputs.extend(a["status"] for a in alerts if a.get("impact") == "CORE")
    overall = worst_status(overall_inputs)
    counts = Counter(c["status"] for c in components)
    return {
        "schema_version": "1.0",
        "system": "MASTER_MARKET_INTEGRATED_HEALTH",
        "evaluated_at_utc": iso(now),
        "overall_status": overall,
        "overall_icon": ICONS[overall],
        "status_order": list(STATUSES),
        "counts": {s: counts.get(s, 0) for s in STATUSES},
        "components": components,
        "registered_market_alerts": alerts,
        "policy": {
            "operational_health_only": True,
            "never_changes_master_score_or_direction": True,
            "current_failure_precedence": "FAILED > STALE > HISTORY RECOVERING > CURRENT OK",
            "history_rule": "Missing comparison history never backfills/interpolates current data; current values may remain usable while status is HISTORY RECOVERING.",
            "core_alert_rule": "Related Source Health CORE alerts affect overall_status; OPTIONAL/CONTEXT alerts remain visible only.",
        },
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# MASTER MARKET 통합 건강판",
        "",
        f"- 평가시각(UTC): `{payload.get('evaluated_at_utc')}`",
        f"- 종합상태: {payload.get('overall_icon')} **{payload.get('overall_status')}**",
        "- 상태 기준: 🟢 CURRENT OK / 🟡 HISTORY RECOVERING / 🟠 STALE / 🔴 FAILED",
        "- 이 건강판은 운영 진단용이며 MASTER MARKET 점수·롱/숏·WATCH 판단을 변경하지 않습니다.",
        "",
        "| 영역 | 상태 | 데이터 나이(분) | History | 핵심 원인 | 다음 조치 |",
        "|---|---|---:|---|---|---|",
    ]
    for c in payload.get("components") or []:
        reasons = ", ".join(c.get("reason_codes") or [])
        history = c.get("history_status") or "-"
        age = c.get("age_minutes")
        age_text = "-" if age is None else f"{age:.1f}"
        lines.append(f"| {c.get('display_name')} | {c.get('icon')} {c.get('status')} | {age_text} | {history} | {reasons} | {c.get('next_action')} |")

    alerts = payload.get("registered_market_alerts") or []
    lines += ["", "## 연관 MARKET Source Health 경보"]
    if not alerts:
        lines.append("현재 별도 경보 없음.")
    else:
        lines += [
            "",
            "| Source | 영향도 | 상태 | 원인 | Workflow |",
            "|---|---|---|---|---|",
        ]
        for a in alerts:
            icon = ICONS.get(a.get("status"), "⚪")
            reasons = ", ".join(a.get("reason_codes") or [])
            lines.append(f"| {a.get('display_name')} | {a.get('impact')} | {icon} {a.get('status')} | {reasons} | {a.get('workflow_file') or '-'} |")
    lines.append("")
    return "\n".join(lines)


def validate(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != "1.0":
        errors.append("schema_version != 1.0")
    if payload.get("system") != "MASTER_MARKET_INTEGRATED_HEALTH":
        errors.append("wrong system")
    if payload.get("overall_status") not in RANK:
        errors.append("invalid overall_status")
    components = payload.get("components")
    required = {"data_vault", "stablecoin_windows", "derivatives", "actor_retail", "yen_carry"}
    if not isinstance(components, list):
        errors.append("components missing")
    else:
        got = {c.get("component_id") for c in components if isinstance(c, dict)}
        if got != required:
            errors.append(f"component set mismatch: {sorted(got)}")
        for c in components:
            if not isinstance(c, dict):
                errors.append("invalid component row")
                continue
            if c.get("status") not in RANK:
                errors.append(f"{c.get('component_id')}: invalid status")
            if not c.get("reason_codes"):
                errors.append(f"{c.get('component_id')}: empty reason_codes")
            if not c.get("next_action"):
                errors.append(f"{c.get('component_id')}: next_action missing")
    if payload.get("status_order") != list(STATUSES):
        errors.append("status_order mismatch")
    return errors


def self_test() -> None:
    assert worst_status(["CURRENT OK"]) == "CURRENT OK"
    assert worst_status(["CURRENT OK", "HISTORY RECOVERING"]) == "HISTORY RECOVERING"
    assert worst_status(["HISTORY RECOVERING", "STALE"]) == "STALE"
    assert worst_status(["STALE", "FAILED"]) == "FAILED"
    assert map_source_health_status({"status": "HEALTHY"}) == "CURRENT OK"
    assert map_source_health_status({"status": "DEGRADED"}) == "STALE"
    assert map_source_health_status({"status": "MISSING"}) == "FAILED"
    print("MASTER_MARKET_INTEGRATED_HEALTH_SELF_TEST=PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["build", "validate", "self-test"], default="build")
    args = parser.parse_args()
    if args.mode == "self-test":
        self_test()
        return
    if args.mode == "validate":
        payload = load_json(JSON_OUT) or {}
        errors = validate(payload)
        if errors:
            print("MASTER_MARKET_INTEGRATED_HEALTH_VALIDATION=FAIL")
            for e in errors:
                print("-", e)
            raise SystemExit(1)
        if not MD_OUT.exists() or "MASTER MARKET 통합 건강판" not in MD_OUT.read_text(encoding="utf-8"):
            raise SystemExit("MASTER MARKET integrated health markdown missing")
        print("MASTER_MARKET_INTEGRATED_HEALTH_VALIDATION=PASS")
        return

    payload = build()
    errors = validate(payload)
    if errors:
        raise SystemExit("integrated health build invalid: " + "; ".join(errors))
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MD_OUT.write_text(render_markdown(payload), encoding="utf-8")
    print(json.dumps({"overall_status": payload["overall_status"], "counts": payload["counts"]}, ensure_ascii=False))
    print("MASTER_MARKET_INTEGRATED_HEALTH_BUILD=PASS")


if __name__ == "__main__":
    main()
