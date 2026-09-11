#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "market_scoring/score_engine_contract.json"
OUT = ROOT / "market_scoring/output/latest_scores.json"


def load(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must be a JSON object")
    return obj


def clip(x: float) -> float:
    return max(0.0, min(100.0, x))


def parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def age_minutes(doc: dict[str, Any], now: datetime) -> float | None:
    dt = parse_utc(doc.get("generated_at_utc"))
    if dt is None:
        return None
    return max(0.0, (now - dt).total_seconds() / 60.0)


def five_band(x: float | None, cuts: list[float], inverse: bool = False) -> float | None:
    if x is None:
        return None
    a, b, c, d = [float(v) for v in cuts]
    if x <= a:
        score = 0.0
    elif x <= b:
        score = 25.0
    elif x < c:
        score = 50.0
    elif x < d:
        score = 75.0
    else:
        score = 100.0
    return 100.0 - score if inverse else score


def transform(spec: dict[str, Any], score_name: str, component: str, value: float | None) -> float | None:
    cfg = spec["formula_spec"][score_name]["transforms"][component]
    return five_band(value, cfg["cuts"], bool(cfg.get("inverse", False)))


def metric_map(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        m.get("metric"): m
        for m in doc.get("metrics", [])
        if isinstance(m, dict) and m.get("metric")
    }


def delta_pct(metric: dict[str, Any] | None, key: str = "vs_24h") -> float | None:
    if not metric:
        return None
    block = metric.get(key)
    return None if not isinstance(block, dict) else block.get("delta_pct")


def delta_abs(metric: dict[str, Any] | None, key: str = "vs_24h") -> float | None:
    if not metric:
        return None
    block = metric.get(key)
    return None if not isinstance(block, dict) else block.get("delta")


def weighted(parts: dict[str, float | None], weights: dict[str, float]) -> dict[str, Any]:
    used = {k: float(v) for k, v in parts.items() if v is not None and k in weights}
    confirmed_weight = sum(float(weights[k]) for k in used)
    total_weight = sum(float(v) for v in weights.values())
    coverage = 0.0 if total_weight == 0 else confirmed_weight / total_weight * 100.0
    if not used or confirmed_weight == 0:
        return {
            "score": None,
            "coverage_pct": round(coverage, 1),
            "status": "N/A",
            "confidence_cap": "C",
            "threshold_alert_allowed": False,
            "components": parts,
        }
    score = sum(used[k] * float(weights[k]) for k in used) / confirmed_weight
    partial = coverage < 70.0
    return {
        "score": round(clip(score), 1),
        "coverage_pct": round(coverage, 1),
        "status": "PARTIAL" if partial else "ACTIVE_OK",
        "confidence_cap": "C" if partial else None,
        "threshold_alert_allowed": not partial,
        "components": parts,
    }


def get_asset(doc: dict[str, Any], asset: str) -> dict[str, Any]:
    for row in doc.get("assets", []):
        if row.get("asset") == asset or row.get("symbol") == asset:
            return row
    return {}


def liquidity_stage(score: float | None) -> str:
    if score is None:
        return "N/A"
    if score < 40:
        return "Risk-Off"
    if score < 55:
        return "Neutral/Weak"
    if score < 65:
        return "상승 초입 신호"
    if score < 75:
        return "상승 시작"
    if score < 85:
        return "가속화"
    return "과열"


def market_band(score: float | None) -> str:
    if score is None:
        return "N/A"
    if score < 30:
        return "매우 부정적"
    if score < 45:
        return "부정적"
    if score < 55:
        return "혼합"
    if score < 65:
        return "약한 긍정"
    if score < 80:
        return "긍정적"
    return "매우 긍정적"


def write_blocked(now: datetime, reason: str, ages: dict[str, float | None]) -> int:
    scores = {
        key: {
            "score": None,
            "coverage_pct": 0.0,
            "status": "N/A",
            "confidence_cap": "C",
            "threshold_alert_allowed": False,
            "components": {},
        }
        for key in ("liquidity_lead", "crypto_money_inflow", "alt_money_inflow", "market_positive")
    }
    out = {
        "engine": "MASTER_MARKET_SCORE_ENGINE_V1",
        "schema_version": "1.0",
        "status": "BLOCKED_STALE_INPUT",
        "generated_at_utc": now.isoformat().replace("+00:00", "Z"),
        "reason": reason,
        "source_age_minutes": ages,
        "official_persistence_eligible": False,
        "official_history_write_enabled": False,
        "watch_history_write_enabled": False,
        "scores": scores,
        "score_history_source": "state/master_market_official_history.csv",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": out["status"], "reason": reason}, ensure_ascii=False))
    return 0


def main() -> int:
    now = datetime.now(timezone.utc)
    spec = load(SPEC)
    if spec.get("status") != "ACTIVE":
        raise SystemExit("MASTER_MARKET_SCORE_ENGINE_NOT_ACTIVE")
    approval = spec.get("activation_approval") or {}
    if approval.get("explicit_user_approval") is not True:
        raise SystemExit("MASTER_MARKET_SCORE_ENGINE_APPROVAL_MISSING")

    docs = {
        "vault": load(ROOT / "market_vault/output/latest_summary.json"),
        "macro": load(ROOT / "market_vault/output/latest_macro_liquidity.json"),
        "etf": load(ROOT / "market_vault/output/latest_etf_flows.json"),
        "actors": load(ROOT / "market_vault/output/latest_actor_flows.json"),
        "derivatives": load(ROOT / "derivatives/output/latest_microstructure.json"),
    }
    ages = {name: age_minutes(doc, now) for name, doc in docs.items()}
    max_age = float((spec.get("freshness_policy") or {}).get("machine_output_max_age_minutes", 180))
    stale = [name for name, age in ages.items() if age is None or age > max_age]
    if stale:
        return write_blocked(now, "stale_or_unparseable_machine_output:" + ",".join(stale), ages)

    vault, macro, etf, actors, deriv = (
        docs["vault"], docs["macro"], docs["etf"], docs["actors"], docs["derivatives"]
    )
    vm = metric_map(vault)
    mm = metric_map(macro)
    btc_etf = get_asset(etf, "BTC")
    eth_etf = get_asset(etf, "ETH")
    btc_deriv = get_asset(deriv, "BTCUSDT")

    liq_parts = {
        "us_net_liquidity": transform(spec, "liquidity_lead", "us_net_liquidity", delta_pct(mm.get("US_NET_LIQUIDITY_PROXY"), "vs_1d")),
        "tga_change": transform(spec, "liquidity_lead", "tga_change", delta_pct(vm.get("TGA_CLOSING_BALANCE"), "vs_24h")),
        "fed_reserves": transform(spec, "liquidity_lead", "fed_reserves", delta_pct(mm.get("FED_RESERVE_BALANCES"), "vs_1d")),
        "us10y_real_yield": transform(spec, "liquidity_lead", "us10y_real_yield", delta_abs(vm.get("US10Y_REAL"), "vs_24h")),
        "dxy": transform(spec, "liquidity_lead", "dxy", delta_pct(vm.get("DXY"), "vs_24h")),
        "treasury_qra": transform(spec, "liquidity_lead", "treasury_qra", (mm.get("QRA_NET_MARKETABLE_BORROWING_CURRENT_Q") or {}).get("value")),
        "treasury_buyback": transform(spec, "liquidity_lead", "treasury_buyback", (mm.get("TREASURY_BUYBACK_ACTUAL_ACCEPTED") or {}).get("value")),
        "btc_etf_flow": transform(spec, "liquidity_lead", "btc_etf_flow", btc_etf.get("flow_1d_usd_m")),
        "stablecoin_total_flow": transform(spec, "liquidity_lead", "stablecoin_total_flow", delta_abs(vm.get("STABLECOIN_TOTAL_SUPPLY"), "vs_24h")),
    }
    liquidity = weighted(liq_parts, spec["formula_spec"]["liquidity_lead"]["weights"])

    btc_whale = (((actors.get("whale") or {}).get("BTC") or {}).get("current") or {}).get("net_usd")
    eth_whale = (((actors.get("whale") or {}).get("ETH") or {}).get("current") or {}).get("net_usd")
    crypto_parts = {
        "btc_etf_1d": transform(spec, "crypto_money_inflow", "btc_etf_1d", btc_etf.get("flow_1d_usd_m")),
        "btc_etf_3d": transform(spec, "crypto_money_inflow", "btc_etf_3d", btc_etf.get("flow_3d_usd_m")),
        "eth_etf_1d": transform(spec, "crypto_money_inflow", "eth_etf_1d", eth_etf.get("flow_1d_usd_m")),
        "stablecoin_total_1d": transform(spec, "crypto_money_inflow", "stablecoin_total_1d", delta_abs(vm.get("STABLECOIN_TOTAL_SUPPLY"), "vs_24h")),
        "stablecoin_total_7d": transform(spec, "crypto_money_inflow", "stablecoin_total_7d", delta_abs(vm.get("STABLECOIN_TOTAL_SUPPLY"), "vs_7d")),
        "btc_whale_net": transform(spec, "crypto_money_inflow", "btc_whale_net", btc_whale),
        "eth_whale_net": transform(spec, "crypto_money_inflow", "eth_whale_net", eth_whale),
        "total_market_cap_1d": transform(spec, "crypto_money_inflow", "total_market_cap_1d", delta_pct(vm.get("CRYPTO_TOTAL_MCAP"), "vs_24h")),
        "market_volume_1d": transform(spec, "crypto_money_inflow", "market_volume_1d", delta_pct(vm.get("CRYPTO_24H_VOLUME"), "vs_24h")),
    }
    crypto = weighted(crypto_parts, spec["formula_spec"]["crypto_money_inflow"]["weights"])

    alt_parts = {
        "eth_etf_1d": transform(spec, "alt_money_inflow", "eth_etf_1d", eth_etf.get("flow_1d_usd_m")),
        "eth_etf_7d": transform(spec, "alt_money_inflow", "eth_etf_7d", eth_etf.get("flow_7d_usd_m")),
        "eth_whale_net": transform(spec, "alt_money_inflow", "eth_whale_net", eth_whale),
        "eth_dominance_change": transform(spec, "alt_money_inflow", "eth_dominance_change", delta_abs(vm.get("ETH_DOMINANCE"), "vs_24h")),
        "btc_dominance_inverse_change": transform(spec, "alt_money_inflow", "btc_dominance_inverse_change", delta_abs(vm.get("BTC_DOMINANCE"), "vs_24h")),
        "total_market_cap_1d": transform(spec, "alt_money_inflow", "total_market_cap_1d", delta_pct(vm.get("CRYPTO_TOTAL_MCAP"), "vs_24h")),
        "market_volume_1d": transform(spec, "alt_money_inflow", "market_volume_1d", delta_pct(vm.get("CRYPTO_24H_VOLUME"), "vs_24h")),
        "stablecoin_total_7d": transform(spec, "alt_money_inflow", "stablecoin_total_7d", delta_abs(vm.get("STABLECOIN_TOTAL_SUPPLY"), "vs_7d")),
    }
    alt = weighted(alt_parts, spec["formula_spec"]["alt_money_inflow"]["weights"])

    cvd_score = transform(spec, "market_positive", "btc_derivatives_cvd", btc_deriv.get("cvd_notional_usdt"))
    taker_score = transform(spec, "market_positive", "btc_derivatives_taker_buy_ratio", btc_deriv.get("taker_buy_ratio"))
    deriv_values = [x for x in (cvd_score, taker_score) if x is not None]
    derivatives_state = None if not deriv_values else round(sum(deriv_values) / len(deriv_values), 1)
    oil_deltas = [delta_pct(vm.get(k), "vs_24h") for k in ("WTI", "BRENT")]
    oil_deltas = [x for x in oil_deltas if x is not None]
    oil_value = None if not oil_deltas else sum(oil_deltas) / len(oil_deltas)
    market_parts = {
        "liquidity_lead": liquidity["score"],
        "crypto_money_inflow": crypto["score"],
        "alt_money_inflow": alt["score"],
        "btc_whale_net": transform(spec, "market_positive", "btc_whale_net", btc_whale),
        "btc_derivatives_state": derivatives_state,
        "market_breadth_state": transform(spec, "market_positive", "market_breadth_state", delta_pct(vm.get("CRYPTO_TOTAL_MCAP"), "vs_24h")),
        "rates_macro_state": transform(spec, "market_positive", "rates_macro_state", delta_abs(vm.get("US10Y"), "vs_24h")),
        "oil_geopolitical_state": transform(spec, "market_positive", "oil_geopolitical_state", oil_value),
    }
    market = weighted(market_parts, spec["formula_spec"]["market_positive"]["weights"])

    scores = {
        "liquidity_lead": liquidity,
        "crypto_money_inflow": crypto,
        "alt_money_inflow": alt,
        "market_positive": market,
    }
    numeric = all(row["score"] is not None for row in scores.values())
    any_partial = any(row["status"] == "PARTIAL" for row in scores.values())
    overall_status = "BLOCKED_SCORE_NA" if not numeric else ("PARTIAL" if any_partial else "ACTIVE_OK")
    coverages = [row["coverage_pct"] for row in scores.values()]
    overall_coverage = min(coverages) if coverages else 0.0

    out = {
        "engine": spec["engine_id"],
        "schema_version": "1.0",
        "status": overall_status,
        "generated_at_utc": now.isoformat().replace("+00:00", "Z"),
        "activation_approval": spec["activation_approval"],
        "source_age_minutes": {k: (None if v is None else round(v, 1)) for k, v in ages.items()},
        "overall_coverage_pct": round(overall_coverage, 1),
        "confidence_cap": "C" if overall_coverage < 70 else None,
        "official_persistence_eligible": numeric,
        "official_history_write_enabled": False,
        "watch_history_write_enabled": False,
        "score_history_source": "state/master_market_official_history.csv",
        "scores": scores,
        "display": {
            "liquidity_stage": liquidity_stage(liquidity["score"]),
            "liquidity_thresholds": [55, 65, 75],
            "liquidity_threshold_alert_allowed": liquidity["threshold_alert_allowed"],
            "market_positive_band": market_band(market["score"]),
        },
        "anti_fallback": {
            "last_known_score_reuse": False,
            "na_as_zero": False,
            "historical_backsolve": False,
            "cross_master_inputs": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v["score"] for k, v in scores.items()}, ensure_ascii=False))
    print(f"MASTER_MARKET_SCORE_ENGINE={overall_status} coverage={overall_coverage:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
