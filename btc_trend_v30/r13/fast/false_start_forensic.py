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


def categorical_false_rate(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    rows = []
    for keys, g in df.groupby(cols, dropna=False, observed=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        fs = int(g["is_false_start"].sum())
        n = int(len(g))
        row = {c: ("N/A" if pd.isna(v) else clean(v)) for c, v in zip(cols, keys)}
        row.update({
            "n": n,
            "false_start_n": fs,
            "control_n": n - fs,
            "false_start_rate": float(fs / n) if n else None,
            "delta_vs_overall_pp": float((fs / n - 0.5) * 100.0) if n else None,
        })
        rows.append(row)
    rows.sort(key=lambda r: (-r["n"], str(r)))
    return rows


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

    complete = ep[bmask(ep["available_90d"]) & bmask(ep["entry_1h"])].copy()
    complete["is_false_start"] = bmask(complete["confirmed_false_start_90d"])

    first = legs[legs["leg"].eq("1H_ENTRY")].copy()
    first = first[first["episode_id"].astype(str).isin(set(complete["episode_id"].astype(str)))]
    first = first.drop_duplicates("episode_id", keep="first")

    first_cols = [
        "episode_id", "engine", "direction", "route", "risk_state", "quality_score",
        "independent_reasons", "safety_score", "confirm_time", "resolved_time",
        "realized_unit_R", "outcome"
    ]
    first_cols = [c for c in first_cols if c in first.columns]
    first_small = first[first_cols].rename(columns={
        "engine": "first_engine_leg",
        "direction": "first_direction_leg",
        "route": "first_route_leg",
        "risk_state": "first_risk_leg",
        "quality_score": "first_quality_leg",
        "independent_reasons": "first_reasons_leg",
        "safety_score": "first_safety_leg",
        "confirm_time": "first_confirm_leg",
        "resolved_time": "first_resolved_leg",
        "realized_unit_R": "first_R",
        "outcome": "first_outcome",
    })
    x = complete.merge(first_small, on="episode_id", how="left", validate="one_to_one")

    required_first = [
        "first_engine_leg", "first_direction_leg", "first_route_leg", "first_risk_leg",
        "first_quality_leg", "first_reasons_leg", "first_safety_leg", "first_R"
    ]
    missing_first = {c: int(x[c].isna().sum()) for c in required_first if c in x.columns}
    if any(v > 0 for v in missing_first.values()):
        raise SystemExit(f"FIRST-LEG MERGE INCOMPLETE: {missing_first}")

    adds = legs[legs["leg"].eq("4H_ADD")].copy()
    adds = adds[adds["episode_id"].astype(str).isin(set(x["episode_id"].astype(str)))]
    add_summary = []
    for eid, g in adds.groupby("episode_id", observed=False):
        first_row = x.loc[x["episode_id"].astype(str).eq(str(eid))].iloc[0]
        first_route = str(first_row["first_route_leg"])
        first_resolved = first_row.get("first_resolved_leg")
        route_changed = bool((g["route"].astype(str) != first_route).any()) if "route" in g.columns else False
        after_resolved = False
        if "confirm_time" in g.columns and pd.notna(first_resolved):
            after_resolved = bool((g["confirm_time"] > first_resolved).any())
        add_summary.append({
            "episode_id": eid,
            "has_add": True,
            "add_n": int(len(g)),
            "add_sum_R": float(pd.to_numeric(g["realized_unit_R"], errors="coerce").sum()),
            "add_route_changed": route_changed,
            "add_after_first_resolved": after_resolved,
        })
    if add_summary:
        x = x.merge(pd.DataFrame(add_summary), on="episode_id", how="left")
    else:
        x["has_add"] = False
        x["add_n"] = 0
        x["add_sum_R"] = 0.0
        x["add_route_changed"] = False
        x["add_after_first_resolved"] = False

    for c, default in [
        ("has_add", False), ("add_n", 0), ("add_sum_R", 0.0),
        ("add_route_changed", False), ("add_after_first_resolved", False)
    ]:
        x[c] = x[c].fillna(default)

    x["first_entry_loss"] = pd.to_numeric(x["first_R"], errors="coerce") < 0
    x["first_entry_win"] = pd.to_numeric(x["first_R"], errors="coerce") > 0
    x["quality_bucket"] = pd.cut(
        pd.to_numeric(x["first_quality_leg"], errors="coerce"),
        [-float("inf"), 69.999, 79.999, 89.999, float("inf")],
        labels=["<70", "70-79", "80-89", "90+"]
    )
    x["reasons_bucket"] = pd.cut(
        pd.to_numeric(x["first_reasons_leg"], errors="coerce"),
        [-float("inf"), 2.999, 3.999, 4.999, float("inf")],
        labels=["<=2", "3", "4", "5+"]
    )

    fs = x[x["is_false_start"]].copy()
    ctrl = x[~x["is_false_start"]].copy()
    if len(fs) != 39 or len(ctrl) != 39:
        raise SystemExit(f"POPULATION IDENTITY FAILED: false={len(fs)} control={len(ctrl)}")

    pre_num_cols = ["first_quality_leg", "first_reasons_leg", "first_safety_leg"]
    truth_num_cols = [c for c in ["MFE_90d", "MAE_90d"] if c in x.columns]

    cat_tables = {}
    for cols in [
        ["first_engine_leg"], ["first_direction_leg"], ["first_route_leg"], ["first_risk_leg"],
        ["first_engine_leg", "first_route_leg"], ["first_direction_leg", "first_route_leg"],
        ["quality_bucket"], ["reasons_bucket"]
    ]:
        cat_tables["__".join(cols)] = categorical_false_rate(x, cols)

    def post_counts(g: pd.DataFrame) -> dict:
        return {
            "n": int(len(g)),
            "first_entry_loss_n": int(g["first_entry_loss"].sum()),
            "first_entry_win_n": int(g["first_entry_win"].sum()),
            "has_4h_add_n": int(g["has_add"].sum()),
            "route_changed_add_n": int(g["add_route_changed"].sum()),
            "add_after_first_resolved_n": int(g["add_after_first_resolved"].sum()),
            "add_sum_R": float(pd.to_numeric(g["add_sum_R"], errors="coerce").sum()),
        }

    candidate_enrichments = []
    for table_name, rows in cat_tables.items():
        for r in rows:
            if r["n"] >= 5 and r["delta_vs_overall_pp"] is not None:
                candidate_enrichments.append({"table": table_name, **r})
    candidate_enrichments.sort(key=lambda r: abs(r["delta_vs_overall_pp"]), reverse=True)

    total_route_changed = int(x["add_route_changed"].sum())
    out = {
        "status": "FAST_DIAGNOSTIC_ONLY_NOT_PROMOTION_EVIDENCE",
        "slice": "FALSE_START_39_FORENSICS_V2_MERGE_AUDITED",
        "population": {
            "executed_complete_90d": int(len(x)),
            "false_start_n": int(len(fs)),
            "control_non_false_start_n": int(len(ctrl)),
            "false_start_rate": float(len(fs) / len(x)),
        },
        "merge_audit": {
            "first_leg_missing_counts": missing_first,
            "route_changed_add_total_in_complete90_population": total_route_changed,
            "note": "Unique first-leg column names prevent episode/leg suffix collisions."
        },
        "pre_entry_numeric": {
            "false_start": numeric_summary(fs, pre_num_cols),
            "control": numeric_summary(ctrl, pre_num_cols),
        },
        "pre_entry_categorical_false_rates": cat_tables,
        "largest_diagnostic_enrichments_n_ge_5": candidate_enrichments[:15],
        "post_entry_behavior": {
            "false_start": post_counts(fs),
            "control": post_counts(ctrl),
        },
        "truth_only_not_entry_features": {
            "false_start": numeric_summary(fs, truth_num_cols),
            "control": numeric_summary(ctrl, truth_num_cols),
        },
        "interpretation_rules": {
            "no_rule_change": True,
            "pre_entry_only_for_candidate_filters": True,
            "post_entry_and_truth_fields_are_diagnostic_not_eligible_as_entry_features": True,
            "same_historical_snapshot_cannot_validate_new_thresholds": True,
        },
    }

    text = json.dumps(out, ensure_ascii=False, indent=2, default=str)
    Path(args.output).write_text(text, encoding="utf-8")
    print(text)

    if args.csv_output:
        cols = [
            "episode_id", "first_engine_leg", "first_direction_leg", "first_route_leg", "first_risk_leg",
            "first_quality_leg", "first_reasons_leg", "first_safety_leg", "first_R", "first_outcome",
            "has_add", "add_n", "add_sum_R", "add_route_changed", "add_after_first_resolved",
            "MFE_90d", "MAE_90d", "medium_truth_status", "is_false_start"
        ]
        cols = [c for c in cols if c in x.columns]
        x[cols].to_csv(args.csv_output, index=False)


if __name__ == "__main__":
    main()
