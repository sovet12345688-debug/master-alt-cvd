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
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    root = Path(args.snapshot_root) / "r13/output/historical_diagnostic"
    legs = pd.read_csv(root / "r13_trade_legs.csv")
    ep = pd.read_csv(root / "r13_episode_metrics.csv")

    for c in ["confirm_time", "resolved_time"]:
        legs[c] = pd.to_datetime(legs[c], utc=True, format="mixed", errors="coerce")
    for c in ["first_confirm_time", "first_resolved_time", "start_date"]:
        ep[c] = pd.to_datetime(ep[c], utc=True, format="mixed", errors="coerce")

    ids = set(ep["episode_id"].astype(str))
    first = legs[legs["leg"].eq("1H_ENTRY") & legs["episode_id"].astype(str).isin(ids)].copy()
    add = legs[legs["leg"].eq("4H_ADD") & legs["episode_id"].astype(str).isin(ids)].copy()

    f = first[["episode_id","route","risk_state","confirm_time","resolved_time","realized_unit_R","outcome"]].rename(columns={
        "route":"first_route","risk_state":"first_risk_state","confirm_time":"first_confirm_time_leg",
        "resolved_time":"first_resolved_time_leg","realized_unit_R":"first_R","outcome":"first_outcome"})
    x = add.merge(f, on="episode_id", how="left")
    x = x[x["route"].ne(x["first_route"])].copy()
    x["delay_h"] = (x["confirm_time"] - x["first_confirm_time_leg"]).dt.total_seconds()/3600
    x["after_first_resolved"] = x["confirm_time"] > x["first_resolved_time_leg"]
    x = x.merge(ep[["episode_id","start_date","MFE_90d","available_90d","medium_truth_status","confirmed_false_start_90d"]], on="episode_id", how="left")
    x["episode_year"] = x["start_date"].dt.year
    x["medium_hit"] = x["medium_truth_status"].astype(str).str.contains("SUCCESS")

    cols = ["episode_id","engine","direction","first_route","route","first_R","realized_unit_R","delay_h",
            "after_first_resolved","risk_state","quality_score","independent_reasons","safety_score","MFE_90d",
            "medium_truth_status","confirmed_false_start_90d","episode_year"]
    rows = x[cols].copy()

    out = {
        "status":"FAST_DIAGNOSTIC_ONLY_NOT_PROMOTION_EVIDENCE",
        "n":int(len(x)),
        "wins":int((pd.to_numeric(x["realized_unit_R"], errors="coerce")>0).sum()),
        "losses":int((pd.to_numeric(x["realized_unit_R"], errors="coerce")<0).sum()),
        "sum_R":float(pd.to_numeric(x["realized_unit_R"], errors="coerce").sum()),
        "transitions":x.groupby(["first_route","route"]).size().rename("n").reset_index().to_dict("records"),
        "engine_counts":x.groupby("engine").size().to_dict(),
        "direction_counts":x.groupby("direction").size().to_dict(),
        "first_trade_results":{"first_loss":int((pd.to_numeric(x["first_R"], errors="coerce")<0).sum()),"first_win":int((pd.to_numeric(x["first_R"], errors="coerce")>0).sum())},
        "timing":{"after_first_resolved":int(x["after_first_resolved"].sum()),"before_or_at_first_resolved":int((~x["after_first_resolved"]).sum()),"median_delay_h":float(x["delay_h"].median()),"mean_delay_h":float(x["delay_h"].mean())},
        "truth90":{"medium_hit":int(x["medium_hit"].sum()),"no_medium_target":int((~x["medium_hit"]).sum()),"median_MFE_90d":float(pd.to_numeric(x["MFE_90d"],errors="coerce").median())},
        "risk_quality":{"open_n":int(x["risk_state"].eq("OPEN").sum()),"safety_100_n":int((pd.to_numeric(x["safety_score"],errors="coerce")==100).sum()),"median_quality":float(pd.to_numeric(x["quality_score"],errors="coerce").median()),"median_reasons":float(pd.to_numeric(x["independent_reasons"],errors="coerce").median())},
        "rows":rows.to_dict("records")
    }
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))

    assert out["n"] == 12, out["n"]
    assert out["wins"] == 0 and out["losses"] == 12 and out["sum_R"] == -12.0


if __name__ == "__main__":
    main()
