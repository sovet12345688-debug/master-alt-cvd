#!/usr/bin/env python3
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "shared_fact_vault/registry.json"
HEALTH_PATH = ROOT / "source_health/output/latest.json"
OUTPUT_PATH = ROOT / "shared_fact_vault/output/latest.json"

ALLOWED_HEALTH = {"HEALTHY", "DEGRADED", "STALE", "FAILED", "MISSING", "UNKNOWN"}
CURRENT_USABLE = {"HEALTHY", "DEGRADED"}
FORBIDDEN_FACT_KEYS = {
    "direction", "permission", "action", "entry", "sl", "tp", "rr", "score",
    "candidate_rank", "pre_runner", "enter", "bullish", "bearish"
}


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def now_utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def health_map():
    if not HEALTH_PATH.exists():
        return {"evaluated_at_utc": None, "overall": "UNKNOWN", "sources": {}}
    raw = load_json(HEALTH_PATH)
    return {
        "evaluated_at_utc": raw.get("evaluated_at_utc"),
        "overall": raw.get("overall", "UNKNOWN"),
        "sources": raw.get("sources", {})
    }


def source_health(health, source_id):
    item = health.get("sources", {}).get(source_id, {})
    status = item.get("status", "UNKNOWN")
    if status not in ALLOWED_HEALTH:
        status = "UNKNOWN"
    return status, item.get("reason_codes", ["SOURCE_HEALTH_NA"]), item.get("state_timestamp_utc")


def fact_id(source_id, metric, asset=None, venue=None, window=None):
    parts = [source_id, metric, asset or "GLOBAL", venue or "NA", window or "NA"]
    return "::".join(str(x).replace(" ", "_") for x in parts)


def add_fact(facts, *, source_id, domain, data_path, metric, value, unit, fact_type,
             source_name, observation_time, source_status, reason_codes,
             asset=None, venue=None, window=None, source_url=None,
             source_frequency=None, neutral_context=None, lineage_extra=None):
    if value is None:
        return
    if observation_time is None:
        observation_time = "UNKNOWN"
    f = {
        "fact_id": fact_id(source_id, metric, asset, venue, window),
        "metric": metric,
        "domain": domain,
        "asset": asset,
        "venue": venue,
        "window": window,
        "value": value,
        "unit": unit,
        "fact_type": fact_type,
        "evidence_label": "CONFIRMED",
        "source_id": source_id,
        "source_name": source_name,
        "source_url": source_url,
        "source_observation_time": str(observation_time),
        "source_frequency": source_frequency,
        "source_status": source_status,
        "current_usable": source_status in CURRENT_USABLE,
        "source_health_reason_codes": reason_codes,
        "source_data_path": data_path,
        "neutral_context": neutral_context or {},
        "lineage": {
            "same_source_same_definition_only": True,
            "cross_source_reconciliation_applied": False,
            "chat_memory_used": False,
            **(lineage_extra or {})
        }
    }
    facts.append(f)


def adapt_market_metrics(source_id, cfg, data, health_item, facts):
    status, reasons, _ = health_item
    for m in data.get("metrics", []):
        add_fact(
            facts, source_id=source_id, domain=cfg["domain"], data_path=cfg["data_path"],
            metric=m.get("metric", "UNKNOWN"), value=m.get("value"), unit=m.get("unit"),
            fact_type="OBSERVED", source_name=m.get("source", "UNKNOWN"),
            observation_time=m.get("source_observation_time"), source_status=status,
            reason_codes=reasons, source_frequency=m.get("source_frequency"),
            neutral_context={
                "timestamp_quality": m.get("timestamp_quality"),
                "new_source_observation_since_prior_snapshot": m.get("new_source_observation_since_prior_snapshot"),
                "vs_prior_snapshot": m.get("vs_prior_snapshot"),
                "vs_24h": m.get("vs_24h"),
                "vs_7d": m.get("vs_7d")
            }, lineage_extra={"adapter": "market_metrics"}
        )


def adapt_macro_metrics(source_id, cfg, data, health_item, facts):
    status, reasons, _ = health_item
    for m in data.get("metrics", []):
        add_fact(
            facts, source_id=source_id, domain=cfg["domain"], data_path=cfg["data_path"],
            metric=m.get("metric", "UNKNOWN"), value=m.get("value"), unit=m.get("unit"),
            fact_type="OBSERVED", source_name=m.get("source", "UNKNOWN"),
            source_url=m.get("source_url"), observation_time=m.get("source_observation_time"),
            source_status=status, reason_codes=reasons, source_frequency=m.get("source_frequency"),
            neutral_context={"note": m.get("note"), "vs_1d": m.get("vs_1d"), "vs_3d": m.get("vs_3d"), "vs_7d": m.get("vs_7d")},
            lineage_extra={"adapter": "macro_metrics"}
        )


def adapt_etf_assets(source_id, cfg, data, health_item, facts):
    status, reasons, _ = health_item
    field_map = {
        "flow_1d_usd_m": "ETF_FLOW_1D",
        "flow_3d_usd_m": "ETF_FLOW_3D",
        "flow_5d_usd_m": "ETF_FLOW_5D",
        "flow_20d_usd_m": "ETF_FLOW_20D"
    }
    for a in data.get("assets", []):
        asset = a.get("asset")
        for field, metric in field_map.items():
            add_fact(
                facts, source_id=source_id, domain=cfg["domain"], data_path=cfg["data_path"],
                metric=metric, value=a.get(field), unit=a.get("unit"), fact_type="DERIVED_METRIC",
                source_name=a.get("source", "UNKNOWN"), source_url=a.get("source_url"),
                observation_time=a.get("latest_trading_date"), source_status=status,
                reason_codes=reasons, asset=asset, window=metric.split("_")[-1],
                source_frequency="trading_day",
                neutral_context={"window_rule": a.get("window_rule"), "source_policy": a.get("source_policy")},
                lineage_extra={"adapter": "etf_assets", "transport_mirror": a.get("mirror_url")}
            )


def adapt_derivatives_assets(source_id, cfg, data, health_item, facts):
    status, reasons, _ = health_item
    venue = data.get("venue")
    fields = {
        "mark_price": ("MARK_PRICE", "quote", "OBSERVED", None),
        "open_interest_usdt": ("OPEN_INTEREST", "USDT", "OBSERVED", None),
        "last_funding_rate": ("FUNDING_RATE", "rate", "OBSERVED", None),
        "oi_change_1h_pct": ("OI_CHANGE_PCT", "percent", "DERIVED_METRIC", "1H"),
        "oi_change_4h_pct": ("OI_CHANGE_PCT", "percent", "DERIVED_METRIC", "4H"),
        "oi_change_24h_pct": ("OI_CHANGE_PCT", "percent", "DERIVED_METRIC", "24H"),
        "price_change_1h_pct": ("PRICE_CHANGE_PCT", "percent", "DERIVED_METRIC", "1H"),
        "price_change_4h_pct": ("PRICE_CHANGE_PCT", "percent", "DERIVED_METRIC", "4H"),
        "price_change_24h_pct": ("PRICE_CHANGE_PCT", "percent", "DERIVED_METRIC", "24H"),
        "funding_change_1h": ("FUNDING_CHANGE", "rate", "DERIVED_METRIC", "1H"),
        "funding_change_4h": ("FUNDING_CHANGE", "rate", "DERIVED_METRIC", "4H"),
        "funding_change_24h": ("FUNDING_CHANGE", "rate", "DERIVED_METRIC", "24H")
    }
    for a in data.get("assets", []):
        if a.get("status") != "OK":
            continue
        asset = (a.get("symbol") or "").replace("USDT", "") or None
        obs = a.get("time_utc") or data.get("snapshot_time_utc")
        for field, (metric, unit, ftype, window) in fields.items():
            add_fact(
                facts, source_id=source_id, domain=cfg["domain"], data_path=cfg["data_path"],
                metric=metric, value=a.get(field), unit=unit, fact_type=ftype,
                source_name=f"{venue} repository collector", observation_time=obs,
                source_status=status, reason_codes=reasons, asset=asset, venue=venue, window=window,
                source_frequency="hourly_snapshot",
                neutral_context={"venue_specific": True},
                lineage_extra={"adapter": "derivatives_assets", "symbol": a.get("symbol")}
            )


def adapt_large_flow_assets(source_id, cfg, data, health_item, facts):
    status, reasons, _ = health_item
    venue = data.get("venue")
    source_name = data.get("source", "UNKNOWN")
    obs = data.get("target_completed_hour_utc")
    fields = {
        "large_cvd": ("LARGE_CVD", "normalized", "DERIVED_METRIC"),
        "retail_cvd": ("RETAIL_CVD", "normalized", "DERIVED_METRIC"),
        "large_notional_usdt": ("LARGE_NOTIONAL", "USDT", "OBSERVED"),
        "large_trade_count": ("LARGE_TRADE_COUNT", "count", "OBSERVED"),
        "coverage_pct": ("FLOW_COVERAGE", "percent", "DERIVED_METRIC")
    }
    for a in data.get("assets", []):
        if a.get("status") != "OK":
            continue
        asset = (a.get("symbol") or "").replace("USDT", "") or None
        for window in ("1h", "4h", "24h"):
            block = a.get(window)
            if not isinstance(block, dict):
                continue
            for field, (metric, unit, ftype) in fields.items():
                add_fact(
                    facts, source_id=source_id, domain=cfg["domain"], data_path=cfg["data_path"],
                    metric=metric, value=block.get(field), unit=unit, fact_type=ftype,
                    source_name=source_name, observation_time=obs, source_status=status,
                    reason_codes=reasons, asset=asset, venue=venue, window=window.upper(),
                    source_frequency="completed_hour",
                    neutral_context={"large_activity_quality": block.get("large_activity_quality"), "hours_observed": block.get("hours_observed")},
                    lineage_extra={"adapter": "large_flow_assets", "wallet_identity": False}
                )


def adapt_long_cvd_results(source_id, cfg, data, health_item, facts):
    status, reasons, _ = health_item
    venue = data.get("venue")
    obs_default = data.get("cvd_asof_utc")
    fields = {
        "large_ncvd": ("LARGE_NCVD", "normalized", "DERIVED_METRIC"),
        "retail_ncvd": ("RETAIL_NCVD", "normalized", "DERIVED_METRIC"),
        "large_notional_share_pct": ("LARGE_NOTIONAL_SHARE", "percent", "DERIVED_METRIC"),
        "large_trade_count": ("LARGE_TRADE_COUNT", "count", "OBSERVED"),
        "data_coverage_pct": ("CVD_COVERAGE", "percent", "DERIVED_METRIC")
    }
    for r in data.get("results", []):
        if r.get("supported") is not True:
            continue
        asset = r.get("ticker") or (r.get("symbol") or "").replace("USDT", "") or None
        obs = r.get("cvd_asof_utc") or obs_default
        for window, block in (r.get("windows") or {}).items():
            if not isinstance(block, dict):
                continue
            for field, (metric, unit, ftype) in fields.items():
                add_fact(
                    facts, source_id=source_id, domain=cfg["domain"], data_path=cfg["data_path"],
                    metric=metric, value=block.get(field), unit=unit, fact_type=ftype,
                    source_name="Binance Spot order-size CVD repository engine",
                    observation_time=obs, source_status=status, reason_codes=reasons,
                    asset=asset, venue=venue, window=f"{window}W", source_frequency="daily_completed_data",
                    neutral_context={"timestamp_locked": r.get("timestamp_locked"), "large_activity_pct": block.get("large_activity_pct")},
                    lineage_extra={"adapter": "long_cvd_results", "wallet_identity": False}
                )


ADAPTERS = {
    "market_metrics": adapt_market_metrics,
    "macro_metrics": adapt_macro_metrics,
    "etf_assets": adapt_etf_assets,
    "derivatives_assets": adapt_derivatives_assets,
    "large_flow_assets": adapt_large_flow_assets,
    "long_cvd_results": adapt_long_cvd_results
}


def build():
    registry = load_json(REGISTRY_PATH)
    health = health_map()
    facts = []
    gaps = []
    source_summaries = {}
    active = registry.get("active_adapters", {})

    for source_id, cfg in active.items():
        status, reasons, state_ts = source_health(health, cfg.get("source_health_id", source_id))
        path = ROOT / cfg["data_path"]
        before = len(facts)
        if not path.exists():
            gaps.append({"source_id": source_id, "reason": "DATA_FILE_MISSING", "data_path": cfg["data_path"], "source_status": status})
        else:
            try:
                data = load_json(path)
                fn = ADAPTERS.get(cfg.get("adapter"))
                if fn is None:
                    gaps.append({"source_id": source_id, "reason": "ADAPTER_UNKNOWN", "adapter": cfg.get("adapter")})
                else:
                    fn(source_id, cfg, data, (status, reasons, state_ts), facts)
            except Exception as e:
                gaps.append({"source_id": source_id, "reason": "ADAPTER_ERROR", "detail": str(e), "data_path": cfg["data_path"]})
        count = len(facts) - before
        source_summaries[source_id] = {
            "domain": cfg.get("domain"),
            "data_path": cfg.get("data_path"),
            "source_status": status,
            "source_health_reason_codes": reasons,
            "source_state_timestamp_utc": state_ts,
            "fact_count": count,
            "allowed_consumers": cfg.get("allowed_consumers", [])
        }
        if count == 0 and not any(g.get("source_id") == source_id for g in gaps):
            gaps.append({"source_id": source_id, "reason": "NO_FACTS_EXTRACTED", "data_path": cfg["data_path"]})

    for source_id, reason in registry.get("deferred_sources", {}).items():
        gaps.append({"source_id": source_id, "reason": "DEFERRED_SCHEMA_REVIEW", "detail": reason})

    seen = set()
    for f in facts:
        if f["fact_id"] in seen:
            raise ValueError(f"duplicate fact_id: {f['fact_id']}")
        seen.add(f["fact_id"])
    facts.sort(key=lambda x: x["fact_id"])

    active_count = len(active)
    populated = sum(1 for s in source_summaries.values() if s.get("fact_count", 0) > 0)
    coverage = round(100.0 * populated / active_count, 1) if active_count else None
    vault_status = "HEALTHY"
    if populated < active_count or any(s.get("source_status") not in CURRENT_USABLE for s in source_summaries.values()):
        vault_status = "DEGRADED"

    return {
        "schema_version": "1.0",
        "system": "MONEY_MASTER_OS_SHARED_FACT_VAULT",
        "generated_at_utc": now_utc(),
        "vault_status": vault_status,
        "registered_fact_source_coverage_pct": coverage,
        "policy": registry.get("policy", {}),
        "source_health_snapshot": {
            "evaluated_at_utc": health.get("evaluated_at_utc"),
            "overall": health.get("overall"),
            "path": "source_health/output/latest.json",
            "rule": "availability metadata only; not a decision source"
        },
        "sources": source_summaries,
        "facts": facts,
        "gaps": gaps
    }


def validate(payload):
    errors = []
    if payload.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    if payload.get("system") != "MONEY_MASTER_OS_SHARED_FACT_VAULT":
        errors.append("system mismatch")
    policy = payload.get("policy", {})
    required_true = [
        "facts_only", "master_decisions_forbidden", "cross_source_reconciliation_forbidden",
        "stale_or_failed_not_current", "last_good_substitution_forbidden", "missing_is_not_zero",
        "chat_memory_backfill_forbidden", "raw_high_volume_duplication_forbidden",
        "vault_coverage_is_not_master_coverage"
    ]
    for key in required_true:
        if policy.get(key) is not True:
            errors.append(f"policy missing/false: {key}")

    facts = payload.get("facts")
    if not isinstance(facts, list):
        errors.append("facts must be list")
        facts = []
    ids = set()
    for f in facts:
        required = ["fact_id", "metric", "domain", "value", "fact_type", "evidence_label", "source_id", "source_name", "source_observation_time", "source_status", "current_usable", "source_data_path", "lineage"]
        for key in required:
            if key not in f:
                errors.append(f"{f.get('fact_id','UNKNOWN')}: missing {key}")
        fid = f.get("fact_id")
        if fid in ids:
            errors.append(f"duplicate fact_id: {fid}")
        ids.add(fid)
        if f.get("evidence_label") != "CONFIRMED":
            errors.append(f"{fid}: evidence_label must be CONFIRMED")
        if f.get("source_status") not in ALLOWED_HEALTH:
            errors.append(f"{fid}: invalid source_status")
        if f.get("source_status") not in CURRENT_USABLE and f.get("current_usable") is True:
            errors.append(f"{fid}: stale/failed/missing fact cannot be current_usable")
        if f.get("lineage", {}).get("cross_source_reconciliation_applied") is not False:
            errors.append(f"{fid}: cross-source reconciliation must be false")
        if f.get("lineage", {}).get("chat_memory_used") is not False:
            errors.append(f"{fid}: chat memory lineage forbidden")
        lower_keys = {str(k).lower() for k in f.keys()}
        bad = lower_keys.intersection(FORBIDDEN_FACT_KEYS)
        if bad:
            errors.append(f"{fid}: forbidden decision keys {sorted(bad)}")
        metric = str(f.get("metric", "")).upper()
        if any(token in metric for token in ["BULLISH", "BEARISH", "ENTER", "PERMISSION", "ACTION", "SCORE"]):
            errors.append(f"{fid}: decision/score metric forbidden")

    source_counts = {}
    for f in facts:
        source_counts[f.get("source_id")] = source_counts.get(f.get("source_id"), 0) + 1
    for sid, summary in payload.get("sources", {}).items():
        if summary.get("fact_count") != source_counts.get(sid, 0):
            errors.append(f"{sid}: fact_count mismatch")

    return errors


def self_test():
    payload = build()
    errors = validate(payload)
    if not payload.get("facts"):
        errors.append("self-test extracted zero facts")
    # Guard the most important separation: upstream semantic labels must not be flattened into shared facts.
    forbidden_metrics = {"DERIVATIVES_STATE", "FLOW_STATE", "FLOW_SCORE", "STEALTH_SCORE", "NON_CHASE_GATE"}
    emitted = {f.get("metric") for f in payload.get("facts", [])}
    overlap = forbidden_metrics.intersection(emitted)
    if overlap:
        errors.append(f"semantic/master metrics leaked into vault: {sorted(overlap)}")
    return payload, errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["build", "dry-run", "validate", "self-test"], default="build")
    args = ap.parse_args()

    if args.mode == "validate":
        if not OUTPUT_PATH.exists():
            print("SHARED FACT VAULT VALIDATION: FAIL\n- latest output missing")
            return 1
        payload = load_json(OUTPUT_PATH)
        errors = validate(payload)
    elif args.mode == "self-test":
        payload, errors = self_test()
    else:
        payload = build()
        errors = validate(payload)
        if args.mode == "build" and not errors:
            OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if errors:
        print("SHARED FACT VAULT: FAIL")
        for e in errors:
            print(f"- {e}")
        return 1

    print("SHARED FACT VAULT: PASS")
    print(f"- vault_status={payload.get('vault_status')}")
    print(f"- facts={len(payload.get('facts', []))}")
    print(f"- active_sources={len(payload.get('sources', {}))}")
    print(f"- registered_fact_source_coverage_pct={payload.get('registered_fact_source_coverage_pct')}")
    if args.mode == "dry-run":
        print("- dry-run only; repository output not modified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
