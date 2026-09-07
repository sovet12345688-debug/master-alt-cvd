from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

import validation_protocol_v1_step2_r12_daily_scoring as s2

STATUS="RESEARCH_ONLY_PROMOTION_HOLD"
ENGINES=("LR","LC","SR","SC")
LONG=("LR","LC")
IN=Path("btc_trend_v30/output/validation_v1/data_integrity/btc_usdt_1d_matrix.csv")
STEP2=Path("btc_trend_v30/output/validation_v1/step2_full_daily_scoring/audit.json")
OUT=Path("btc_trend_v30/output/validation_v1/step3_episode_dedupe")
OUT.mkdir(parents=True,exist_ok=True)


def signal_zone(row,eng):
    atr=float(row.atr14) if pd.notna(row.atr14) and row.atr14>0 else float(row.close)*.02
    if eng=="LR": anchor=float(min(row.low,row.prior_low20 if pd.notna(row.prior_low20) else row.low)); lo,hi=anchor,anchor+.85*atr
    elif eng=="SR": anchor=float(max(row.high,row.prior_high20 if pd.notna(row.prior_high20) else row.high)); lo,hi=anchor-.85*atr,anchor
    elif eng=="LC": anchor=float(min(row.low,row.prior_low10 if pd.notna(row.prior_low10) else row.low)); lo,hi=anchor,anchor+.70*atr
    else: anchor=float(max(row.high,row.prior_high10 if pd.notna(row.prior_high10) else row.high)); lo,hi=anchor-.70*atr,anchor
    return anchor,lo,hi,atr


def build_families(scored):
    """Recovered R1.2 causal Zone Family v4; no fixed 14-day grouping."""
    fams=[]; sig=[]; seeds=[]; nxt={"long":1,"short":1}; cur={"long":None,"short":None}
    def newf(direction,date,anchor,atr,source):
        width=.85*atr; lo,hi=(anchor,anchor+width) if direction=="long" else (anchor-width,anchor); fid=f"{direction[0].upper()}ZF{nxt[direction]:03d}"; nxt[direction]+=1
        f={"family_id":fid,"direction":direction,"created":date,"anchor":anchor,"zone_lo":lo,"zone_hi":hi,"last_atr":atr,"last_seed_date":date,"last_signal_date":pd.NaT,"source":source,"seed_count":1,"signal_count":0,"departed":False,"departure_date":pd.NaT,"retired":False}; fams.append(f); cur[direction]=f; return f
    def departure(f,row,date):
        if f is None or f["retired"] or pd.isna(row.atr14): return
        atr=max(float(row.atr14),f["last_atr"],1e-9); c=float(row.close)
        if f["direction"]=="long" and c>f["zone_hi"]+2.5*atr and not f["departed"]: f["departed"]=True; f["departure_date"]=date
        if f["direction"]=="short" and c<f["zone_lo"]-2.5*atr and not f["departed"]: f["departed"]=True; f["departure_date"]=date
    for idx,row in scored.reset_index(drop=True).iterrows():
        if pd.isna(row.atr14): continue
        date=pd.Timestamp(row.date); atr=float(row.atr14)
        for d in ("long","short"): departure(cur[d],row,date)
        sd=[]
        if pd.notna(row.prior_low20) and row.low<row.prior_low20: sd.append(("long",float(row.low),"new20_low"))
        if pd.notna(row.prior_high20) and row.high>row.prior_high20: sd.append(("short",float(row.high),"new20_high"))
        if pd.notna(row.vol_ratio20) and row.ret1<=-.05 and row.vol_ratio20>=1.5: sd.append(("long",float(row.low),"sell_climax"))
        if pd.notna(row.vol_ratio20) and row.ret1>=.05 and row.vol_ratio20>=1.5: sd.append(("short",float(row.high),"buy_climax"))
        for direction,anchor,source in sd:
            f=cur[direction]; create=False
            if f is None or f["retired"]: create=True
            elif f["departed"] and pd.notna(f["departure_date"]) and (date-f["departure_date"]).days>=5:
                create=anchor<f["anchor"]-max(.50*atr,.015*f["anchor"]) if direction=="long" else anchor>f["anchor"]+max(.50*atr,.015*f["anchor"])
            elif (date-f["last_seed_date"]).days>60: create=True
            if create:
                if f is not None: f["retired"]=True
                f=newf(direction,date,anchor,atr,source); created=True
            else:
                created=False; f["anchor"]=min(f["anchor"],anchor) if direction=="long" else max(f["anchor"],anchor); width=.85*atr; f["zone_lo"],f["zone_hi"]=(f["anchor"],f["anchor"]+width) if direction=="long" else (f["anchor"]-width,f["anchor"]); f["last_atr"]=atr; f["last_seed_date"]=date; f["seed_count"]+=1
            seeds.append({"date":date,"idx":idx,"direction":direction,"family_id":f["family_id"],"source":source,"anchor":anchor,"created_new":created})
        for eng in ENGINES:
            if not bool(row.get(f"{eng}_eligible",False)): continue
            direction="long" if eng in LONG else "short"; anchor,lo,hi,a2=signal_zone(row,eng); f=cur[direction]
            if f is None or f["retired"]: f=newf(direction,date,anchor,a2,"signal_local")
            else:
                dist=abs(anchor-f["anchor"]); repriced=dist>max(2.5*a2,.055*f["anchor"])
                if eng in ("LC","SC") and f["departed"] and repriced and pd.notna(f["departure_date"]) and (date-f["departure_date"]).days>=5:
                    f["retired"]=True; f=newf(direction,date,anchor,a2,"continuation_local")
            f["signal_count"]+=1; f["last_signal_date"]=date
            sig.append({"date":date,"idx":idx,"engine":eng,"structure":"REV" if eng in ("LR","SR") else "CONT","direction":direction,"score":float(row[f"{eng}_SCORE"]),"stage":int(row[f"{eng}_STAGE"]),"execution_eligible":bool(row[f"{eng}_execution_eligible"]),"new_risk_ok":bool(row[f"{eng}_new_risk_ok"]),"family_id":f["family_id"],"anchor":anchor,"zone_lo":lo,"zone_hi":hi,"close":float(row.close)})
    return pd.DataFrame(sig),pd.DataFrame(seeds),pd.DataFrame(fams)


def episodes(sig):
    """Independent episode requires same direction + same structure + same Zone Family."""
    if sig.empty: return pd.DataFrame()
    out=[]
    for (fid,eng),g in sig.sort_values("date").groupby(["family_id","engine"],sort=False):
        first=g.iloc[0]; mx=g.loc[g.score.idxmax()]; structure="REV" if eng in ("LR","SR") else "CONT"
        out.append({"family_id":fid,"independent_episode_id":f"{fid}:{structure}","directional_risk_episode_id":fid,"engine":eng,"structure":structure,"direction":first.direction,"start_idx":int(first.idx),"start_date":first.date,"start_close":float(first.close),"first_score":float(first.score),"first_stage":int(first.stage),"max_score":float(mx.score),"max_score_date":mx.date,"signal_days":len(g),"first_execution_eligible":bool(first.execution_eligible),"first_new_risk_ok":bool(first.new_risk_ok)})
    return pd.DataFrame(out).sort_values("start_date").reset_index(drop=True)


def prefix_test(raw,full_sig):
    checks=[]
    for ds in ["2020-12-31","2021-12-31","2022-12-31","2023-12-31","2024-12-31","2025-12-31","2026-09-04"]:
        t=pd.Timestamp(ds,tz="UTC"); r=raw[pd.to_datetime(raw.open_time,unit="ms",utc=True)<=t].copy(); x=s2.build(r); q,_,_=build_families(x); ref=full_sig[full_sig.idx<len(r)].reset_index(drop=True)
        cols=["idx","engine","family_id","direction","structure"]
        ok=len(q)==len(ref) and (q[cols].astype(str).to_numpy()==ref[cols].astype(str).to_numpy()).all()
        checks.append({"checkpoint":ds,"rows":len(r),"signals":len(q),"pass":bool(ok)})
    return checks


def main():
    if not STEP2.exists() or json.loads(STEP2.read_text()).get("step2")!="PASS": raise RuntimeError("Step2 PASS required")
    raw=pd.read_csv(IN); x=s2.build(raw); sig,seeds,fams=build_families(x); eps=episodes(sig); pit=prefix_test(raw,sig); pit_ok=all(z["pass"] for z in pit)
    sig.to_csv(OUT/"daily_signal_family_assignments.csv",index=False); seeds.to_csv(OUT/"zone_family_seeds.csv",index=False); fams.to_csv(OUT/"zone_families.csv",index=False); eps.to_csv(OUT/"independent_episodes.csv",index=False)
    counts={e:int((sig.engine==e).sum()) for e in ENGINES}; epcounts={e:int((eps.engine==e).sum()) for e in ENGINES}; unique_ids=int(eps.independent_episode_id.nunique()) if len(eps) else 0
    no_collision=(len(eps)==unique_ids)
    summary={"status":STATUS,"protocol":"VALIDATION_PROTOCOL_V1_0_FINAL_LOCK","step":"3_ZONE_FAMILY_INDEPENDENT_EPISODE_DEDUPE","dedupe_rule":"same direction + same structure + same causal Zone Family; no fixed 14-day grouping","daily_signal_n":len(sig),"daily_signal_by_engine":counts,"zone_family_n":len(fams),"independent_episode_n":unique_ids,"episode_by_engine":epcounts,"episode_id_unique":no_collision,"point_in_time_prefix_invariance":{"pass":pit_ok,"tests":pit},"step3":"PASS" if pit_ok and no_collision else "HOLD","next_step":"STEP4_MEDIUM_LONG_OUTCOME_LABELING" if pit_ok and no_collision else "STOP_AND_AUDIT_DEDUPE","probability":"확률 산출보류","v2_6_modified":False,"promotion":"HOLD"}
    (OUT/"audit.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
