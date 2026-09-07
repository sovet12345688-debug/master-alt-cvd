from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def bmask(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s.fillna(False)
    return s.astype(str).str.lower().eq("true")


def clean(v):
    if pd.isna(v):
        return None
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            pass
    return v


def stats(df: pd.DataFrame) -> dict:
    r = pd.to_numeric(df["realized_unit_R"], errors="coerce").dropna()
    return {
        "n": int(len(df)),
        "wins": int((r > 0).sum()),
        "losses": int((r < 0).sum()),
        "sum_R": float(r.sum()),
        "mean_R": float(r.mean()) if len(r) else None,
        "win_rate": float((r > 0).mean()) if len(r) else None,
    }


def group_stats(df: pd.DataFrame, cols: str | list[str]) -> list[dict]:
    if isinstance(cols, str):
        cols = [cols]
    rows = []
    for keys, g in df.groupby(cols, dropna=False, observed=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = {c: ("N/A" if pd.isna(v) else clean(v)) for c, v in zip(cols, keys)}
        row.update(stats(g))
        rows.append(row)
    rows.sort(key=lambda r: (-r["n"], str(r)))
    return rows


def numeric_summary(df: pd.DataFrame, cols: list[str]) -> dict:
    out = {}
    for c in cols:
        if c not in df.columns:
            continue
        x = pd.to_numeric(df[c], errors="coerce").dropna()
        out[c] = {
            "n": int(len(x)),
            "mean": float(x.mean()) if len(x) else None,
            "median": float(x.median()) if len(x) else None,
            "p25": float(x.quantile(0.25)) if len(x) else None,
            "p75": float(x.quantile(0.75)) if len(x) else None,
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot-root", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--csv-output")
    args = ap.parse_args()

    root = Path(args.snapshot_root) / "r13/output/historical_diagnostic"
    legs = pd.read_csv(root / "r13_trade_legs.csv")
    ep = pd.read_csv(root / "r13_episode_metrics.csv")

    for c in ["confirm_time", "resolved_time"]:
        if c in legs.columns:
            legs[c] = pd.to_datetime(legs[c], utc=True, format="mixed", errors="coerce")
    for c in ["start_date", "first_confirm_time", "first_resolved_time"]:
        if c in ep.columns:
            ep[c] = pd.to_datetime(ep[c], utc=True, format="mixed", errors="coerce")

    # Exact population contract: mirror fast_diagnostic.py. Only legs belonging to the
    # frozen R1.3 episode table are eligible for this forensic slice.
    ids = set(ep["episode_id"].astype(str))
    first = legs[legs["leg"].eq("1H_ENTRY")].copy()
    add = legs[legs["leg"].eq("4H_ADD")].copy()
    first = first[first["episode_id"].astype(str).isin(ids)].copy()
    add = add[add["episode_id"].astype(str).isin(ids)].copy()
    if len(first) != 80 or len(add) != 64:
        raise SystemExit(f"OFFICIAL POPULATION FAILED: first={len(first)} add={len(add)} expected 80/64")

    first_small = first[[
        "episode_id", "engine", "direction", "route", "risk_state", "quality_score",
        "independent_reasons", "safety_score", "confirm_time", "resolved_time",
        "realized_unit_R", "outcome"
    ]].rename(columns={
        "engine": "first_engine", "direction": "first_direction", "route": "first_route",
        "risk_state": "first_risk_state", "quality_score": "first_quality",
        "independent_reasons": "first_reasons", "safety_score": "first_safety",
        "confirm_time": "first_confirm_time_leg", "resolved_time": "first_resolved_time_leg",
        "realized_unit_R": "first_R", "outcome": "first_outcome"
    })

    # For episode IDs that receive a 4H ADD, the frozen snapshot must map to exactly one 1H entry.
    dup_needed = first_small[first_small["episode_id"].isin(add["episode_id"])]["episode_id"].duplicated().sum()
    if int(dup_needed) != 0:
        raise SystemExit(f"FIRST ENTRY DUPLICATE WITHIN ADD POPULATION: {int(dup_needed)}")

    x = add.merge(first_small, on="episode_id", how="left", validate="one_to_one")
    if len(x) != 64:
        raise SystemExit(f"MERGE POPULATION FAILED: {len(x)} != 64")
    if int(x["first_route"].isna().sum()) or int(x["first_confirm_time_leg"].isna().sum()):
        raise SystemExit("FIRST ENTRY LINKAGE FAILED")

    first_r = pd.to_numeric(x["first_R"], errors="coerce")
    x["first_status"] = "UNRESOLVED"
    x.loc[first_r > 0, "first_status"] = "WIN"
    x.loc[first_r < 0, "first_status"] = "LOSS"
    first_status_counts = {str(k): int(v) for k, v in x["first_status"].value_counts(dropna=False).items()}

    # Reproduce the historical fast_diagnostic first_success=false slice exactly.
    x["first_success_legacy"] = first_r > 0
    legacy = x[~x["first_success_legacy"]].copy()
    if len(legacy) != 45:
        raise SystemExit(f"LEGACY NON-SUCCESS IDENTITY FAILED: {len(legacy)} != 45")
    strict_loss = legacy[legacy["first_status"].eq("LOSS")].copy()
    unresolved = legacy[legacy["first_status"].eq("UNRESOLVED")].copy()

    x["add_delay_h"] = (x["confirm_time"] - x["first_confirm_time_leg"]).dt.total_seconds() / 3600.0
    x["add_after_first_resolved"] = pd.NA
    known_resolved = x["first_resolved_time_leg"].notna()
    x.loc[known_resolved, "add_after_first_resolved"] = x.loc[known_resolved, "confirm_time"] > x.loc[known_resolved, "first_resolved_time_leg"]
    x["route_same"] = x["route"].astype(str).eq(x["first_route"].astype(str))
    x["route_changed"] = ~x["route_same"]
    x["delay_bucket"] = pd.cut(x["add_delay_h"], [-float("inf"), 24, 72, 168, float("inf")], labels=["<=24H", "24-72H", "72-168H", ">168H"])
    x["quality_bucket"] = pd.cut(pd.to_numeric(x["quality_score"], errors="coerce"), [-float("inf"), 69.999, 79.999, 89.999, float("inf")], labels=["<70", "70-79", "80-89", "90+"])
    x["reasons_bucket"] = pd.cut(pd.to_numeric(x["independent_reasons"], errors="coerce"), [-float("inf"), 2.999, 3.999, 4.999, float("inf")], labels=["<=2", "3", "4", "5+"])

    # Use the legacy45 slice for exact reconciliation with the existing reported 3/42/-33R.
    legacy = x[~x["first_success_legacy"]].copy()
    add_r = pd.to_numeric(legacy["realized_unit_R"], errors="coerce")
    winners = legacy[add_r > 0].copy()
    losers = legacy[add_r < 0].copy()
    if len(winners) != 3 or len(losers) != 42 or float(add_r.sum()) != -33.0:
        raise SystemExit(f"LEGACY OUTCOME FAILED: wins={len(winners)} losses={len(losers)} sumR={add_r.sum()}")

    # Attach episode truth only after the execution slice is frozen.
    ep_cols = ["episode_id", "start_date", "available_90d", "MFE_90d", "MAE_90d", "medium_truth_status", "confirmed_false_start_90d"]
    ep_cols = [c for c in ep_cols if c in ep.columns]
    legacy = legacy.merge(ep[ep_cols], on="episode_id", how="left", validate="one_to_one")
    if "start_date" in legacy.columns:
        legacy["episode_year"] = legacy["start_date"].dt.year
    winners = legacy[pd.to_numeric(legacy["realized_unit_R"], errors="coerce") > 0].copy()
    losers = legacy[pd.to_numeric(legacy["realized_unit_R"], errors="coerce") < 0].copy()
    strict_loss = legacy[legacy["first_status"].eq("LOSS")].copy()
    unresolved = legacy[legacy["first_status"].eq("UNRESOLVED")].copy()

    strict_r = pd.to_numeric(strict_loss["realized_unit_R"], errors="coerce")
    strict_stats = {
        "n": int(len(strict_loss)),
        "wins": int((strict_r > 0).sum()),
        "losses": int((strict_r < 0).sum()),
        "sum_R": float(strict_r.sum()),
        "mean_R": float(strict_r.mean()) if len(strict_r) else None,
    }

    complete90 = legacy[bmask(legacy["available_90d"])] if "available_90d" in legacy.columns else legacy.iloc[0:0]
    fs90 = complete90[bmask(complete90["confirmed_false_start_90d"])] if "confirmed_false_start_90d" in complete90.columns else complete90.iloc[0:0]

    categorical = {}
    for cols in [
        ["engine"], ["direction"], ["route"], ["risk_state"], ["first_status"], ["route_same"],
        ["add_after_first_resolved"], ["delay_bucket"], ["quality_bucket"], ["reasons_bucket"],
        ["engine", "route"], ["first_route", "route"], ["direction", "engine"], ["episode_year"]
    ]:
        if all(c in legacy.columns for c in cols):
            categorical["__".join(cols)] = group_stats(legacy, cols)

    case_cols = [
        "episode_id", "first_status", "first_outcome", "first_R", "engine", "direction", "first_route", "route",
        "risk_state", "quality_score", "independent_reasons", "safety_score", "add_delay_h",
        "add_after_first_resolved", "route_changed", "realized_unit_R", "outcome", "MFE_90d",
        "medium_truth_status", "confirmed_false_start_90d", "episode_year"
    ]
    case_cols = [c for c in case_cols if c in legacy.columns]

    out = {
        "status": "FAST_DIAGNOSTIC_ONLY_NOT_PROMOTION_EVIDENCE",
        "slice": "FIRST_1H_NON_SUCCESS_THEN_4H_ADD_45_OFFICIAL_POPULATION_AUDITED",
        "population_identity": {
            "official_first_1h_n": int(len(first)),
            "official_4h_add_n": int(len(add)),
            "first_status_counts_within_64_adds": first_status_counts,
            "legacy_non_success_then_add_n": int(len(legacy)),
            "strict_first_loss_then_add_n": int(len(strict_loss)),
            "unresolved_first_entry_then_add_n": int(len(unresolved)),
            "legacy_add_wins": int(len(winners)),
            "legacy_add_losses": int(len(losers)),
            "legacy_sum_R": float(pd.to_numeric(legacy["realized_unit_R"], errors="coerce").sum()),
            "legacy_mean_R": float(pd.to_numeric(legacy["realized_unit_R"], errors="coerce").mean()),
            "strict_first_loss_subset": strict_stats,
        },
        "unresolved_first_entry_cases": [{k: clean(v) for k, v in row.items()} for row in unresolved[case_cols].to_dict(orient="records")],
        "pre_add_numeric": {
            "legacy45": numeric_summary(legacy, ["quality_score", "independent_reasons", "safety_score", "add_delay_h"]),
            "winning_adds": numeric_summary(winners, ["quality_score", "independent_reasons", "safety_score", "add_delay_h"]),
            "losing_adds": numeric_summary(losers, ["quality_score", "independent_reasons", "safety_score", "add_delay_h"]),
        },
        "pre_add_categorical_performance": categorical,
        "post_first_entry_timing": {
            "known_first_resolved_n": int(legacy["first_resolved_time_leg"].notna().sum()),
            "after_first_resolved_true_n": int((legacy["add_after_first_resolved"] == True).sum()),
            "route_changed_n": int(legacy["route_changed"].sum()),
            "route_changed_and_after_resolved_n": int((legacy["route_changed"] & (legacy["add_after_first_resolved"] == True)).sum()),
        },
        "truth_only_not_add_features": {
            "complete_90d_n": int(len(complete90)),
            "confirmed_false_start_90d_n": int(len(fs90)),
            "medium_truth_status_counts": {str(k): int(v) for k, v in complete90["medium_truth_status"].value_counts(dropna=False).items()} if "medium_truth_status" in complete90.columns else {},
            "MFE_90d_legacy45": numeric_summary(complete90, ["MFE_90d"]).get("MFE_90d"),
            "MFE_90d_winning_adds": numeric_summary(winners, ["MFE_90d"]).get("MFE_90d"),
            "MFE_90d_losing_adds": numeric_summary(losers, ["MFE_90d"]).get("MFE_90d"),
        },
        "three_winning_add_cases": [{k: clean(v) for k, v in row.items()} for row in winners[case_cols].to_dict(orient="records")],
        "interpretation_rules": {
            "official_episode_filter_matches_fast_diagnostic": True,
            "confirmed_first_loss_is_available_before_4h_add": True,
            "unresolved_first_entry_must_not_be_silently_treated_as_loss": True,
            "route_change_delay_risk_quality_are_pre_add_diagnostic_features": True,
            "90d_truth_is_post_hoc_only_not_eligible_as_add_feature": True,
            "no_r13_rule_change": True,
            "same_historical_snapshot_cannot_validate_a_new_add_filter": True,
        },
    }

    text = json.dumps(out, ensure_ascii=False, indent=2, default=str)
    Path(args.output).write_text(text, encoding="utf-8")
    print(text)
    if args.csv_output:
        legacy[case_cols].to_csv(args.csv_output, index=False)


if __name__ == "__main__":
    main()
