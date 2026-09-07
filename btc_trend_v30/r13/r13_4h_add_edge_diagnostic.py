from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

BASE = Path("btc_trend_v30/r13/output/historical_diagnostic")
LEGS_PATH = BASE / "r13_trade_legs.csv"
EP_PATH = BASE / "r13_episode_metrics.csv"
OUT_JSON = BASE / "r13_4h_add_edge_diagnostic.json"
OUT_CSV = BASE / "r13_4h_add_edge_breakdown.csv"

def stat(df: pd.DataFrame) -> dict:
    n = int(len(df))
    wins = int((df["outcome"] == "TP_3R").sum()) if n else 0
    losses = int((df["outcome"] == "SL_1R").sum()) if n else 0
    total_r = float(pd.to_numeric(df["realized_unit_R"], errors="coerce").fillna(0).sum()) if n else 0.0
    mean_r = float(total_r / n) if n else None
    win_rate = float(wins / n) if n else None
    cap_stop = float((3.0 * wins) / losses) if losses else (None if wins == 0 else float("inf"))
    return {
        "legs": n, "wins": wins, "losses": losses, "win_rate": win_rate,
        "total_R": total_r, "mean_R": mean_r, "capture_to_stop_loss_ratio": cap_stop,
    }

def grouped(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    out=[]
    for keys, g in df.groupby(cols, dropna=False, observed=False):
        if not isinstance(keys, tuple): keys=(keys,)
        row={c: (None if pd.isna(v) else str(v)) for c,v in zip(cols,keys)}
        row.update(stat(g))
        out.append(row)
    return out

def raw_mdd(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"resolution_events":0,"ending_R":0.0,"max_drawdown_R":0.0,"worst_time":None}
    d=df.copy()
    d["resolved_time"]=pd.to_datetime(d["resolved_time"],utc=True,errors="coerce")
    d=d.dropna(subset=["resolved_time"])
    g=d.groupby("resolved_time",as_index=False)["realized_unit_R"].sum().sort_values("resolved_time")
    g["equity"]=g["realized_unit_R"].cumsum()
    g["peak"]=g["equity"].cummax().clip(lower=0)
    g["dd"]=g["equity"]-g["peak"]
    ix=int(g["dd"].idxmin())
    return {
        "resolution_events":int(len(g)),
        "ending_R":float(g["realized_unit_R"].sum()),
        "max_drawdown_R":float(g.loc[ix,"dd"]),
        "worst_time":g.loc[ix,"resolved_time"].isoformat(),
        "note":"EQUAL_1R_RESEARCH_LEGS__NOT_PORTFOLIO_MDD"
    }

def main():
    legs=pd.read_csv(LEGS_PATH)
    eps=pd.read_csv(EP_PATH)
    oos_ids=set(eps["episode_id"].astype(str))
    legs=legs[legs["episode_id"].astype(str).isin(oos_ids)].copy()
    legs["confirm_time"]=pd.to_datetime(legs["confirm_time"],utc=True,errors="coerce")
    legs["resolved_time"]=pd.to_datetime(legs["resolved_time"],utc=True,errors="coerce")
    first=legs[legs["leg"].eq("1H_ENTRY")].copy()
    add=legs[legs["leg"].eq("4H_ADD")].copy()

    checks={
        "oos_episodes_136":len(eps)==136,
        "first_entries_80":len(first)==80,
        "adds_4h_64":len(add)==64,
        "first_total_R_16":abs(float(first["realized_unit_R"].sum())-16.0)<1e-12,
        "add_total_R_minus8":abs(float(add["realized_unit_R"].sum())+8.0)<1e-12,
        "all_total_R_8":abs(float(legs["realized_unit_R"].sum())-8.0)<1e-12,
    }
    if not all(checks.values()):
        raise SystemExit(f"IDENTITY CHECK FAILED: {checks}")

    first_total=float(first["realized_unit_R"].sum())
    add_total=float(add["realized_unit_R"].sum())
    all_total=float(legs["realized_unit_R"].sum())
    edge_erosion_abs=first_total-all_total
    edge_erosion_pct=(edge_erosion_abs/first_total) if first_total else None
    mean_erosion_pct=(stat(first)["mean_R"]-stat(legs)["mean_R"])/stat(first)["mean_R"]

    fp=first[["episode_id","route","risk_state","outcome","realized_unit_R","confirm_time","resolved_time"]].rename(columns={
        "route":"first_route","risk_state":"first_risk_state","outcome":"first_outcome",
        "realized_unit_R":"first_R","confirm_time":"first_confirm_time","resolved_time":"first_resolved_time"
    })
    ap=add.merge(fp,on="episode_id",how="left",validate="one_to_one")
    ap["timing_class"]=np.where(ap["confirm_time"] < ap["first_resolved_time"],"BEFORE_FIRST_RESOLVED","AFTER_FIRST_RESOLVED")
    ap["add_delay_h"]=(ap["confirm_time"]-ap["first_confirm_time"]).dt.total_seconds()/3600.0
    ap["delay_bucket"]=pd.cut(ap["add_delay_h"],[-1,24,72,168,np.inf],labels=["<=24h","24-72h","72-168h",">168h"])
    ap["route_changed"]=ap["route"].astype(str).ne(ap["first_route"].astype(str))
    ap["risk_changed"]=ap["risk_state"].astype(str).ne(ap["first_risk_state"].astype(str))

    ekey=eps[["episode_id","start_date"]].copy()
    ekey["start_date"]=pd.to_datetime(ekey["start_date"],utc=True,errors="coerce")
    ekey["fold_year"]=ekey["start_date"].dt.year.astype("Int64")
    add_y=add.merge(ekey,on="episode_id",how="left",validate="one_to_one")

    pair_matrix=[]
    for (fo,ao),g in ap.groupby(["first_outcome","outcome"],dropna=False):
        pair_matrix.append({
            "first_outcome":str(fo),"add_outcome":str(ao),"episodes":int(len(g)),
            "first_total_R":float(g["first_R"].sum()),
            "add_total_R":float(g["realized_unit_R"].sum()),
            "combined_total_R":float(g["first_R"].sum()+g["realized_unit_R"].sum())
        })

    route_transition=[]
    for (fr,ar),g in ap.groupby(["first_route","route"],dropna=False):
        r={"first_route":str(fr),"add_route":str(ar)}
        r.update(stat(g))
        route_transition.append(r)

    risk_transition=[]
    for (fr,ar),g in ap.groupby(["first_risk_state","risk_state"],dropna=False):
        r={"first_risk_state":str(fr),"add_risk_state":str(ar)}
        r.update(stat(g))
        risk_transition.append(r)

    rows=[]
    for dim, cols in [
        ("direction",["direction"]),("engine",["engine"]),("route",["route"]),("risk_state",["risk_state"]),
        ("timing_class",["timing_class"]),("delay_bucket",["delay_bucket"]),("first_outcome",["first_outcome"])
    ]:
        source=ap if dim in {"timing_class","delay_bucket","first_outcome"} else add
        for r in grouped(source,cols):
            rows.append({"dimension":dim,**r})
    pd.DataFrame(rows).to_csv(OUT_CSV,index=False)

    audit={
        "model":"MASTER_BTC_TREND_V3_R1_3",
        "status":"HISTORICAL_DIAGNOSTIC_ONLY__4H_ADD_EDGE_DECOMPOSITION",
        "identity_checks":checks,
        "headline":{
            "first_1h":stat(first),
            "four_hour_add":stat(add),
            "all_legs":stat(legs),
            "first_total_R_before_add":first_total,
            "four_hour_add_marginal_R":add_total,
            "combined_total_R_after_add":all_total,
            "cumulative_edge_eroded_R":edge_erosion_abs,
            "cumulative_edge_eroded_pct":edge_erosion_pct,
            "mean_R_first_only":stat(first)["mean_R"],
            "mean_R_all_legs":stat(legs)["mean_R"],
            "mean_R_dilution_pct":mean_erosion_pct,
        },
        "four_hour_add_breakdown":{
            "by_direction":grouped(add,["direction"]),
            "by_engine":grouped(add,["engine"]),
            "by_route":grouped(add,["route"]),
            "by_risk_state":grouped(add,["risk_state"]),
            "by_direction_engine":grouped(add,["direction","engine"]),
            "by_engine_route":grouped(add,["engine","route"]),
            "by_engine_risk_state":grouped(add,["engine","risk_state"]),
            "by_route_risk_state":grouped(add,["route","risk_state"]),
            "by_timing_relative_to_first_resolution":grouped(ap,["timing_class"]),
            "by_timing_and_direction":grouped(ap,["timing_class","direction"]),
            "by_timing_and_route":grouped(ap,["timing_class","route"]),
            "by_delay_bucket":grouped(ap,["delay_bucket"]),
            "by_first_outcome":grouped(ap,["first_outcome"]),
            "route_transition":route_transition,
            "risk_state_transition":risk_transition,
            "pair_outcome_matrix":pair_matrix,
            "by_fold_year":grouped(add_y,["fold_year"]),
        },
        "raw_equal_1R_path":{
            "first_only":raw_mdd(first),
            "four_hour_add_only":raw_mdd(add),
            "all_legs":raw_mdd(legs),
        },
        "guardrails":{
            "rules_changed":False,
            "production_promotion_allowed":False,
            "future_first_outcome_may_not_be_used_as_live_gate":True,
            "overlapping_breakdown_dimensions_must_not_be_summed":True,
            "portfolio_mdd":"N/A_ALLOCATION_MODEL_NOT_DEFINED",
            "interpretation":"Diagnostic attribution only. Any rule change requires a new frozen version and fresh untouched OOS."
        }
    }
    OUT_JSON.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(audit,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
