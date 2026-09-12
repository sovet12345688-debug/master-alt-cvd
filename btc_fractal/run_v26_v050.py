from __future__ import annotations

import io
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import run_v26_v035 as v035

core = v035.core
v032 = v035.v032
D = v035.D
ETF_CACHE = core.DATA / "layer_c_farside_btc_etf_daily.csv"


def _flatten_col(c: object) -> str:
    if isinstance(c, tuple):
        parts = [str(x).strip() for x in c if str(x).strip() and not str(x).startswith("Unnamed")]
        return " ".join(parts).strip()
    return str(c).strip()


def _flow_num(v: object) -> float:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return np.nan
    if isinstance(v, (int, float, np.number)):
        return float(v)
    s = str(v).strip().replace("$", "").replace(",", "")
    if not s or s in {"-", "—", "–", "nan", "None"}:
        return np.nan
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1].strip()
    try:
        x = float(s)
        return -x if neg else x
    except ValueError:
        return np.nan


def fetch_farside_etf(cfg: dict) -> tuple[pd.DataFrame, dict]:
    url = cfg.get("v050", {}).get("source_url", "https://farside.co.uk/bitcoin-etf-flow-all-data/")
    try:
        r = requests.get(
            url,
            timeout=25,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; BTC-FRACTAL-LAYER-C/0.5.0; +https://github.com/)",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        r.raise_for_status()
        tables = pd.read_html(io.StringIO(r.text))
    except Exception as e:
        return pd.DataFrame(), {"status": "SOURCE_FETCH_FAILED", "url": url, "error": f"{type(e).__name__}: {str(e)[:240]}"}

    target = None
    for t in tables:
        z = t.copy()
        z.columns = [_flatten_col(c) for c in z.columns]
        lc = {c.lower(): c for c in z.columns}
        if "date" in lc and "total" in lc:
            target = z
            break
    if target is None:
        return pd.DataFrame(), {"status": "TABLE_NOT_FOUND", "url": url, "tables": int(len(tables))}

    target.columns = [_flatten_col(c) for c in target.columns]
    date_col = next(c for c in target.columns if c.lower() == "date")
    total_col = next(c for c in target.columns if c.lower() == "total")
    dt = pd.to_datetime(target[date_col], errors="coerce", dayfirst=True, utc=True)
    out = pd.DataFrame(index=dt)
    out["total_flow_usd_m"] = target[total_col].map(_flow_num).to_numpy()

    fund_cols = []
    for c in target.columns:
        if c in {date_col, total_col}:
            continue
        vals = target[c].map(_flow_num).to_numpy()
        if np.isfinite(vals).sum() == 0:
            continue
        key = c.strip().upper().replace(" ", "_")
        out[f"fund_{key}_usd_m"] = vals
        fund_cols.append(f"fund_{key}_usd_m")

    out = out[out.index.notna()].copy()
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out = out[out["total_flow_usd_m"].notna()].copy()
    if len(out) < 20:
        return pd.DataFrame(), {"status": "SOURCE_TOO_SHORT", "url": url, "rows": int(len(out))}

    # Sanity check against the known US spot-BTC ETF launch era.
    if out.index.min() < pd.Timestamp("2024-01-01", tz="UTC") or out.index.min() > pd.Timestamp("2024-02-01", tz="UTC"):
        return pd.DataFrame(), {
            "status": "DATE_RANGE_INVALID",
            "url": url,
            "range_start": out.index.min().date().isoformat(),
            "range_end": out.index.max().date().isoformat(),
        }

    out.to_csv(ETF_CACHE, index_label="day")
    return out, {
        "status": "OK",
        "source": "Farside Investors - Bitcoin ETF Flow All Data (US$m)",
        "url": url,
        "rows": int(len(out)),
        "range_start": out.index.min().date().isoformat(),
        "range_end": out.index.max().date().isoformat(),
        "fund_columns": fund_cols,
        "warning": "Third-party ETF flow table; values are used as reported and are never backfilled or synthetically repaired.",
    }


def build_etf_features(raw: pd.DataFrame) -> pd.DataFrame:
    x = raw.copy().sort_index()
    f = pd.DataFrame(index=x.index)
    total = pd.to_numeric(x["total_flow_usd_m"], errors="coerce")
    f["total_flow_usd_m"] = total
    f["flow_3d_usd_m"] = total.rolling(3, min_periods=3).sum()
    f["flow_5d_usd_m"] = total.rolling(5, min_periods=5).sum()
    f["flow_20d_usd_m"] = total.rolling(20, min_periods=20).sum()
    f["positive_share_5d"] = (total > 0).astype(float).rolling(5, min_periods=5).mean()
    f["positive_share_20d"] = (total > 0).astype(float).rolling(20, min_periods=20).mean()
    m60 = total.rolling(60, min_periods=20).mean()
    s60 = total.rolling(60, min_periods=20).std().replace(0, np.nan)
    f["flow_1d_z60"] = (total - m60) / s60
    f["history_trading_days"] = np.arange(1, len(f) + 1)

    fund_cols = [c for c in x.columns if c.startswith("fund_")]
    if fund_cols:
        funds = x[fund_cols].apply(pd.to_numeric, errors="coerce")
        pos = (funds > 0).sum(axis=1)
        neg = (funds < 0).sum(axis=1)
        avail = funds.notna().sum(axis=1).replace(0, np.nan)
        f["issuer_breadth_1d"] = (pos - neg) / avail
        f["issuer_breadth_5d"] = f["issuer_breadth_1d"].rolling(5, min_periods=3).mean()
        for ticker in ["IBIT", "FBTC", "ARKB", "GBTC", "BTC"]:
            cand = [c for c in fund_cols if c == f"fund_{ticker}_usd_m"]
            if cand:
                f[f"{ticker.lower()}_5d_usd_m"] = pd.to_numeric(x[cand[0]], errors="coerce").rolling(5, min_periods=3).sum()
    return f


def etf_state_at(features: pd.DataFrame, cfg: dict, qt: pd.Timestamp) -> dict:
    c = cfg.get("v050", {})
    min_days = int(c.get("min_trading_days_for_state", 20))
    # Strict PIT rule: ETF row dated the same day as the BTC query is excluded. Only already-reported prior dates qualify.
    view = features[features.index < qt.floor("D")].copy()
    if view.empty:
        return {"pred": None, "status": "NO_PRIOR_ETF_HISTORY"}
    row = view.iloc[-1]
    if int(row.get("history_trading_days", 0) or 0) < min_days or pd.isna(row.get("flow_20d_usd_m")):
        return {"pred": None, "status": "ETF_HISTORY_TOO_SHORT", "latest_etf_day": view.index[-1].date().isoformat()}

    f5 = float(row["flow_5d_usd_m"])
    f20 = float(row["flow_20d_usd_m"])
    ps20 = float(row["positive_share_20d"])
    up_ps = float(c.get("up_positive_share_20d", 0.55))
    dn_ps = float(c.get("down_positive_share_20d", 0.45))
    if f5 > 0 and f20 > 0 and ps20 >= up_ps:
        pred = D[0]
        state = "INFLOW_CONFIRMATION"
    elif f5 < 0 and f20 < 0 and ps20 <= dn_ps:
        pred = D[1]
        state = "OUTFLOW_CONFIRMATION"
    else:
        pred = None
        state = "MIXED_ABSTAIN"

    z = row.get("flow_1d_z60")
    breadth = row.get("issuer_breadth_5d")
    consistency = abs(ps20 - 0.5) * 2.0
    magnitude = 0.0 if pd.isna(z) else min(1.0, abs(float(z)) / 2.0)
    strength = int(round(np.clip(35 + 35 * consistency + 20 * magnitude + (10 if pred is not None else 0), 0, 100)))
    return {
        "pred": pred,
        "status": "SIGNAL" if pred is not None else "ABSTAIN",
        "flow_state": state,
        "latest_etf_day": view.index[-1].date().isoformat(),
        "total_1d_usd_m": round(float(row["total_flow_usd_m"]), 1),
        "total_5d_usd_m": round(f5, 1),
        "total_20d_usd_m": round(f20, 1),
        "positive_share_20d_pct": round(ps20 * 100.0, 1),
        "issuer_breadth_5d": None if pd.isna(breadth) else round(float(breadth), 3),
        "evidence_strength_score": strength,
        "warning": "ETF evidence-strength is not a probability and cannot by itself change the frozen price-regime direction.",
    }


def walk_forward_layer_c(etf_features: pd.DataFrame, cfg: dict, indep035: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for qt, base in indep035.iterrows():
        s = etf_state_at(etf_features, cfg, qt)
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


def _acc(x: pd.Series) -> float | None:
    z = x.dropna()
    return None if z.empty else round(float(z.astype(bool).mean()) * 100.0, 2)


def validate_layer_c(wf: pd.DataFrame, cfg: dict) -> dict:
    c = cfg.get("v050", {}).get("acceptance", {})
    ev = wf[wf["actual"].isin(D)].copy()
    eligible = ev[ev["etf_eligible"] == True].copy()  # noqa: E712
    sig = eligible[eligible["etf_pred"].isin(D)].copy()
    direct = v032.stats(sig.rename(columns={"etf_pred": "candidate_pred"}), "candidate_pred") if not sig.empty else {
        "rows": 0, "coverage_pct": 0.0, "accuracy": None, "balanced_accuracy": None, "class_recalls": {}, "prediction_mix": {},
    }
    signal_coverage = round(len(sig) / len(eligible) * 100.0, 2) if len(eligible) else 0.0
    direct["coverage_pct_of_etf_eligible"] = signal_coverage

    base_sig_acc = _acc(sig["base_correct"]) if not sig.empty else None
    confirm = sig[sig["confirmation_status"] == "CONFIRM"]
    conflict = sig[sig["confirmation_status"] == "CONFLICT"]
    confirm_acc = _acc(confirm["base_correct"])
    conflict_acc = _acc(conflict["base_correct"])
    confirm_gain = None if base_sig_acc is None or confirm_acc is None else round(confirm_acc - base_sig_acc, 2)
    conflict_gap = None if base_sig_acc is None or conflict_acc is None else round(conflict_acc - base_sig_acc, 2)
    recalls = direct.get("class_recalls", {})
    mix = direct.get("prediction_mix", {})
    minority = min(float(mix.get("up_pct") or 0), float(mix.get("down_pct") or 0))

    gates = {
        "independent_history_gate": len(eligible) >= int(c.get("min_independent_evaluable", 10)),
        "signal_case_gate": len(sig) >= int(c.get("min_signal_cases", 6)),
        "coverage_gate": signal_coverage >= float(c.get("min_signal_coverage_pct", 40)),
        "balanced_accuracy_gate": direct.get("balanced_accuracy") is not None and direct["balanced_accuracy"] >= float(c.get("min_balanced_accuracy", 55)),
        "both_recall_gate": all(recalls.get(x) is not None and recalls[x] >= float(c.get("min_each_recall_pct", 30)) for x in D),
        "nondegenerate_gate": minority >= float(c.get("min_minority_prediction_pct", 20)),
    }
    confirm_utility = len(confirm) >= int(c.get("min_utility_cases", 4)) and confirm_gain is not None and confirm_gain >= float(c.get("min_confirm_gain_pp", 5))
    conflict_utility = len(conflict) >= int(c.get("min_utility_cases", 4)) and conflict_gap is not None and conflict_gap <= float(c.get("max_conflict_gap_pp", -5))
    utility_pass = bool(confirm_utility or conflict_utility)
    direct_pass = bool(all(gates.values()))

    if not gates["independent_history_gate"]:
        status = "CONTEXT_ONLY_INSUFFICIENT_HISTORY"
    elif direct_pass and utility_pass:
        status = "VALIDATED_CONFIRMATION_LAYER"
    elif direct_pass:
        status = "DIRECT_SIGNAL_PASS_UTILITY_FAIL"
    else:
        status = "SHADOW_ONLY_FAIL"

    return {
        "all_independent_evaluable_rows": int(len(ev)),
        "etf_era_feature_eligible_rows": int(len(eligible)),
        "signal_rows": int(len(sig)),
        "eligible_status_counts": {str(k): int(v) for k, v in eligible["etf_status"].value_counts(dropna=False).to_dict().items()},
        "direct_etf_signal": direct,
        "acceptance_gates": gates,
        "direct_pass": direct_pass,
        "base_accuracy_on_signal_rows": base_sig_acc,
        "confirmation": {"count": int(len(confirm)), "base_accuracy": confirm_acc, "gain_vs_signal_baseline_pp": confirm_gain},
        "conflict": {"count": int(len(conflict)), "base_accuracy": conflict_acc, "gap_vs_signal_baseline_pp": conflict_gap},
        "confirmation_utility_pass": bool(confirm_utility),
        "conflict_utility_pass": bool(conflict_utility),
        "utility_pass": utility_pass,
        "layer_c_status": status,
        "locked_acceptance": c,
        "interpretation_guard": "ETF-era history is deliberately not promoted to a strong standalone probability until the independent-history gate passes.",
    }


def main_v050() -> None:
    cfg, out, data = core.load_config(), core.OUT, core.DATA
    # Standalone fallback for local/manual runs. In CI the prior V0.4.2 step already builds these files.
    if not (out / "episode_independent_v035.csv").exists() or not (data / "historical_features_daily.csv").exists():
        v035.main_v035()

    indep035 = pd.read_csv(out / "episode_independent_v035.csv", parse_dates=["time"]).set_index("time")
    btc_features = pd.read_csv(data / "historical_features_daily.csv", parse_dates=["time"]).set_index("time")
    raw, source_meta = fetch_farside_etf(cfg)

    if raw.empty:
        validation = {"layer_c_status": "SOURCE_UNAVAILABLE", "source_meta": source_meta}
        current_state = {"pred": None, "status": "NO_ETF_SOURCE"}
        wf = pd.DataFrame()
    else:
        etf_features = build_etf_features(raw)
        etf_features.to_csv(data / "layer_c_farside_btc_etf_features.csv", index_label="day")
        wf = walk_forward_layer_c(etf_features, cfg, indep035)
        wf.to_csv(out / "walk_forward_v050_layer_c.csv")
        validation = validate_layer_c(wf, cfg)
        qt = btc_features.dropna(subset=["price"]).index.max()
        current_state = etf_state_at(etf_features, cfg, qt)

    current = {
        "engine": "BTC_HISTORICAL_REGIME_OUTCOME_V0_5_0",
        "schema_version": "0.5.0",
        "architecture": "V035_PRICE_REGIME_CORE_PLUS_US_SPOT_BTC_ETF_LAYER_C",
        "query_date": btc_features.dropna(subset=["price"]).index.max().date().isoformat(),
        "source_meta": source_meta,
        "current_layer_c": current_state,
        "master_integration": "FORBIDDEN_PENDING_FULL_SEQUENCE_AND_EXPLICIT_USER_APPROVAL",
    }
    (out / "current_v050_layer_c.json").write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")

    final = {
        "engine": "BTC_HISTORICAL_REGIME_OUTCOME_V0_5_0",
        "schema_version": "0.5.0",
        "architecture": "V035_PRICE_REGIME_CORE_PLUS_US_SPOT_BTC_ETF_LAYER_C",
        "core_freeze": "V0.3.5 FULL_PASS core unchanged",
        "layer_b": "V0.4.2 SHADOW_ONLY_FAIL; not integrated",
        "source_meta": source_meta,
        "validation": validation,
        "next_step": "FINAL_PREINTEGRATION_AUDIT" if validation.get("layer_c_status") != "SOURCE_UNAVAILABLE" else "REPAIR_ETF_SOURCE_WITHOUT_SYNTHETIC_DATA",
        "master_readiness": "NOT_READY_PENDING_FINAL_AUDIT_AND_EXPLICIT_USER_APPROVAL",
        "master_integration": "FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL",
        "anti_overfit": "No gate lowering, no outcome-fitted ETF thresholds, no same-day flow leakage, no N/A zero-fill, no synthetic flow history.",
    }
    (out / "v050_validation_summary.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(final, ensure_ascii=False))


if __name__ == "__main__":
    main_v050()
