from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def b(v) -> bool:
    return str(v).lower() == "true" if not isinstance(v, bool) else v


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot-root", required=True)
    ap.add_argument("--json-output", required=True)
    ap.add_argument("--csv-output", required=True)
    args = ap.parse_args()

    root = Path(args.snapshot_root) / "r13/output/historical_diagnostic"
    legs = pd.read_csv(root / "r13_trade_legs.csv")
    ep = pd.read_csv(root / "r13_episode_metrics.csv")

    for c in ["confirm_time", "resolved_time"]:
        legs[c] = pd.to_datetime(legs[c], utc=True, format="mixed", errors="coerce")
    for c in ["start_date", "first_confirm_time", "first_resolved_time"]:
        ep[c] = pd.to_datetime(ep[c], utc=True, format="mixed", errors="coerce")

    ids = set(ep["episode_id"].astype(str))
    first = legs[(legs["leg"] == "1H_ENTRY") & legs["episode_id"].astype(str).isin(ids)].copy()
    add = legs[(legs["leg"] == "4H_ADD") & legs["episode_id"].astype(str).isin(ids)].copy()
    sc = add[add["engine"] == "SC"].copy()

    first_small = first[[
        "episode_id", "route", "confirm_time", "resolved_time", "outcome", "realized_unit_R"
    ]].rename(columns={
        "route": "first_route",
        "confirm_time": "first_confirm_time_leg",
        "resolved_time": "first_resolved_time_leg",
        "outcome": "first_outcome_leg",
        "realized_unit_R": "first_R",
    })

    ep_small = ep[[
        "episode_id", "start_date", "MFE_90d", "MFE_365d", "available_90d", "available_365d",
        "medium_truth_status", "long_truth_status", "confirmed_false_start_90d"
    ]].copy()

    x = sc.merge(first_small, on="episode_id", how="left").merge(ep_small, on="episode_id", how="left")
    x["delay_h"] = (x["confirm_time"] - x["first_confirm_time_leg"]).dt.total_seconds() / 3600
    x["after_first_resolved"] = x["confirm_time"] > x["first_resolved_time_leg"]
    x["route_changed"] = x["route"] != x["first_route"]
    x["episode_year"] = x["start_date"].dt.year
    x["has_20pct_90d_trend"] = pd.to_numeric(x["MFE_90d"], errors="coerce") >= 0.20
    x["has_30pct_90d_trend"] = pd.to_numeric(x["MFE_90d"], errors="coerce") >= 0.30
    x["failure_type"] = x["has_20pct_90d_trend"].map({True: "REAL_TREND_BUT_ENTRY_STOPPED", False: "NO_20PCT_90D_TREND"})

    summary = {
        "status": "FAST_DIAGNOSTIC_ONLY_NOT_PROMOTION_EVIDENCE",
        "slice": "SC_4H_ADD_FORENSICS",
        "n": int(len(x)),
        "wins": int((pd.to_numeric(x["realized_unit_R"], errors="coerce") > 0).sum()),
        "losses": int((pd.to_numeric(x["realized_unit_R"], errors="coerce") < 0).sum()),
        "sum_R": float(pd.to_numeric(x["realized_unit_R"], errors="coerce").sum()),
        "all_risk_state_open": bool((x["risk_state"] == "OPEN").all()),
        "all_safety_100": bool((pd.to_numeric(x["safety_score"], errors="coerce") == 100).all()),
        "quality_median": float(pd.to_numeric(x["quality_score"], errors="coerce").median()),
        "independent_reasons_median": float(pd.to_numeric(x["independent_reasons"], errors="coerce").median()),
        "first_entry_tp3_n": int((x["first_outcome_leg"] == "TP_3R").sum()),
        "first_entry_sl1_n": int((x["first_outcome_leg"] == "SL_1R").sum()),
        "route_changed_n": int(x["route_changed"].sum()),
        "after_first_resolved_n": int(x["after_first_resolved"].sum()),
        "delay_h_median": float(x["delay_h"].median()),
        "delay_h_mean": float(x["delay_h"].mean()),
        "delay_gt72h_n": int((x["delay_h"] > 72).sum()),
        "delay_gt168h_n": int((x["delay_h"] > 168).sum()),
        "has_20pct_90d_trend_n": int(x["has_20pct_90d_trend"].sum()),
        "no_20pct_90d_trend_n": int((~x["has_20pct_90d_trend"]).sum()),
        "confirmed_false_start_n": int(x["confirmed_false_start_90d"].map(b).sum()),
        "interpretation": {
            "primary": "SC 4H ADD failures are dominated by false continuation classification: most episodes never produced a >=20% favorable 90D move.",
            "secondary": "Delay, route changes, and post-resolution adds are overlapping secondary risk patterns, not additive independent causes.",
            "rule_change_allowed": False,
            "next_version_only": "Any SC filter change belongs to a new pre-frozen baseline, not frozen R1.3."
        }
    }

    keep = [
        "episode_id", "episode_year", "start_date", "first_route", "first_outcome_leg", "first_R",
        "first_confirm_time_leg", "first_resolved_time_leg", "route", "risk_state", "confirm_time", "delay_h",
        "after_first_resolved", "route_changed", "quality_score", "independent_reasons", "safety_score",
        "entry", "stop", "target", "resolved_time", "realized_unit_R", "MFE_90d", "MFE_365d",
        "medium_truth_status", "long_truth_status", "confirmed_false_start_90d", "failure_type"
    ]
    cases = x[keep].sort_values("confirm_time").copy()

    out = {"summary": summary, "cases": json.loads(cases.to_json(orient="records", date_format="iso"))}
    Path(args.json_output).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    cases.to_csv(args.csv_output, index=False)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
