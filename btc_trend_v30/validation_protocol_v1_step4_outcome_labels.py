from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

STATUS="RESEARCH_ONLY_PROMOTION_HOLD"
DATA=Path("btc_trend_v30/output/validation_v1/data_integrity/btc_usdt_1d_matrix.csv")
EP=Path("btc_trend_v30/output/validation_v1/step3_episode_dedupe/independent_episodes.csv")
STEP3=Path("btc_trend_v30/output/validation_v1/step3_episode_dedupe/audit.json")
OUT=Path("btc_trend_v30/output/validation_v1/step4_outcome_labels")
OUT.mkdir(parents=True,exist_ok=True)


def first_day(arr,cond):
    q=np.flatnonzero(cond(arr)); return int(q[0])+1 if len(q) else np.nan


def episode_truth(ep,df):
    idx=int(ep.start_idx); entry=float(ep.start_close); direction=str(ep.direction)
    out={}
    for horizon in (30,90,180,365):
        avail=idx+horizon<len(df); out[f"available_{horizon}d"]=bool(avail)
        n=min(horizon,len(df)-idx-1); fut=df.iloc[idx+1:idx+1+n]
        if not len(fut):
            out[f"observed_days_{horizon}d"]=0; continue
        out[f"observed_days_{horizon}d"]=int(len(fut))
        hi=fut.high.to_numpy(float); lo=fut.low.to_numpy(float)
        if direction=="long":
            out[f"MFE_{horizon}d"]=float(np.max(hi/entry-1)); out[f"MAE_{horizon}d"]=float(np.max(1-lo/entry))
            targets={"M20":lambda a: hi>=entry*1.20,"M30":lambda a: hi>=entry*1.30,"L50":lambda a: hi>=entry*1.50,"L100":lambda a: hi>=entry*2.00}
        else:
            out[f"MFE_{horizon}d"]=float(np.max(1-lo/entry)); out[f"MAE_{horizon}d"]=float(np.max(hi/entry-1))
            targets={"M20":lambda a: lo<=entry*.80,"M30":lambda a: lo<=entry*.70,"L33":lambda a: lo<=entry*.67,"L50":lambda a: lo<=entry*.50}
        for name,fn in targets.items(): out[f"{name}_day_{horizon}d"]=first_day(fut,fn)
    # Official horizon status is censored unless the full required horizon exists.
    if not out.get("available_90d",False): med="CENSORED"
    else:
        primary=out.get("M20_day_90d",np.nan); extension=out.get("M30_day_90d",np.nan)
        med="MEDIUM_SUCCESS_30" if np.isfinite(extension) else ("MEDIUM_SUCCESS_20" if np.isfinite(primary) else "NO_MEDIUM_TARGET")
    if not out.get("available_365d",False): lng="CENSORED"
    else:
        if direction=="long": primary=out.get("L50_day_365d",np.nan); extension=out.get("L100_day_365d",np.nan)
        else: primary=out.get("L33_day_365d",np.nan); extension=out.get("L50_day_365d",np.nan)
        lng="LONG_SUCCESS_EXTENSION" if np.isfinite(extension) else ("LONG_SUCCESS_PRIMARY" if np.isfinite(primary) else "NO_LONG_TARGET")
    out["medium_truth_status"]=med; out["long_truth_status"]=lng
    # TRUE FALSE START / PARTIAL / MISSED require later state-machine + truth-trend reconciliation.
    out["final_outcome_class"]="PENDING_STATE_MACHINE_AND_MISSED_TREND_RECONCILIATION"
    return out


def main():
    if not STEP3.exists() or json.loads(STEP3.read_text()).get("step3")!="PASS": raise RuntimeError("Step3 PASS required")
    df=pd.read_csv(DATA); ep=pd.read_csv(EP); df[["high","low","close"]]=df[["high","low","close"]].astype(float)
    truth=pd.DataFrame([episode_truth(r,df) for _,r in ep.iterrows()]); z=pd.concat([ep.reset_index(drop=True),truth],axis=1)
    z.to_csv(OUT/"episode_medium_long_truth_labels.csv",index=False)
    med_av=z.available_90d.astype(bool); long_av=z.available_365d.astype(bool)
    engine={}
    for e,g in z.groupby("engine"):
        ma=g[g.available_90d]; la=g[g.available_365d]
        engine[e]={"episodes":len(g),"medium_available":len(ma),"medium20_success":int(ma.medium_truth_status.isin(["MEDIUM_SUCCESS_20","MEDIUM_SUCCESS_30"]).sum()),"medium30_success":int((ma.medium_truth_status=="MEDIUM_SUCCESS_30").sum()),"long_available":len(la),"long_primary_or_extension_success":int(la.long_truth_status.isin(["LONG_SUCCESS_PRIMARY","LONG_SUCCESS_EXTENSION"]).sum()),"long_extension_success":int((la.long_truth_status=="LONG_SUCCESS_EXTENSION").sum())}
    checks={"all_episode_rows_labeled":len(z)==len(ep),"medium_availability_gate_explicit":bool(med_av.notna().all()),"long_availability_gate_explicit":bool(long_av.notna().all()),"final_false_start_not_invented":bool((z.final_outcome_class=="PENDING_STATE_MACHINE_AND_MISSED_TREND_RECONCILIATION").all())}
    ok=all(checks.values())
    summary={"status":STATUS,"protocol":"VALIDATION_PROTOCOL_V1_0_FINAL_LOCK","step":"4_MEDIUM_LONG_OUTCOME_LABELING","episodes":len(z),"medium_horizon":"90D primary; 30D auxiliary","long_horizon":"365D primary; 180D auxiliary","targets":{"LONG":{"medium":[20,30],"long":[50,100]},"SHORT":{"medium":[-20,-30],"long":[-33,-50]}},"future_data_usage":"historical truth labels only; never features","engine_counts":engine,"censored":{"medium_90d":int((~med_av).sum()),"long_365d":int((~long_av).sum())},"checks":checks,"step4":"PASS" if ok else "HOLD","next_step":"STEP5_WALK_FORWARD_OOS" if ok else "STOP_AND_AUDIT_LABELS","probability":"확률 산출보류","v2_6_modified":False,"promotion":"HOLD"}
    (OUT/"audit.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
