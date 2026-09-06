from __future__ import annotations

import io
import json

import numpy as np
import pandas as pd
import requests

import run_v26_v050 as v050

core = v050.core
D = v050.D

FARSIDE_URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"
MIRROR_URL = "https://raw.githubusercontent.com/canadiancode/btc-etf-flows/main/Bitcoin-ETF-Flow-Data/data/BTC_ETF_INFLOWS_OUTFLOWS.csv"
MAX_SOURCE_STALENESS_DAYS = 10


def _mirror_fetch() -> tuple[pd.DataFrame, dict]:
    """Use a read-only GitHub snapshot whose scraper explicitly names Farside as its source.

    This is a fallback for CI source-access only. It is not treated as a second independent vendor and is never
    blended with direct Farside rows. Because the mirror is a historical snapshot, a strict freshness guard prevents
    its last observation from being carried forward into later query dates.
    """
    try:
        r = requests.get(MIRROR_URL, timeout=20, headers={"User-Agent": "BTC-FRACTAL-LAYER-C/0.5.1"})
        r.raise_for_status()
        raw = pd.read_csv(io.StringIO(r.text))
    except Exception as e:
        return pd.DataFrame(), {"status": "MIRROR_FETCH_FAILED", "url": MIRROR_URL, "error": f"{type(e).__name__}: {str(e)[:240]}"}

    if not {"Date", "Total"}.issubset(raw.columns):
        return pd.DataFrame(), {"status": "MIRROR_SCHEMA_INVALID", "url": MIRROR_URL, "columns": [str(c) for c in raw.columns]}
    dates = pd.to_datetime(raw["Date"].astype(str).str.replace("T", "", regex=False), format="%Y%m%d", utc=True, errors="coerce")
    vals = pd.to_numeric(raw["Total"], errors="coerce")
    out = pd.DataFrame({"total_flow_usd_m": vals.to_numpy()}, index=dates)
    out = out[out.index.notna() & out["total_flow_usd_m"].notna()].copy()
    out = out[~out.index.duplicated(keep="last")].sort_index()
    if len(out) < 20:
        return pd.DataFrame(), {"status": "MIRROR_TOO_SHORT", "url": MIRROR_URL, "rows": int(len(out))}

    # Locked provenance/integrity checks against public Farside historical totals. These are checks, not inserted data.
    expected = {
        pd.Timestamp("2024-01-11", tz="UTC"): 655.3,
        pd.Timestamp("2024-01-12", tz="UTC"): 203.0,
        pd.Timestamp("2024-01-16", tz="UTC"): -52.7,
        pd.Timestamp("2025-05-02", tz="UTC"): 674.9,
    }
    mismatches = []
    for d, exp in expected.items():
        if d not in out.index or not np.isclose(float(out.loc[d, "total_flow_usd_m"]), exp, atol=0.05):
            got = None if d not in out.index else float(out.loc[d, "total_flow_usd_m"])
            mismatches.append({"date": d.date().isoformat(), "expected": exp, "got": got})
    if mismatches:
        return pd.DataFrame(), {"status": "MIRROR_INTEGRITY_FAILED", "url": MIRROR_URL, "mismatches": mismatches}

    return out, {
        "status": "OK_STALE_HISTORICAL_MIRROR",
        "source": "canadiancode/btc-etf-flows GitHub CSV; scraper provenance = Farside Investors",
        "url": MIRROR_URL,
        "rows": int(len(out)),
        "range_start": out.index.min().date().isoformat(),
        "range_end": out.index.max().date().isoformat(),
        "integrity_checks": {d.date().isoformat(): exp for d, exp in expected.items()},
        "limitations": [
            "Mirror is a stale historical snapshot and cannot provide a current ETF state.",
            "Mirror contains aggregate Total only, so issuer-breadth features are unavailable.",
            "Upstream scraper omits rows whose Total parses as zero; rolling windows are therefore on reported non-zero-flow rows, not a perfect exchange calendar.",
        ],
        "freshness_guard_calendar_days": MAX_SOURCE_STALENESS_DAYS,
    }


def fetch_etf_with_fallback(cfg: dict) -> tuple[pd.DataFrame, dict]:
    direct, dm = v050.fetch_farside_etf(cfg)
    if not direct.empty:
        dm = dict(dm)
        dm["source_route"] = "DIRECT_FARSIDE"
        return direct, dm
    mirror, mm = _mirror_fetch()
    if not mirror.empty:
        mm = dict(mm)
        mm["source_route"] = "GITHUB_FARSIDE_MIRROR_FALLBACK"
        mm["direct_farside_failure"] = dm
        return mirror, mm
    return pd.DataFrame(), {"status": "ALL_SOURCES_UNAVAILABLE", "direct": dm, "mirror": mm}


def etf_state_at_v051(features: pd.DataFrame, cfg: dict, qt: pd.Timestamp) -> dict:
    view = features[features.index < qt.floor("D")].copy()
    if view.empty:
        return {"pred": None, "status": "NO_PRIOR_ETF_HISTORY"}
    latest = view.index[-1]
    age = int((qt.floor("D") - latest.floor("D")).days)
    if age > MAX_SOURCE_STALENESS_DAYS:
        return {
            "pred": None,
            "status": "ETF_SOURCE_STALE",
            "latest_etf_day": latest.date().isoformat(),
            "staleness_calendar_days": age,
            "freshness_limit_days": MAX_SOURCE_STALENESS_DAYS,
        }
    return v050.etf_state_at(features, cfg, qt)


def walk_forward_v051(etf_features: pd.DataFrame, cfg: dict, indep035: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for qt, base in indep035.iterrows():
        s = etf_state_at_v051(etf_features, cfg, qt)
        ep = s.get("pred")
        actual = base.get("actual")
        bp = base.get("pred")
        eligible = s.get("status") in {"SIGNAL", "ABSTAIN"}
        rows.append({
            "time": qt,
            "base_pred": bp,
            "actual": actual,
            "base_correct": bp == actual if bp in D and actual in D else np.nan,
            "etf_eligible": eligible,
            "etf_pred": ep,
            "etf_correct": ep == actual if ep in D and actual in D else np.nan,
            "etf_status": s.get("status"),
            "flow_state": s.get("flow_state"),
            "latest_etf_day": s.get("latest_etf_day"),
            "flow_5d": s.get("total_5d_usd_m"),
            "flow_20d": s.get("total_20d_usd_m"),
            "positive_share_20d_pct": s.get("positive_share_20d_pct"),
            "confirmation_status": ("CONFIRM" if ep in D and bp in D and ep == bp else ("CONFLICT" if ep in D and bp in D and ep != bp else "NO_SIGNAL")),
        })
    return pd.DataFrame(rows).set_index("time") if rows else pd.DataFrame()


def main_v051() -> None:
    cfg, out, data = core.load_config(), core.OUT, core.DATA
    if not (out / "episode_independent_v035.csv").exists() or not (data / "historical_features_daily.csv").exists():
        v050.v035.main_v035()

    indep035 = pd.read_csv(out / "episode_independent_v035.csv", parse_dates=["time"]).set_index("time")
    btc_features = pd.read_csv(data / "historical_features_daily.csv", parse_dates=["time"]).set_index("time")
    raw, source_meta = fetch_etf_with_fallback(cfg)

    if raw.empty:
        validation = {"layer_c_status": "SOURCE_UNAVAILABLE", "source_meta": source_meta}
        current_state = {"pred": None, "status": "NO_ETF_SOURCE"}
    else:
        raw.to_csv(data / "layer_c_etf_daily_v051.csv", index_label="day")
        etf_features = v050.build_etf_features(raw)
        etf_features.to_csv(data / "layer_c_etf_features_v051.csv", index_label="day")
        wf = walk_forward_v051(etf_features, cfg, indep035)
        wf.to_csv(out / "walk_forward_v051_layer_c.csv")
        validation = v050.validate_layer_c(wf, cfg)
        qt = btc_features.dropna(subset=["price"]).index.max()
        current_state = etf_state_at_v051(etf_features, cfg, qt)

    final = {
        "engine": "BTC_HISTORICAL_REGIME_OUTCOME_V0_5_1",
        "schema_version": "0.5.1",
        "architecture": "V035_PRICE_REGIME_CORE_PLUS_US_SPOT_BTC_ETF_LAYER_C_WITH_SOURCE_FALLBACK",
        "core_freeze": "V0.3.5 FULL_PASS core unchanged",
        "layer_b": "V0.4.2 SHADOW_ONLY_FAIL; not integrated",
        "source_meta": source_meta,
        "current_layer_c": current_state,
        "validation": validation,
        "next_step": "FINAL_PREINTEGRATION_AUDIT",
        "master_readiness": "NOT_READY_PENDING_FINAL_AUDIT_AND_EXPLICIT_USER_APPROVAL",
        "master_integration": "FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL",
        "anti_overfit": "Acceptance gates and ETF directional rules are unchanged from V0.5.0. Fallback repair changes source accessibility only; stale rows cannot be carried forward.",
    }
    (out / "v051_validation_summary.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(final, ensure_ascii=False))


if __name__ == "__main__":
    main_v051()
