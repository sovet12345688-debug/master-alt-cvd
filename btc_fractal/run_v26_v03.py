from __future__ import annotations

import json

import numpy as np
import pandas as pd

import run_v26_fast as v02

core = v02.core


def standalone_domain_cfg(cfg: dict, domain: str) -> dict:
    x = json.loads(json.dumps(cfg))
    if domain == "onchain":
        x["feature_group_weights"] = {
            "price_state": 0.0,
            "price_path": 0.0,
            "onchain_state": 0.55,
            "onchain_path": 0.45,
            "macro": 0.0,
        }
    elif domain == "macro":
        x["feature_group_weights"] = {
            "price_state": 0.0,
            "price_path": 0.0,
            "onchain_state": 0.0,
            "onchain_path": 0.0,
            "macro": 1.0,
        }
    else:
        raise ValueError(domain)
    return x


def domain_signal_usable(prediction: dict) -> bool:
    conf = prediction.get("confidence", {})
    return bool(
        prediction.get("pred") in ("UP30_FIRST", "DN20_FIRST")
        and conf.get("grade") in ("MEDIUM", "HIGH")
        and int(conf.get("directional_case_count") or 0) >= 8
        and float(conf.get("majority_share_pct") or 0.0) >= 62.0
    )


def confirmation_at(
    features: pd.DataFrame,
    events: pd.DataFrame,
    cfg: dict,
    query_time: pd.Timestamp,
    base_prediction: str | None = None,
) -> dict:
    pframe = v02.price_only_frame(features)
    pcfg = v02.price_only_cfg(cfg)

    if base_prediction is None:
        base = v02.predict_at(pframe, events, pcfg, query_time)
        base_prediction = base.get("pred")
    else:
        base = v02.predict_at(pframe, events, pcfg, query_time)

    domains: dict[str, dict] = {}
    agree: list[str] = []
    conflict: list[str] = []

    for domain in ("onchain", "macro"):
        pred = v02.predict_at(features, events, standalone_domain_cfg(cfg, domain), query_time)
        usable = domain_signal_usable(pred)
        signal = pred.get("pred")

        if usable and base_prediction in ("UP30_FIRST", "DN20_FIRST"):
            if signal == base_prediction:
                agree.append(domain)
            else:
                conflict.append(domain)

        domains[domain] = {
            "pred": signal,
            "usable": usable,
            "up_case_share": pred.get("up_case_share"),
            "down_case_share": pred.get("down_case_share"),
            "confidence": pred.get("confidence"),
        }

    if base_prediction not in ("UP30_FIRST", "DN20_FIRST"):
        status = "BASE_NO_SIGNAL"
    elif agree and conflict:
        status = "MIXED_CONFLICT"
    elif conflict:
        status = "CONFLICT"
    elif len(agree) >= 2:
        status = "CONFIRMED_BOTH"
    elif len(agree) == 1:
        status = "CONFIRMED_ONE"
    else:
        status = "NO_CONFIRMATION"

    return {
        "query_date": query_time.date().isoformat(),
        "base_price_prediction": base_prediction,
        "base_price_case_share": {
            "up_case_share": base.get("up_case_share"),
            "down_case_share": base.get("down_case_share"),
        },
        "base_price_confidence": base.get("confidence"),
        "confirmation_status": status,
        "agreeing_domains": agree,
        "conflicting_domains": conflict,
        "domains": domains,
        "rule": (
            "Price-only determines direction. On-chain and macro may confirm or conflict, "
            "but cannot change the base direction."
        ),
    }


def confirmation_walk_forward(
    features: pd.DataFrame,
    events: pd.DataFrame,
    cfg: dict,
    price_wf: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict] = []
    if price_wf.empty:
        return pd.DataFrame()

    for qt, base_row in price_wf.iterrows():
        base_pred = base_row.get("pred")
        actual = base_row.get("actual")
        try:
            c = confirmation_at(features, events, cfg, qt, base_prediction=base_pred)
        except Exception:
            continue

        rows.append(
            {
                "time": qt,
                "base_pred": base_pred,
                "actual": actual,
                "base_correct": (
                    base_pred == actual
                    if base_pred in ("UP30_FIRST", "DN20_FIRST")
                    and actual in ("UP30_FIRST", "DN20_FIRST")
                    else np.nan
                ),
                "confirmation_status": c["confirmation_status"],
                "onchain_pred": c["domains"]["onchain"]["pred"],
                "onchain_usable": c["domains"]["onchain"]["usable"],
                "onchain_confidence": c["domains"]["onchain"]["confidence"].get("score"),
                "macro_pred": c["domains"]["macro"]["pred"],
                "macro_usable": c["domains"]["macro"]["usable"],
                "macro_confidence": c["domains"]["macro"]["confidence"].get("score"),
            }
        )

    return pd.DataFrame(rows).set_index("time") if rows else pd.DataFrame()


def _subset_stats(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {"count": 0, "accuracy": None}
    ev = frame[frame["base_correct"].notna()]
    return {
        "count": int(len(ev)),
        "accuracy": round(float(ev["base_correct"].mean()) * 100, 2) if len(ev) else None,
    }


def summarize_confirmation_wf(cwf: pd.DataFrame) -> dict:
    if cwf.empty:
        return {"rows": 0}

    ev = cwf[cwf["base_correct"].notna()]
    baseline_accuracy = round(float(ev["base_correct"].mean()) * 100, 2) if len(ev) else None

    confirmed = ev[ev["confirmation_status"].isin(["CONFIRMED_BOTH", "CONFIRMED_ONE"])]
    both = ev[ev["confirmation_status"] == "CONFIRMED_BOTH"]
    conflict = ev[ev["confirmation_status"].isin(["CONFLICT", "MIXED_CONFLICT"])]
    no_conf = ev[ev["confirmation_status"] == "NO_CONFIRMATION"]

    return {
        "rows": int(len(cwf)),
        "evaluable": int(len(ev)),
        "baseline_price_core_accuracy": baseline_accuracy,
        "confirmed": _subset_stats(confirmed),
        "confirmed_both": _subset_stats(both),
        "conflict": _subset_stats(conflict),
        "no_confirmation": _subset_stats(no_conf),
        "confirmed_coverage_pct": round(len(confirmed) / len(ev) * 100, 2) if len(ev) else None,
        "conflict_coverage_pct": round(len(conflict) / len(ev) * 100, 2) if len(ev) else None,
    }


def confirmation_acceptance(summary: dict) -> dict:
    baseline = summary.get("baseline_price_core_accuracy")
    confirmed = summary.get("confirmed", {})
    both = summary.get("confirmed_both", {})
    conflict = summary.get("conflict", {})

    confirm_count = int(confirmed.get("count") or 0)
    confirm_acc = confirmed.get("accuracy")
    both_count = int(both.get("count") or 0)
    both_acc = both.get("accuracy")
    conflict_count = int(conflict.get("count") or 0)
    conflict_acc = conflict.get("accuracy")

    confirmation_gain = (
        None if baseline is None or confirm_acc is None
        else round(float(confirm_acc) - float(baseline), 2)
    )
    both_gain = (
        None if baseline is None or both_acc is None
        else round(float(both_acc) - float(baseline), 2)
    )
    conflict_gap = (
        None if baseline is None or conflict_acc is None
        else round(float(conflict_acc) - float(baseline), 2)
    )

    positive_confirmation_gate = bool(
        confirm_count >= 20
        and confirmation_gain is not None
        and confirmation_gain >= 3.0
    )
    conflict_gate = bool(
        conflict_count >= 10
        and conflict_gap is not None
        and conflict_gap <= -3.0
    )
    strong_confirmation_gate = bool(
        both_count >= 10
        and both_gain is not None
        and both_gain >= 6.0
    )

    utility_pass = bool(
        positive_confirmation_gate
        and (conflict_gate or strong_confirmation_gate)
    )

    return {
        "utility_pass": utility_pass,
        "confirmed_count": confirm_count,
        "confirmed_accuracy": confirm_acc,
        "confirmed_accuracy_gain_pp": confirmation_gain,
        "confirmed_both_count": both_count,
        "confirmed_both_accuracy": both_acc,
        "confirmed_both_gain_pp": both_gain,
        "conflict_count": conflict_count,
        "conflict_accuracy": conflict_acc,
        "conflict_accuracy_gap_pp": conflict_gap,
        "positive_confirmation_gate": positive_confirmation_gate,
        "conflict_gate": conflict_gate,
        "strong_confirmation_gate": strong_confirmation_gate,
        "rule": (
            "Confirmation-only layer passes if confirmed cases >=20 and beat the price-core baseline by >=3pp, "
            "plus either conflict cases >=10 underperform baseline by >=3pp or both-domain confirmations >=10 "
            "beat baseline by >=6pp."
        ),
    }


def main_v03() -> None:
    cfg = core.load_config()
    core.OUT.mkdir(parents=True, exist_ok=True)
    core.DATA.mkdir(parents=True, exist_ok=True)
    now = pd.Timestamp.now(tz="UTC").floor("D")

    cm, cm_fail = core.cm_fetch_asset_metrics(
        cfg["community_asset_metrics"],
        cfg["start_date"],
        now.date().isoformat(),
    )
    macro, macro_fail = core.treasury_history(
        pd.Timestamp(cfg["start_date"]).year,
        now.year,
    )

    raw = cm.join(macro, how="left") if not macro.empty else cm
    raw.to_csv(core.DATA / "historical_raw_daily.csv")

    features = v02.build_features_v02(raw, cfg)
    features.to_csv(core.DATA / "historical_features_daily.csv")

    events = v02.build_event_registry_fast(features["price"].dropna(), cfg)
    events.to_csv(core.DATA / "event_registry.csv")

    query = features.dropna(subset=["price"]).index.max()
    pframe = v02.price_only_frame(features)
    pcfg = v02.price_only_cfg(cfg)

    price_current = v02.run_current_v02(pframe, events, pcfg)
    confirmation_current = confirmation_at(features, events, cfg, query)

    (core.OUT / "current_price_core.json").write_text(
        json.dumps(price_current, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (core.OUT / "current_confirmation.json").write_text(
        json.dumps(confirmation_current, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    price_wf = v02.walk_forward_v02(pframe, events, pcfg, step_days=30)
    price_wf.to_csv(core.OUT / "walk_forward_price_core.csv")

    cwf = confirmation_walk_forward(features, events, cfg, price_wf)
    cwf.to_csv(core.OUT / "walk_forward_confirmation.csv")

    summary = summarize_confirmation_wf(cwf)
    acceptance = confirmation_acceptance(summary)

    (core.OUT / "confirmation_walk_forward_summary.json").write_text(
        json.dumps(
            {"summary": summary, "acceptance": acceptance},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    derived_cols = [
        "CapRealDerivedUSD",
        "MVRVZ_DERIVED_PIT",
        "RealizedPriceDerivedUSD",
        "dist_to_realized_price",
    ]
    meta = {
        "generated_at_utc": core.datetime.now(core.UTC).isoformat(),
        "schema_version": cfg["schema_version"],
        "architecture": "PRICE_CORE_WITH_ONCHAIN_MACRO_CONFIRMATION",
        "rows_raw": len(raw),
        "rows_features": len(features),
        "rows_events": len(events),
        "coinmetrics_failures": cm_fail,
        "treasury_failures_count": len(macro_fail),
        "available_columns": list(raw.columns),
        "derived_metric_non_null_counts": {
            c: int(features[c].notna().sum())
            for c in derived_cols
            if c in features.columns
        },
    }
    (core.OUT / "build_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    decision = {
        "schema_version": cfg["schema_version"],
        "architecture": "PRICE_CORE_WITH_ONCHAIN_MACRO_CONFIRMATION",
        "current_price_core": {
            "direction_case_share": price_current.get("direction_case_share"),
            "confidence": price_current.get("confidence"),
        },
        "current_confirmation": confirmation_current,
        "walk_forward": summary,
        "acceptance": acceptance,
        "next_step": (
            "BUILD_MODERN_DERIVATIVES_AND_ETF_AS_CONFIRMATION_LAYERS"
            if acceptance["utility_pass"]
            else "KEEP_NON_PRICE_DOMAINS_INFORMATIONAL_ONLY_AND_DO_NOT_BUILD_NEXT_LAYERS_YET"
        ),
        "master_integration": "FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL",
    }
    (core.OUT / "model_decision.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main_v03()
