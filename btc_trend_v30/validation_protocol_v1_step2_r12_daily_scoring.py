from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ENGINE = "MASTER_BTC_TREND_V3_R1_2_VALIDATION_PROTOCOL_V1"
STATUS = "RESEARCH_ONLY_PROMOTION_HOLD"
ENGINES = ("LR","LC","SR","SC")
IN = Path("btc_trend_v30/output/validation_v1/data_integrity/btc_usdt_1d_matrix.csv")
AUDIT = Path("btc_trend_v30/output/validation_v1/data_integrity/audit.json")
OUT = Path("btc_trend_v30/output/validation_v1/step2_full_daily_scoring")
OUT.mkdir(parents=True, exist_ok=True)


def add_core(df: pd.DataFrame) -> pd.DataFrame:
    x=df.copy().sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    for c in ["open","high","low","close","volume","quote_volume","taker_buy_quote"]: x[c]=pd.to_numeric(x[c],errors="coerce")
    x["date"]=pd.to_datetime(x.open_time,unit="ms",utc=True)
    o,h,l,c,v=x.open,x.high,x.low,x.close,x.volume
    prev=c.shift(1); tr=pd.concat([(h-l).abs(),(h-prev).abs(),(l-prev).abs()],axis=1).max(axis=1)
    x["atr14"]=tr.rolling(14,min_periods=14).mean()
    x["ema20"]=c.ewm(span=20,adjust=False,min_periods=20).mean(); x["ma50"]=c.rolling(50,min_periods=50).mean(); x["ma200"]=c.rolling(200,min_periods=200).mean()
    x["ema20_slope5"]=x.ema20/x.ema20.shift(5)-1; x["ma50_slope10"]=x.ma50/x.ma50.shift(10)-1
    d=c.diff(); gain=d.clip(lower=0); loss=-d.clip(upper=0); ag=gain.ewm(alpha=1/14,adjust=False,min_periods=14).mean(); al=loss.ewm(alpha=1/14,adjust=False,min_periods=14).mean(); rs=ag/al.replace(0,np.nan); x["rsi14"]=100-100/(1+rs)
    lo9=l.rolling(9,min_periods=9).min(); hi9=h.rolling(9,min_periods=9).max(); rsv=100*(c-lo9)/(hi9-lo9).replace(0,np.nan); x["k"]=rsv.ewm(alpha=1/3,adjust=False,min_periods=9).mean(); x["d"]=x.k.ewm(alpha=1/3,adjust=False,min_periods=9).mean(); x["j"]=3*x.k-2*x.d
    rng=(h-l).replace(0,np.nan); x["body_frac"]=(c-o).abs()/rng; x["close_loc"]=(c-l)/rng
    for n in [1,3,5,10,20,30,60]: x[f"ret{n}"]=c.pct_change(n) if n==1 else c/c.shift(n)-1
    x["vol_med20"]=v.rolling(20,min_periods=20).median(); x["vol_ratio20"]=v/x.vol_med20
    x["taker_imb"]=(2*x.taker_buy_quote-x.quote_volume)/x.quote_volume.replace(0,np.nan)
    x["taker_proxy_label"]="SPOT_TAKER_PROXY_NOT_TRUE_CVD"
    for n in [3,5,10,20,30,60]: x[f"prior_high{n}"]=h.shift(1).rolling(n,min_periods=n).max(); x[f"prior_low{n}"]=l.shift(1).rolling(n,min_periods=n).min()
    x["dd_from_prior30h"]=c/x.prior_high30-1; x["up_from_prior30l"]=c/x.prior_low30-1
    x["reclaim_low20"]=(l<x.prior_low20)&(c>x.prior_low20); x["reject_high20"]=(h>x.prior_high20)&(c<x.prior_high20)
    x["break_high3"]=c>x.prior_high3; x["break_low3"]=c<x.prior_low3; x["break_high5"]=c>x.prior_high5; x["break_low5"]=c<x.prior_low5
    x["ema20_reclaim"]=(c>x.ema20)&(c.shift(1)<=x.ema20.shift(1)); x["ema20_loss"]=(c<x.ema20)&(c.shift(1)>=x.ema20.shift(1))
    x["pullback_from20h"]=c/x.prior_high20-1; x["rebound_from20l"]=c/x.prior_low20-1
    return x


def add_r12(x: pd.DataFrame) -> pd.DataFrame:
    x=x.copy(); x["ma200_slope20"]=x.ma200/x.ma200.shift(20)-1
    x["prior_rsi_max20"]=x.rsi14.shift(1).rolling(20,min_periods=20).max(); x["prior_rsi_min20"]=x.rsi14.shift(1).rolling(20,min_periods=20).min(); x["prior_taker_med10"]=x.taker_imb.shift(1).rolling(10,min_periods=10).median()
    x["pullback_from60h"]=x.close/x.prior_high60-1; x["rebound_from60l"]=x.close/x.prior_low60-1
    x["near_high20"]=x.high>=.985*x.prior_high20; x["near_low20"]=x.low<=1.015*x.prior_low20
    x["rsi_bear_div"]=x.near_high20&(x.rsi14<=x.prior_rsi_max20-5); x["rsi_bull_div"]=x.near_low20&(x.rsi14>=x.prior_rsi_min20+5)
    x["flow_bear_div"]=x.near_high20&(x.taker_imb<=x.prior_taker_med10-.02); x["flow_bull_div"]=x.near_low20&(x.taker_imb>=x.prior_taker_med10+.02)
    x["range_atr"]=(x.high-x.low)/x.atr14.replace(0,np.nan); x["bull_disp"]=(x.close>x.open)&(x.body_frac>=.45)&(x.close_loc>=.70)&(x.range_atr>=.90); x["bear_disp"]=(x.close<x.open)&(x.body_frac>=.45)&(x.close_loc<=.30)&(x.range_atr>=.90)
    x["macro_bull"]=(x.close>x.ma200)&(x.ma200_slope20>0); x["macro_bear"]=(x.close<x.ma200)&(x.ma200_slope20<0); x["mid_bull"]=(x.ema20>x.ma50)&(x.close>x.ma50); x["mid_bear"]=(x.ema20<x.ma50)&(x.close<x.ma50)
    x["long_overextended"]=(x.ret20>.22)|(x.rsi14>72); x["short_overextended"]=(x.ret20<-.22)|(x.rsi14<28)
    x["up_transition"]=x.ema20_reclaim|x.break_high3; x["down_transition"]=x.ema20_loss|x.break_low3
    x["up_transition_strong"]=x.break_high5|(x.break_high3&(x.taker_imb>=.02))|x.bull_disp; x["down_transition_strong"]=x.break_low5|(x.break_low3&(x.taker_imb<=-.02))|x.bear_disp
    x["FALSE_TOP_RISK"]=((x.ret20>.10)&(x.ema20_slope5>0)&(~x.down_transition))|(x.reject_high20&(~x.down_transition)&(x.taker_imb>-.03))
    x["FALSE_BOTTOM_RISK"]=((x.ret20<-.10)&(x.ema20_slope5<0)&(~x.up_transition))|(x.reclaim_low20&(~x.up_transition)&(x.taker_imb<.03))
    return x


def stage_from_score(s):
    return np.select([s>=80,s>=60,s>=40],[3,2,1],default=0).astype(int)


def score(x: pd.DataFrame) -> pd.DataFrame:
    x=x.copy(); neg=x.taker_imb<=-.03; pos=x.taker_imb>=.03; hv=x.vol_ratio20>=1.25
    lr_ctx=(x.dd_from_prior30h<=-.12)|((x.close<x.ma50)&(x.ret20<=-.08))|((x.rsi14<=38)&(x.close<x.ema20)); lr_re=x.reclaim_low20|x.bull_disp|((x.close>x.open)&(x.close_loc>=.65)&(x.body_frac>=.30)); lr_tr=x.up_transition; lr_st=x.up_transition_strong; lr_ex=(x.rsi14<=35)|x.rsi_bull_div|x.flow_bull_div; lr_q=5*lr_ex.astype(int)+5*hv.astype(int)+5*pos.astype(int)+4*x.rsi_bull_div.astype(int); lr_struct=np.select([lr_ctx&lr_re&lr_tr&lr_st,lr_ctx&lr_re&lr_tr,lr_ctx&lr_re],[3,2,1],default=0); lr_s=np.where(lr_struct==0,np.minimum(39,20*lr_ctx.astype(int)+10*lr_ex.astype(int)),np.where(lr_struct==1,40+np.minimum(lr_q,19),np.where(lr_struct==2,60+np.minimum(lr_q,19),80+np.minimum(lr_q,20)))); lr_s=np.where(x.FALSE_BOTTOM_RISK,np.minimum(lr_s,49),lr_s)
    sr_ctx=(x.up_from_prior30l>=.15)|((x.close>x.ma50)&(x.ret20>=.10))|((x.rsi14>=65)&(x.close>x.ema20)); sr_re=x.reject_high20|x.bear_disp|((x.close<x.open)&(x.close_loc<=.35)&(x.body_frac>=.30)); sr_tr=x.down_transition; sr_st=x.down_transition_strong; sr_ex=(x.rsi14>=70)|x.rsi_bear_div|x.flow_bear_div; sr_q=5*sr_ex.astype(int)+5*hv.astype(int)+5*neg.astype(int)+4*x.rsi_bear_div.astype(int); sr_struct=np.select([sr_ctx&sr_re&sr_tr&sr_st,sr_ctx&sr_re&sr_tr,sr_ctx&sr_re],[3,2,1],default=0); sr_s=np.where(sr_struct==0,np.minimum(39,20*sr_ctx.astype(int)+10*sr_ex.astype(int)),np.where(sr_struct==1,40+np.minimum(sr_q,19),np.where(sr_struct==2,60+np.minimum(sr_q,19),80+np.minimum(sr_q,20)))); sr_s=np.where(x.FALSE_TOP_RISK,np.minimum(sr_s,49),sr_s)
    lc_parent=x.macro_bull|x.mid_bull; lc_reset=((x.pullback_from20h<=-.05)&(x.pullback_from20h>=-.18))|((x.pullback_from60h<=-.08)&(x.pullback_from60h>=-.30)); lc_tr=x.up_transition|x.bull_disp; lc_st=x.up_transition_strong; lc_q=5*(x.vol_ratio20>=1.15).astype(int)+5*(x.taker_imb>=.02).astype(int)+5*((x.rsi14>=45)&(x.rsi14<=68)).astype(int)+4*(x.ma50_slope10>0).astype(int); lc_struct=np.select([lc_parent&lc_reset&lc_tr&lc_st,lc_parent&lc_reset&lc_tr,lc_parent&lc_reset],[3,2,1],default=0); lc_s=np.where(lc_struct==0,np.minimum(39,20*lc_parent.astype(int)+10*lc_reset.astype(int)),np.where(lc_struct==1,40+np.minimum(lc_q,19),np.where(lc_struct==2,60+np.minimum(lc_q,19),80+np.minimum(lc_q,20)))); lc_s=np.where(x.mid_bull&(~x.macro_bull)&(x.ret60<=.05),np.minimum(lc_s,69),lc_s); lc_s=np.where(x.long_overextended,np.minimum(lc_s,64),lc_s)
    sc_parent=x.macro_bear|x.mid_bear; sc_reset=((x.rebound_from20l>=.05)&(x.rebound_from20l<=.20))|((x.rebound_from60l>=.10)&(x.rebound_from60l<=.50)); sc_tr=x.down_transition|x.bear_disp; sc_st=x.down_transition_strong; sc_q=5*(x.vol_ratio20>=1.15).astype(int)+5*(x.taker_imb<=-.02).astype(int)+5*((x.rsi14>=32)&(x.rsi14<=55)).astype(int)+4*((x.ma50_slope10<0)|x.macro_bear).astype(int); sc_struct=np.select([sc_parent&sc_reset&sc_tr&sc_st,sc_parent&sc_reset&sc_tr,sc_parent&sc_reset],[3,2,1],default=0); sc_s=np.where(sc_struct==0,np.minimum(39,20*sc_parent.astype(int)+10*sc_reset.astype(int)),np.where(sc_struct==1,40+np.minimum(sc_q,19),np.where(sc_struct==2,60+np.minimum(sc_q,19),80+np.minimum(sc_q,20)))); sc_s=np.where(x.mid_bear&(~x.macro_bear)&(x.ret60>=-.05),np.minimum(sc_s,69),sc_s); sc_s=np.where(x.short_overextended,np.minimum(sc_s,64),sc_s)
    for e,s,struct in [("LR",lr_s,lr_struct),("LC",lc_s,lc_struct),("SR",sr_s,sr_struct),("SC",sc_s,sc_struct)]:
        x[f"{e}_STRUCT_STAGE"]=struct.astype(int); x[f"{e}_SCORE"]=np.asarray(s,dtype=float); x[f"{e}_STAGE"]=stage_from_score(x[f"{e}_SCORE"]); x[f"{e}_eligible"]=x[f"{e}_STAGE"]>=1
    x["LR_execution_eligible"]=x.LR_STAGE>=1; x["SR_execution_eligible"]=x.SR_STAGE>=1; x["LC_execution_eligible"]=x.LC_STAGE>=2; x["SC_execution_eligible"]=x.SC_STAGE>=2
    x["LR_new_risk_ok"]=x.LR_execution_eligible&(~x.FALSE_BOTTOM_RISK); x["SR_new_risk_ok"]=x.SR_execution_eligible&(~x.FALSE_TOP_RISK); x["LC_new_risk_ok"]=x.LC_execution_eligible&(~x.long_overextended); x["SC_new_risk_ok"]=x.SC_execution_eligible&(~x.short_overextended)
    x["TRANSITION_CONFLICT"]=(np.maximum(x.LR_STAGE,x.LC_STAGE)>=2)&(np.maximum(x.SR_STAGE,x.SC_STAGE)>=2)
    return x


def build(df): return score(add_r12(add_core(df)))


def pit_prefix_test(raw,full):
    tests=[]; checkpoints=["2020-12-31","2021-12-31","2022-12-31","2023-12-31","2024-12-31","2025-12-31","2026-09-04"]
    cols=[f"{e}_{k}" for e in ENGINES for k in ["SCORE","STAGE"]]
    for ds in checkpoints:
        t=pd.Timestamp(ds,tz="UTC"); sub=raw[pd.to_datetime(raw.open_time,unit="ms",utc=True)<=t].copy()
        if not len(sub): continue
        p=build(sub); n=min(30,len(p)); a=full.iloc[len(p)-n:len(p)][cols].reset_index(drop=True); b=p.iloc[-n:][cols].reset_index(drop=True)
        ok=True; maxdiff=0.0
        for c in cols:
            if c.endswith("SCORE"): maxdiff=max(maxdiff,float(np.nanmax(np.abs(a[c].to_numpy(float)-b[c].to_numpy(float))))) if n else 0; ok=ok and bool(np.allclose(a[c],b[c],equal_nan=True,rtol=1e-12,atol=1e-12))
            else: ok=ok and bool((a[c].to_numpy()==b[c].to_numpy()).all())
        tests.append({"checkpoint":ds,"rows":len(p),"tail_rows_compared":n,"pass":ok,"max_score_abs_diff":maxdiff})
    return tests


def main():
    if not AUDIT.exists(): raise RuntimeError("Step1 audit missing")
    a=json.loads(AUDIT.read_text(encoding="utf-8"))
    if a.get("data_integrity")!="PASS": raise RuntimeError("STEP1 DATA_INTEGRITY is not PASS; Step2 blocked")
    raw=pd.read_csv(IN); x=build(raw)
    score_ok=all(((x[f"{e}_SCORE"]>=0)&(x[f"{e}_SCORE"]<=100)).all() for e in ENGINES)
    stage_band_ok=all((((x[f"{e}_SCORE"]<40)&(x[f"{e}_STAGE"]==0))|((x[f"{e}_SCORE"].between(40,59))&(x[f"{e}_STAGE"]==1))|((x[f"{e}_SCORE"].between(60,79))&(x[f"{e}_STAGE"]==2))|((x[f"{e}_SCORE"]>=80)&(x[f"{e}_STAGE"]==3))).all() for e in ENGINES)
    pit=pit_prefix_test(raw,x); pit_ok=all(r["pass"] for r in pit)
    counts={e:{"daily_signal_n":int(x[f"{e}_eligible"].sum()),"execution_eligible_n":int(x[f"{e}_execution_eligible"].sum()),"stage0":int((x[f"{e}_STAGE"]==0).sum()),"stage1":int((x[f"{e}_STAGE"]==1).sum()),"stage2":int((x[f"{e}_STAGE"]==2).sum()),"stage3":int((x[f"{e}_STAGE"]==3).sum())} for e in ENGINES}
    cols=["open_time","date","close","taker_proxy_label","TRANSITION_CONFLICT"]+sum(([f"{e}_SCORE",f"{e}_STAGE",f"{e}_STRUCT_STAGE",f"{e}_eligible",f"{e}_execution_eligible",f"{e}_new_risk_ok"] for e in ENGINES),[])
    x[cols].to_csv(OUT/"r12_full_daily_scores.csv",index=False)
    summary={"engine":ENGINE,"status":STATUS,"protocol":"VALIDATION_PROTOCOL_V1_0_FINAL_LOCK","step":"2_R1_2_FULL_DAILY_ROLLING_SCORING","rows":len(x),"start":x.date.iloc[0].isoformat(),"end":x.date.iloc[-1].isoformat(),"score_is_probability":False,"probability":"확률 산출보류","fractal_in_core_score":False,"score_range_pass":score_ok,"stage_band_pass":stage_band_ok,"point_in_time_prefix_invariance":{"pass":pit_ok,"tests":pit},"daily_counts":counts,"transition_conflict_days":int(x.TRANSITION_CONFLICT.sum()),"dedupe_performed":False,"outcome_labeling_performed":False,"step2":"PASS" if score_ok and stage_band_ok and pit_ok else "HOLD","next_step":"STEP3_ZONE_FAMILY_EPISODE_DEDUPE" if score_ok and stage_band_ok and pit_ok else "STOP_AND_AUDIT_SCORING","v2_6_modified":False,"promotion":"HOLD"}
    (OUT/"audit.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
