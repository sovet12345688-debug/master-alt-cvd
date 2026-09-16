from __future__ import annotations

import io
import json
import math
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import run_v26_v035 as v035

v034 = v035.v034
v032 = v035.v032
v02 = v035.v02
core = v035.core
D = v035.D

BINANCE_VISION = "https://data.binance.vision/data/futures/um"
BINANCE_FAPI = "https://fapi.binance.com"
DERIV_CACHE = core.DATA / "layer_b_binance_derivatives_daily.csv"


def _zip_csv(url: str, timeout: int = 20) -> pd.DataFrame | None:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "BTC-FRACTAL-LAYER-B/0.4.0"})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not names:
                return None
            with z.open(names[0]) as f:
                return pd.read_csv(f)
    except Exception:
        return None


def _metric_day(day: pd.Timestamp) -> pd.DataFrame | None:
    ds = day.strftime("%Y-%m-%d")
    url = f"{BINANCE_VISION}/daily/metrics/BTCUSDT/BTCUSDT-metrics-{ds}.zip"
    return _zip_csv(url)


def _find_col(df: pd.DataFrame, candidates: list[str], contains: tuple[str, ...] | None = None) -> str | None:
    lookup = {str(c).lower(): str(c) for c in df.columns}
    for c in candidates:
        if c.lower() in lookup:
            return lookup[c.lower()]
    if contains:
        for c in df.columns:
            s = str(c).lower()
            if all(x in s for x in contains):
                return str(c)
    return None


def fetch_daily_metrics(start: pd.Timestamp, end: pd.Timestamp, workers: int = 20) -> tuple[pd.DataFrame, dict]:
    # Known archive family documented by Binance Data Vision. Probe one historical date first so a path change
    # yields an explicit source failure rather than thousands of useless requests.
    probe = _metric_day(pd.Timestamp("2023-01-21", tz="UTC"))
    if probe is None or probe.empty:
        return pd.DataFrame(), {"status": "SOURCE_UNAVAILABLE", "reason": "Binance Data Vision daily metrics probe failed"}

    days = list(pd.date_range(start.floor("D"), end.floor("D"), freq="D", tz="UTC"))
    frames: list[pd.DataFrame] = []
    missing = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(_metric_day, d): d for d in days}
        for fut in as_completed(futs):
            x = fut.result()
            if x is None or x.empty:
                missing += 1
                continue
            x = x.copy()
            x["_file_day"] = futs[fut]
            frames.append(x)
    if not frames:
        return pd.DataFrame(), {"status": "NO_DATA", "requested_days": len(days), "missing_days": missing}

    raw = pd.concat(frames, ignore_index=True, sort=False)
    tcol = _find_col(raw, ["create_time", "time", "timestamp"])
    oicol = _find_col(raw, ["sum_open_interest_value", "open_interest_value"], ("open", "interest", "value"))
    takercol = _find_col(raw, ["sum_taker_long_short_vol_ratio", "taker_long_short_vol_ratio"], ("taker", "ratio"))
    if tcol is None:
        raw["_time"] = pd.to_datetime(raw["_file_day"], utc=True)
    else:
        raw["_time"] = pd.to_datetime(raw[tcol], utc=True, errors="coerce")
        raw["_time"] = raw["_time"].fillna(pd.to_datetime(raw["_file_day"], utc=True))
    raw["day"] = raw["_time"].dt.floor("D")
    if oicol is not None:
        raw["oi_value"] = pd.to_numeric(raw[oicol], errors="coerce")
    else:
        raw["oi_value"] = np.nan
    if takercol is not None:
        raw["taker_ratio"] = pd.to_numeric(raw[takercol], errors="coerce")
    else:
        raw["taker_ratio"] = np.nan

    # For intraday metric files use the final OI snapshot and mean taker balance of the day.
    raw = raw.sort_values("_time")
    daily = raw.groupby("day", as_index=True).agg(
        oi_value=("oi_value", "last"),
        taker_ratio=("taker_ratio", "mean"),
        metric_rows=("_time", "size"),
    )
    meta = {
        "status": "OK",
        "requested_days": len(days),
        "available_days": int(len(daily)),
        "missing_days": int(missing),
        "oi_column": oicol,
        "taker_column": takercol,
        "warning": "Historical venue is Binance USD-M Futures; this is separate from the live MASTER Bitget OI/Funding series.",
    }
    return daily, meta


def _funding_month(month: pd.Timestamp) -> pd.DataFrame | None:
    ms = month.strftime("%Y-%m")
    url = f"{BINANCE_VISION}/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-{ms}.zip"
    return _zip_csv(url)


def fetch_monthly_funding_archive(start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    months = pd.date_range(start.floor("D").replace(day=1), end.floor("D").replace(day=1), freq="MS", tz="UTC")
    frames = []
    for m in months:
        x = _funding_month(m)
        if x is not None and not x.empty:
            frames.append(x)
    if not frames:
        return pd.DataFrame(), {"status": "ARCHIVE_UNAVAILABLE"}
    raw = pd.concat(frames, ignore_index=True, sort=False)
    tcol = _find_col(raw, ["calc_time", "funding_time", "fundingTime", "time", "timestamp"], ("time",))
    rcol = _find_col(raw, ["last_funding_rate", "funding_rate", "fundingRate"], ("funding", "rate"))
    if tcol is None or rcol is None:
        return pd.DataFrame(), {"status": "SCHEMA_UNSUPPORTED", "columns": [str(c) for c in raw.columns]}
    t = pd.to_datetime(raw[tcol], utc=True, errors="coerce")
    # Numeric epochs can fail generic parsing under some pandas versions; repair seconds/ms/us by magnitude.
    if t.notna().mean() < 0.5:
        n = pd.to_numeric(raw[tcol], errors="coerce")
        med = float(n.dropna().median()) if n.notna().any() else 0.0
        unit = "us" if med > 1e14 else ("ms" if med > 1e11 else "s")
        t = pd.to_datetime(n, unit=unit, utc=True, errors="coerce")
    z = pd.DataFrame({"time": t, "funding_rate": pd.to_numeric(raw[rcol], errors="coerce")}).dropna()
    if z.empty:
        return pd.DataFrame(), {"status": "NO_VALID_ROWS", "time_column": tcol, "rate_column": rcol}
    z["day"] = z["time"].dt.floor("D")
    daily = z.groupby("day").agg(funding_rate=("funding_rate", "mean"), funding_obs=("funding_rate", "size"))
    return daily, {"status": "OK", "days": int(len(daily)), "time_column": tcol, "rate_column": rcol, "source": "Binance Data Vision fundingRate monthly archive"}


def fetch_funding_api_fallback(start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    # Funding history has a public endpoint. This fallback is used only if the archive family is unavailable.
    rows: list[dict] = []
    cur = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    calls = 0
    try:
        while cur <= end_ms and calls < 50:
            r = requests.get(
                f"{BINANCE_FAPI}/fapi/v1/fundingRate",
                params={"symbol": "BTCUSDT", "startTime": cur, "endTime": end_ms, "limit": 1000},
                timeout=20,
                headers={"User-Agent": "BTC-FRACTAL-LAYER-B/0.4.0"},
            )
            r.raise_for_status()
            x = r.json()
            calls += 1
            if not isinstance(x, list) or not x:
                break
            rows.extend(x)
            last = max(int(a.get("fundingTime", 0)) for a in x)
            if last <= cur:
                break
            cur = last + 1
            if len(x) < 1000:
                break
            time.sleep(0.05)
    except Exception as e:
        return pd.DataFrame(), {"status": "API_FAILED", "calls": calls, "error": f"{type(e).__name__}: {str(e)[:180]}"}
    if not rows:
        return pd.DataFrame(), {"status": "NO_ROWS", "calls": calls}
    z = pd.DataFrame(rows)
    z["time"] = pd.to_datetime(pd.to_numeric(z["fundingTime"], errors="coerce"), unit="ms", utc=True, errors="coerce")
    z["funding_rate"] = pd.to_numeric(z["fundingRate"], errors="coerce")
    z = z.dropna(subset=["time", "funding_rate"])
    z["day"] = z["time"].dt.floor("D")
    daily = z.groupby("day").agg(funding_rate=("funding_rate", "mean"), funding_obs=("funding_rate", "size"))
    return daily, {"status": "OK", "days": int(len(daily)), "calls": calls, "source": "Binance USD-M public fundingRate API fallback"}


def build_layer_b_daily(features: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, dict]:
    c = cfg.get("v040", {})
    start = pd.Timestamp(c.get("history_start", "2019-09-08"), tz="UTC")
    end = features.dropna(subset=["price"]).index.max()
    metrics, mm = fetch_daily_metrics(start, end, int(c.get("download_workers", 20)))
    funding, fm = fetch_monthly_funding_archive(start, end)
    if funding.empty:
        funding, fm = fetch_funding_api_fallback(start, end)
    if metrics.empty:
        return pd.DataFrame(), {"metrics": mm, "funding": fm, "status": "INSUFFICIENT_SOURCE"}

    d = metrics.join(funding, how="outer").sort_index()
    # PIT safety: date t may only use derivatives observations from t-1 or earlier. This conservative one-day lag
    # prevents a daily close from seeing information that arrived later within the same UTC date.
    for col in ["oi_value", "taker_ratio", "funding_rate"]:
        if col in d:
            d[col] = pd.to_numeric(d[col], errors="coerce").shift(1)
    price = pd.to_numeric(features["price"], errors="coerce").reindex(d.index).ffill()
    d["price"] = price

    for n in [1, 7, 30]:
        d[f"oi_chg_{n}d"] = d["oi_value"].pct_change(n, fill_method=None) if "oi_value" in d else np.nan
    tr = pd.to_numeric(d.get("taker_ratio"), errors="coerce")
    d["taker_log_1d"] = np.log(tr.where(tr > 0))
    d["taker_log_7d"] = d["taker_log_1d"].rolling(7, min_periods=3).mean()
    d["taker_log_30d"] = d["taker_log_1d"].rolling(30, min_periods=10).mean()
    fr = pd.to_numeric(d.get("funding_rate"), errors="coerce")
    d["funding_1d"] = fr
    d["funding_7d"] = fr.rolling(7, min_periods=2).mean()
    d["funding_30d"] = fr.rolling(30, min_periods=5).mean()
    fm90 = fr.rolling(90, min_periods=15).mean(); fs90 = fr.rolling(90, min_periods=15).std()
    d["funding_z90"] = (fr - fm90) / fs90.replace(0, np.nan)

    main = features.reindex(d.index)
    r7 = pd.to_numeric(main.get("ret_7d"), errors="coerce")
    r30 = pd.to_numeric(main.get("ret_30d"), errors="coerce")
    d["price_oi_alignment_7d"] = r7 * pd.to_numeric(d["oi_chg_7d"], errors="coerce")
    d["price_oi_alignment_30d"] = r30 * pd.to_numeric(d["oi_chg_30d"], errors="coerce")

    keep = ["oi_value", "taker_ratio", "funding_rate", "oi_chg_1d", "oi_chg_7d", "oi_chg_30d", "taker_log_1d", "taker_log_7d", "taker_log_30d", "funding_1d", "funding_7d", "funding_30d", "funding_z90", "price_oi_alignment_7d", "price_oi_alignment_30d"]
    d = d[[x for x in keep if x in d.columns]].copy()
    d.to_csv(DERIV_CACHE)
    meta = {
        "status": "OK",
        "venue": "Binance USD-M Futures",
        "range_start": None if d.empty else d.index.min().date().isoformat(),
        "range_end": None if d.empty else d.index.max().date().isoformat(),
        "rows": int(len(d)),
        "metrics_source": mm,
        "funding_source": fm,
        "cvd_status": "N/A_NOT_FABRICATED",
        "pit_lag": "1 UTC day",
        "warning": "Layer B historical Binance data is a separate research venue. It must never be mixed into the live MASTER Bitget OI/Funding delta series.",
    }
    return d, meta


DERIV_FEATURES = [
    "oi_chg_1d", "oi_chg_7d", "oi_chg_30d",
    "taker_log_1d", "taker_log_7d", "taker_log_30d",
    "funding_1d", "funding_7d", "funding_30d", "funding_z90",
    "price_oi_alignment_7d", "price_oi_alignment_30d",
]


def _derivative_similarity(query: pd.Series, cand: pd.DataFrame, min_features: int) -> pd.Series:
    cols = [c for c in DERIV_FEATURES if c in cand.columns and c in query.index and np.isfinite(pd.to_numeric(pd.Series([query[c]]), errors="coerce").iloc[0])]
    if len(cols) < min_features or cand.empty:
        return pd.Series(dtype=float)
    hist = cand[cols].apply(pd.to_numeric, errors="coerce")
    med = hist.median(); iqr = hist.quantile(0.75) - hist.quantile(0.25)
    iqr = iqr.where(iqr.abs() > 1e-9, hist.std()).replace(0, np.nan)
    q = pd.to_numeric(query[cols], errors="coerce")
    diffs = (hist.sub(q, axis=1).abs()).div(iqr, axis=1)
    valid = diffs.notna().sum(axis=1)
    dist = diffs.median(axis=1, skipna=True)
    sim = 100.0 * np.exp(-0.75 * dist)
    return sim.where(valid >= min_features).dropna()


def _agg(s: pd.Series, k: int) -> float | None:
    x = pd.to_numeric(s, errors="coerce").dropna().sort_values(ascending=False).head(k)
    if x.empty:
        return None
    return float(0.5 * x.mean() + 0.3 * x.median() + 0.2 * x.iloc[0])


def derivatives_signal_at(features: pd.DataFrame, events: pd.DataFrame, deriv: pd.DataFrame, cfg: dict, qt: pd.Timestamp) -> dict:
    c = cfg.get("v040", {})
    minf = int(c.get("min_query_features", 6)); pool_k = int(c.get("price_pool_k", 40)); k = int(c.get("per_class_k", 5)); min_class = int(c.get("min_per_class", 4))
    qd = qt.floor("D")
    if qd not in deriv.index:
        return {"pred": None, "status": "NO_DERIVATIVES_DATE"}
    q = deriv.loc[qd]
    q_cov = int(pd.to_numeric(q.reindex(DERIV_FEATURES), errors="coerce").notna().sum())
    if q_cov < minf:
        return {"pred": None, "status": "QUERY_COVERAGE_LOW", "query_feature_count": q_cov}

    reps = v034._episode_representatives(features, events, cfg, qt)
    if reps.empty:
        return {"pred": None, "status": "NO_PRICE_EPISODES"}
    reps = reps.sort_values("similarity", ascending=False).head(pool_k).copy()
    dx = deriv.reindex(reps.index.floor("D"))
    dx.index = reps.index
    sim = _derivative_similarity(q, dx, minf)
    reps["deriv_similarity"] = sim.reindex(reps.index)
    reps = reps[reps["deriv_similarity"].notna()].copy()
    reps["direction"] = np.where(reps["regime"].isin(v035.BULLISH_REGIMES), D[0], D[1])
    up = reps[reps["direction"] == D[0]].sort_values("deriv_similarity", ascending=False)
    dn = reps[reps["direction"] == D[1]].sort_values("deriv_similarity", ascending=False)
    if len(up) < min_class or len(dn) < min_class:
        return {"pred": None, "status": "INSUFFICIENT_CLASS_HISTORY", "up_candidates": int(len(up)), "down_candidates": int(len(dn)), "query_feature_count": q_cov}
    kk = min(k, len(up), len(dn))
    ua = _agg(up["deriv_similarity"], kk); da = _agg(dn["deriv_similarity"], kk)
    if ua is None or da is None:
        return {"pred": None, "status": "NO_CLASS_AGGREGATE"}
    gap = ua - da
    pair_u = int((up["deriv_similarity"].head(kk).to_numpy() > dn["deriv_similarity"].head(kk).to_numpy()).sum())
    pair_d = kk - pair_u
    min_gap = float(c.get("min_score_gap", 2.0)); min_wins = int(c.get("min_pair_wins", 3))
    if abs(gap) < min_gap:
        pred = None
    elif gap > 0 and pair_u >= min_wins:
        pred = D[0]
    elif gap < 0 and pair_d >= min_wins:
        pred = D[1]
    else:
        pred = None
    support = min(1.0, len(reps) / max(float(pool_k), 1.0)); cov = min(1.0, q_cov / max(float(len(DERIV_FEATURES)), 1.0))
    strength = int(round(np.clip(45 + 35 * math.tanh(abs(gap) / 8.0) + 10 * support + 10 * cov, 0, 100))) if pred else int(round(np.clip(25 + 20 * cov + 10 * support, 0, 69)))
    return {
        "pred": pred,
        "status": "SIGNAL" if pred else "ABSTAIN",
        "query_feature_count": q_cov,
        "eligible_price_conditioned_episodes": int(len(reps)),
        "up_score": round(ua, 2), "down_score": round(da, 2), "gap_up_minus_down": round(gap, 2),
        "pair_wins": {"up": pair_u, "down": pair_d, "required": min_wins},
        "evidence_strength_score": strength,
        "warning": "Evidence-strength score is not a probability. CVD is N/A unless a real historical CVD source is added.",
    }


def walk_forward_layer_b(features: pd.DataFrame, events: pd.DataFrame, deriv: pd.DataFrame, cfg: dict, indep035: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for qt, base in indep035.iterrows():
        s = derivatives_signal_at(features, events, deriv, cfg, qt)
        dp = s.get("pred"); actual = base.get("actual"); bp = base.get("pred")
        rows.append({
            "time": qt, "base_pred": bp, "actual": actual,
            "base_correct": bp == actual if bp in D and actual in D else np.nan,
            "deriv_pred": dp,
            "deriv_correct": dp == actual if dp in D and actual in D else np.nan,
            "deriv_status": s.get("status"),
            "deriv_strength": s.get("evidence_strength_score"),
            "query_feature_count": s.get("query_feature_count"),
            "up_score": s.get("up_score"), "down_score": s.get("down_score"),
            "confirmation_status": ("CONFIRM" if dp in D and bp in D and dp == bp else ("CONFLICT" if dp in D and bp in D and dp != bp else "NO_SIGNAL")),
        })
    return pd.DataFrame(rows).set_index("time") if rows else pd.DataFrame()


def _acc(x: pd.Series) -> float | None:
    z = x.dropna()
    return None if z.empty else round(float(z.astype(bool).mean()) * 100, 2)


def validate_layer_b(wf: pd.DataFrame, cfg: dict) -> dict:
    c = cfg.get("v040", {}).get("acceptance", {})
    ev = wf[wf["actual"].isin(D)].copy()
    sig = ev[ev["deriv_pred"].isin(D)].copy()
    direct = v032.stats(sig.rename(columns={"deriv_pred": "candidate_pred"}), "candidate_pred") if not sig.empty else {"rows": 0, "coverage_pct": 0.0, "accuracy": None, "balanced_accuracy": None, "class_recalls": {}, "prediction_mix": {}}
    signal_coverage = round(len(sig) / len(ev) * 100, 2) if len(ev) else 0.0
    direct["coverage_pct_of_modern_eval"] = signal_coverage
    base_sig_acc = _acc(sig["base_correct"]) if not sig.empty else None
    confirm = sig[sig["confirmation_status"] == "CONFIRM"]
    conflict = sig[sig["confirmation_status"] == "CONFLICT"]
    confirm_acc = _acc(confirm["base_correct"]); conflict_acc = _acc(conflict["base_correct"])
    confirm_gain = None if base_sig_acc is None or confirm_acc is None else round(confirm_acc - base_sig_acc, 2)
    conflict_gap = None if base_sig_acc is None or conflict_acc is None else round(conflict_acc - base_sig_acc, 2)
    recalls = direct.get("class_recalls", {}); mix = direct.get("prediction_mix", {})
    minority = min(float(mix.get("up_pct") or 0), float(mix.get("down_pct") or 0))
    direct_gates = {
        "modern_sample_gate": len(ev) >= int(c.get("min_modern_evaluable", 12)),
        "signal_case_gate": len(sig) >= int(c.get("min_signal_cases", 10)),
        "coverage_gate": signal_coverage >= float(c.get("min_signal_coverage_pct", 40)),
        "balanced_accuracy_gate": direct.get("balanced_accuracy") is not None and direct["balanced_accuracy"] >= float(c.get("min_balanced_accuracy", 55)),
        "both_recall_gate": all(recalls.get(x) is not None and recalls[x] >= float(c.get("min_each_recall_pct", 30)) for x in D),
        "nondegenerate_gate": minority >= float(c.get("min_minority_prediction_pct", 20)),
    }
    confirm_utility = len(confirm) >= int(c.get("min_utility_cases", 6)) and confirm_gain is not None and confirm_gain >= float(c.get("min_confirm_gain_pp", 5))
    conflict_utility = len(conflict) >= int(c.get("min_utility_cases", 6)) and conflict_gap is not None and conflict_gap <= float(c.get("max_conflict_gap_pp", -5))
    utility_pass = bool(confirm_utility or conflict_utility)
    direct_pass = bool(all(direct_gates.values()))
    status = "VALIDATED_CONFIRMATION_LAYER" if direct_pass and utility_pass else ("DIRECT_SIGNAL_PASS_UTILITY_FAIL" if direct_pass else "SHADOW_ONLY_FAIL")
    return {
        "modern_evaluable_rows": int(len(ev)),
        "signal_rows": int(len(sig)),
        "direct_derivatives_signal": direct,
        "direct_gates": direct_gates,
        "direct_pass": direct_pass,
        "base_accuracy_on_signal_rows": base_sig_acc,
        "confirmation": {"count": int(len(confirm)), "base_accuracy": confirm_acc, "gain_vs_signal_baseline_pp": confirm_gain},
        "conflict": {"count": int(len(conflict)), "base_accuracy": conflict_acc, "gap_vs_signal_baseline_pp": conflict_gap},
        "confirmation_utility_pass": bool(confirm_utility),
        "conflict_utility_pass": bool(conflict_utility),
        "utility_pass": utility_pass,
        "layer_b_status": status,
        "locked_acceptance": c,
    }


def main_v040() -> None:
    v035.main_v035()
    cfg, out, data = core.load_config(), core.OUT, core.DATA
    features = pd.read_csv(data / "historical_features_daily.csv", parse_dates=["time"]).set_index("time")
    events = pd.read_csv(data / "event_registry.csv", parse_dates=["time"]).set_index("time")
    indep035 = pd.read_csv(out / "episode_independent_v035.csv", parse_dates=["time"]).set_index("time")

    deriv, source_meta = build_layer_b_daily(features, cfg)
    if deriv.empty:
        validation = {"layer_b_status": "INSUFFICIENT_HISTORY_OR_SOURCE", "source_meta": source_meta}
        current_signal = {"pred": None, "status": "NO_LAYER_B_DATA"}
        wf = pd.DataFrame()
    else:
        wf = walk_forward_layer_b(features, events, deriv, cfg, indep035)
        wf.to_csv(out / "walk_forward_v040_layer_b.csv")
        validation = validate_layer_b(wf, cfg)
        qt = features.dropna(subset=["price"]).index.max()
        current_signal = derivatives_signal_at(features, events, deriv, cfg, qt)

    current = {
        "engine": "BTC_HISTORICAL_REGIME_OUTCOME_V0_4_0",
        "schema_version": "0.4.0",
        "architecture": "V035_PRICE_REGIME_CORE_PLUS_MODERN_DERIVATIVES_LAYER_B",
        "historical_venue": "Binance USD-M Futures",
        "live_master_venue_separation": "Bitget live OI/Funding remains separate and untouched",
        "source_meta": source_meta,
        "current_layer_b": current_signal,
        "cvd": "N/A_NOT_FABRICATED",
        "master_integration": "FORBIDDEN_PENDING_FULL_SEQUENCE_AND_EXPLICIT_USER_APPROVAL",
    }
    (out / "current_v040_layer_b.json").write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")

    final = {
        "engine": "BTC_HISTORICAL_REGIME_OUTCOME_V0_4_0",
        "schema_version": "0.4.0",
        "architecture": "V035_PRICE_REGIME_CORE_PLUS_MODERN_DERIVATIVES_LAYER_B",
        "core_freeze": "V0.3.5 FULL_PASS core unchanged",
        "v037_specialists": "EXPERIMENTAL_NOT_VALIDATED; bottom narrowly missed AUC gate and top failed",
        "source_meta": source_meta,
        "validation": validation,
        "next_step": "BUILD_LAYER_C_ETF" if validation.get("layer_b_status") == "VALIDATED_CONFIRMATION_LAYER" else "KEEP_LAYER_B_SHADOW_OR_READ_ONLY_AND_BUILD_LAYER_C_ETF",
        "master_readiness": "NOT_READY_PENDING_LAYER_C_AND_EXPLICIT_APPROVAL",
        "master_integration": "FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL",
        "anti_overfit": "No gate lowering, no current-outcome fitting, no future leakage, no N/A zero-fill, no cross-venue repair.",
    }
    (out / "v040_validation_summary.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(final, ensure_ascii=False))


if __name__ == "__main__":
    main_v040()
