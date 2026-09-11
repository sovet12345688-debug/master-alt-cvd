#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "market_scoring/score_engine_candidate_v1.json"
OUT = ROOT / "market_scoring/output/latest_shadow_scores.json"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def clip(x: float) -> float:
    return max(0.0, min(100.0, x))


def five_band(x: float | None, cuts: tuple[float, float, float, float], inverse: bool = False) -> float | None:
    if x is None:
        return None
    a, b, c, d = cuts
    if x <= a: s = 0.0
    elif x <= b: s = 25.0
    elif x < c: s = 50.0
    elif x < d: s = 75.0
    else: s = 100.0
    return 100.0 - s if inverse else s


def metric_map(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m.get("metric"): m for m in doc.get("metrics", []) if isinstance(m, dict) and m.get("metric")}


def delta_pct(m: dict[str, Any] | None, key: str = "vs_24h") -> float | None:
    if not m:
        return None
    x = m.get(key)
    return None if not isinstance(x, dict) else x.get("delta_pct")


def delta_abs(m: dict[str, Any] | None, key: str = "vs_24h") -> float | None:
    if not m:
        return None
    x = m.get(key)
    return None if not isinstance(x, dict) else x.get("delta")


def weighted(parts: dict[str, float | None], weights: dict[str, float]) -> dict[str, Any]:
    used = {k: float(v) for k, v in parts.items() if v is not None and k in weights}
    confirmed_weight = sum(weights[k] for k in used)
    total_weight = sum(weights.values())
    coverage = 0.0 if total_weight == 0 else confirmed_weight / total_weight * 100.0
    if not used or confirmed_weight == 0:
        return {"score": None, "coverage_pct": round(coverage, 1), "status": "N/A", "components": parts}
    score = sum(used[k] * weights[k] for k in used) / confirmed_weight
    status = "SHADOW_OK" if coverage >= 70 else "PARTIAL_SHADOW"
    return {"score": round(clip(score), 1), "coverage_pct": round(coverage, 1), "status": status, "components": parts}


def get_asset(doc: dict[str, Any], asset: str) -> dict[str, Any]:
    for row in doc.get("assets", []):
        if row.get("asset") == asset or row.get("symbol") == asset:
            return row
    return {}


def main() -> int:
    spec = load(SPEC)
    vault = load(ROOT / "market_vault/output/latest_summary.json")
    macro = load(ROOT / "market_vault/output/latest_macro_liquidity.json")
    etf = load(ROOT / "market_vault/output/latest_etf_flows.json")
    actors = load(ROOT / "market_vault/output/latest_actor_flows.json")
    deriv = load(ROOT / "derivatives/output/latest_microstructure.json")

    vm = metric_map(vault)
    mm = metric_map(macro)
    btc_etf = get_asset(etf, "BTC")
    eth_etf = get_asset(etf, "ETH")
    btc_deriv = get_asset(deriv, "BTCUSDT")

    # Candidate cutoffs are intentionally explicit and SHADOW-only.
    liq_parts = {
        "us_net_liquidity": five_band(delta_pct(mm.get("US_NET_LIQUIDITY_PROXY"), "vs_1d"), (-2.0, -0.5, 0.5, 2.0)),
        "tga_change": five_band(delta_pct(vm.get("TGA_CLOSING_BALANCE"), "vs_24h"), (-5.0, -1.0, 1.0, 5.0), inverse=True),
        "fed_reserves": five_band(delta_pct(mm.get("FED_RESERVE_BALANCES"), "vs_1d"), (-2.0, -0.5, 0.5, 2.0)),
        "us10y_real_yield": five_band(delta_abs(vm.get("US10Y_REAL"), "vs_24h"), (-0.15, -0.05, 0.05, 0.15), inverse=True),
        "dxy": five_band(delta_pct(vm.get("DXY"), "vs_24h"), (-1.0, -0.3, 0.3, 1.0), inverse=True),
        "treasury_qra": five_band((mm.get("QRA_NET_MARKETABLE_BORROWING_CURRENT_Q") or {}).get("value"), (400.0, 600.0, 800.0, 1000.0), inverse=True),
        "treasury_buyback": five_band((mm.get("TREASURY_BUYBACK_ACTUAL_ACCEPTED") or {}).get("value"), (1e9, 5e9, 10e9, 15e9)),
        "btc_etf_flow": five_band(btc_etf.get("flow_1d_usd_m"), (-300.0, -100.0, 100.0, 300.0)),
        "stablecoin_total_flow": five_band(delta_abs(vm.get("STABLECOIN_TOTAL_SUPPLY"), "vs_24h"), (-1e9, -2.5e8, 2.5e8, 1e9)),
    }
    liquidity = weighted(liq_parts, spec["formula_spec"]["liquidity_lead"]["weights"])

    btc_whale = (((actors.get("whale") or {}).get("BTC") or {}).get("current") or {}).get("net_usd")
    eth_whale = (((actors.get("whale") or {}).get("ETH") or {}).get("current") or {}).get("net_usd")
    crypto_parts = {
        "btc_etf_1d": five_band(btc_etf.get("flow_1d_usd_m"), (-300.0, -100.0, 100.0, 300.0)),
        "btc_etf_3d": five_band(btc_etf.get("flow_3d_usd_m"), (-600.0, -200.0, 200.0, 600.0)),
        "eth_etf_1d": five_band(eth_etf.get("flow_1d_usd_m"), (-150.0, -50.0, 50.0, 150.0)),
        "stablecoin_total_1d": five_band(delta_abs(vm.get("STABLECOIN_TOTAL_SUPPLY"), "vs_24h"), (-1e9, -2.5e8, 2.5e8, 1e9)),
        "stablecoin_total_7d": five_band(delta_abs(vm.get("STABLECOIN_TOTAL_SUPPLY"), "vs_7d"), (-3e9, -1e9, 1e9, 3e9)),
        "btc_whale_net": five_band(btc_whale, (-3e8, -1e8, 1e8, 3e8)),
        "eth_whale_net": five_band(eth_whale, (-2e8, -7.5e7, 7.5e7, 2e8)),
        "total_market_cap_1d": five_band(delta_pct(vm.get("CRYPTO_TOTAL_MCAP"), "vs_24h"), (-3.0, -1.0, 1.0, 3.0)),
        "market_volume_1d": five_band(delta_pct(vm.get("CRYPTO_24H_VOLUME"), "vs_24h"), (-15.0, -5.0, 5.0, 15.0)),
    }
    crypto = weighted(crypto_parts, spec["formula_spec"]["crypto_money_inflow"]["weights"])

    alt_parts = {
        "eth_etf_1d": five_band(eth_etf.get("flow_1d_usd_m"), (-150.0, -50.0, 50.0, 150.0)),
        "eth_etf_7d": five_band(eth_etf.get("flow_7d_usd_m"), (-500.0, -150.0, 150.0, 500.0)),
        "eth_whale_net": five_band(eth_whale, (-2e8, -7.5e7, 7.5e7, 2e8)),
        "eth_dominance_change": five_band(delta_abs(vm.get("ETH_DOMINANCE"), "vs_24h"), (-0.5, -0.1, 0.1, 0.5)),
        "btc_dominance_inverse_change": five_band(delta_abs(vm.get("BTC_DOMINANCE"), "vs_24h"), (-0.5, -0.1, 0.1, 0.5), inverse=True),
        "total_market_cap_1d": five_band(delta_pct(vm.get("CRYPTO_TOTAL_MCAP"), "vs_24h"), (-3.0, -1.0, 1.0, 3.0)),
        "market_volume_1d": five_band(delta_pct(vm.get("CRYPTO_24H_VOLUME"), "vs_24h"), (-15.0, -5.0, 5.0, 15.0)),
        "stablecoin_total_7d": five_band(delta_abs(vm.get("STABLECOIN_TOTAL_SUPPLY"), "vs_7d"), (-3e9, -1e9, 1e9, 3e9)),
    }
    alt = weighted(alt_parts, spec["formula_spec"]["alt_money_inflow"]["weights"])

    cvd = btc_deriv.get("cvd_notional_usdt")
    taker = btc_deriv.get("taker_buy_ratio")
    cvd_s = five_band(cvd, (-25e6, -5e6, 5e6, 25e6))
    taker_s = five_band(taker, (0.40, 0.47, 0.53, 0.60))
    deriv_score = None if cvd_s is None and taker_s is None else round(sum(x for x in (cvd_s, taker_s) if x is not None) / len([x for x in (cvd_s, taker_s) if x is not None]), 1)
    breadth_score = five_band(delta_pct(vm.get("CRYPTO_TOTAL_MCAP"), "vs_24h"), (-3.0, -1.0, 1.0, 3.0))
    rates_score = five_band(delta_abs(vm.get("US10Y"), "vs_24h"), (-0.15, -0.05, 0.05, 0.15), inverse=True)
    oil_deltas = [delta_pct(vm.get(k), "vs_24h") for k in ("WTI", "BRENT")]
    oil_deltas = [x for x in oil_deltas if x is not None]
    oil_score = None if not oil_deltas else five_band(sum(oil_deltas)/len(oil_deltas), (-5.0, -1.0, 1.0, 5.0), inverse=True)
    market_parts = {
        "liquidity_lead": liquidity["score"],
        "crypto_money_inflow": crypto["score"],
        "alt_money_inflow": alt["score"],
        "btc_whale_net": five_band(btc_whale, (-3e8, -1e8, 1e8, 3e8)),
        "btc_derivatives_state": deriv_score,
        "market_breadth_state": breadth_score,
        "rates_macro_state": rates_score,
        "oil_geopolitical_state": oil_score,
    }
    market = weighted(market_parts, spec["formula_spec"]["market_positive"]["weights"])

    out = {
        "engine": spec["engine_id"],
        "schema_version": "1.0",
        "status": "SHADOW_ONLY",
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "official_write_enabled": False,
        "scores": {
            "liquidity_lead": liquidity,
            "crypto_money_inflow": crypto,
            "alt_money_inflow": alt,
            "market_positive": market,
        },
        "activation_gate": spec["activation_gate"],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k:v["score"] for k,v in out["scores"].items()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
