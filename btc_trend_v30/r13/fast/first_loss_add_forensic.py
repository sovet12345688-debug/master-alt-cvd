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

    first = legs[legs["leg"].eq("1H_ENTRY")].copy()
    add = legs[legs["leg"].eq("4H_ADD")].copy()

    first_cols = [
        "episode_id", "engine", "direction", "route", "risk_state", "quality_score",
        "independent_reasons", "safety_score", "confirm_time", "resolved_time",
        "realized_unit_R", "outcome"
    ]
    first_small = first[first_cols].rename(columns={
        "engine": "first_engine",
        "direction": "first_direction",
        "route": "first_route",
        "risk_state": "first_risk_state",
        "quality_score": "first_quality",
        "independent_reasons": "first_reasons",
        "safety_score": "first_safety",
        "confirm_time": "first_confirm_time_leg",
        "resolved_time": "first_resolved_time_leg",
        "realized_unit_R": "first_R",
        "outcome": "first_outcome",
    })

    x = add.merge(first_small, on="episode_id", how="left", validate="many_to_one")
    required = ["first_R", "first_route", "first_confirm_time_leg", "first_resolved_time_leg"]
    missing = {c: int(x[c].isna().sum()) for c in required}
    if any(v > 0 for v in missing.values()):
        raise SystemExit(f"FIRST ENTRY MERGE INCOMPLETE: {missing}")

    x["first_loss"] = pd.to_numeric(x["first_R"], errors="coerce") < 0
    x = x[x["first_loss"]].copy()
    if len(x) != 45:
        raise SystemExit(f"POPULATION IDENTITY FAILED: expected 45 first-loss ADD legs, got {len(x)}")

    x["add_delay_h"] = (x["confirm_time"] - x["first_confirm_time_leg"]).dt.total_seconds() / 3600.0
    x["add_after_first_resolved"] = x["confirm_time"] > x["first_resolved_time_leg"]
    x["route_same"] = x["route"].astype(str).eq(x["first_route"].astype(str))
    x["route_changed"] = ~x["route_same"]
    x["delay_bucket"] = pd.cut(
        x["add_delay_h"],
        [-float("inf"), 24, 72, 168, float("inf")],
        labels=["<=24H", "24-72H", "72-168H", ">168H"]
    )
    x["quality_bucket"] = pd.cut(
        pd.to_numeric(x["quality_score"], errors="coerce"),
        [-float("inf"), 69.999, 79.999, 89.999, float("inf")],
        labels=["<70", "70-79", "80-89", "90+"]
    )
    x["reasons_bucket"] = pd.cut(
        pd.to_numeric(x["independent_reasons"], errors="coerce"),
        [-float("inf"), 2.999, 3.999, 4.999, float("inf")],
        labels=["<=2", "3", "4", "5+"]
    )

    ep_cols = [
        "episode_id", "start_date", "available_90d", "MFE_90d", "MAE_90d",
        "medium_truth_status", "confirmed_false_start_90d"
    ]
    ep_cols = [c for c in ep_cols if c in ep.columns]
    x = x.merge(ep[ep_cols], on="episode_id", how="left", validate="many_to_one")
    if "start_date" in x.columns:
        x["episode_year"] = x["start_date"].dt.year

    x["add_win"] = pd.to_numeric(x["realized_unit_R"], errors="coerce") > 0
    winners = x[x["add_win"]].copy()
    losers = x[~x["add_win"]].copy()
    if len(winners) != 3 or len(losers) != 42:
        raise SystemExit(f"OUTCOME IDENTITY FAILED: expected 3 wins/42 losses, got {len(winners)}/{len(losers)}")

    complete90 = x[bmask(x["available_90d"])] if "available_90d" in x.columns else x.iloc[0:0]
    fs90 = complete90[bmask(complete90["confirmed_false_start_90d"])] if "confirmed_false_start_90d" in complete90.columns else complete90.iloc[0:0]

    categorical = {}
    for cols in [
        ["engine"], ["direction"], ["route"], ["risk_state"], ["route_same"],
        ["add_after_first_resolved"], ["delay_bucket"], ["quality_bucket"], ["reasons_bucket"],
        ["engine", "route"], ["first_route", "route"], ["direction", "engine"], ["episode_year"]
    ]:
        if all(c in x.columns for c in cols):
            categorical["__".join(cols)] = group_stats(x, cols)

    success_cases_cols = [
        "episode_id", "engine", "direction", "first_route", "route", "risk_state",
        "quality_score", "independent_reasons", "safety_score", "add_delay_h",
        "add_after_first_resolved", "route_changed", "realized_unit_R", "outcome",
        "MFE_90d", "medium_truth_status", "confirmed_false_start_90d", "episode_year"
    ]
    success_cases_cols = [c for c in success_cases_cols if c in winners.columns]
    success_cases = [
        {k: clean(v) for k, v in row.items()}
        for row in winners[success_cases_cols].to_dict(orient="records")
    ]

    out = {
        "status": "FAST_DIAGNOSTIC_ONLY_NOT_PROMOTION_EVIDENCE",
        "slice": "FIRST_1H_LOSS_THEN_4H_ADD_45_FORENSICS",
        "population_identity": {
            "first_loss_then_add_n": int(len(x)),
            "add_wins": int(len(winners)),
            "add_losses": int(len(losers)),
            "sum_R": float(pd.to_numeric(x["realized_unit_R"], errors="coerce").sum()),
            "mean_R": float(pd.to_numeric(x["realized_unit_R"], errors="coerce").mean()),
            "expected_identity": "45 = 3 wins + 42 losses = -33R",
        },
        "pre_add_numeric": {
            "all": numeric_summary(x, ["quality_score", "independent_reasons", "safety_score", "add_delay_h"]),
            "winning_adds": numeric_summary(winners, ["quality_score", "independent_reasons", "safety_score", "add_delay_h"]),
            "losing_adds": numeric_summary(losers, ["quality_score", "independent_reasons", "safety_score", "add_delay_h"]),
        },
        "pre_add_categorical_performance": categorical,
        "post_first_loss_timing": {
            "after_first_resolved_n": int(x["add_after_first_resolved"].sum()),
            "route_changed_n": int(x["route_changed"].sum()),
            "route_changed_and_after_resolved_n": int((x["route_changed"] & x["add_after_first_resolved"]).sum()),
        },
        "truth_only_not_add_features": {
            "complete_90d_n": int(len(complete90)),
            "confirmed_false_start_90d_n": int(len(fs90)),
            "medium_truth_status_counts": {
                str(k): int(v) for k, v in complete90["medium_truth_status"].value_counts(dropna=False).items()
            } if "medium_truth_status" in complete90.columns else {},
            "MFE_90d_all": numeric_summary(complete90, ["MFE_90d"]).get("MFE_90d"),
            "MFE_90d_winning_adds": numeric_summary(winners, ["MFE_90d"]).get("MFE_90d"),
            "MFE_90d_losing_adds": numeric_summary(losers, ["MFE_90d"]).get("MFE_90d"),
        },
        "three_winning_add_cases": success_cases,
        "interpretation_rules": {
            "first_1h_loss_is_available_before_4h_add": True,
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
        cols = [
            "episode_id", "engine", "direction", "first_route", "route", "risk_state",
            "quality_score", "independent_reasons", "safety_score", "first_R", "add_delay_h",
            "add_after_first_resolved", "route_changed", "realized_unit_R", "outcome",
            "available_90d", "MFE_90d", "MAE_90d", "medium_truth_status",
            "confirmed_false_start_90d", "episode_year"
        ]
        cols = [c for c in cols if c in x.columns]
        x[cols].to_csv(args.csv_output, index=False)


if __name__ == "__main__":
    main()
