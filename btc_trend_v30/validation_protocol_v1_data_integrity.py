from __future__ import annotations

import hashlib, io, json, math, time, zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ENGINE = "MASTER_BTC_TREND_V3_R1_2_VALIDATION_PROTOCOL_V1"
STATUS = "RESEARCH_ONLY_PROMOTION_HOLD"
AUDIT_IMPL = "1.0.2"
SYMBOL, INTERVAL = "BTCUSDT", "1d"
START = pd.Timestamp("2017-08-17T00:00:00Z")
CUTOFF = pd.Timestamp("2026-09-04T00:00:00Z")
DAY_MS = 86_400_000
EXPECTED_CLOSE_DELTA_MS = DAY_MS - 1
OUT = Path("btc_trend_v30/output/validation_v1/data_integrity")
OUT.mkdir(parents=True, exist_ok=True)

COLS = ["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]
REQUIRED = ["open_time","open","high","low","close","volume","quote_volume","taker_buy_quote"]
ARCHIVE_BASE = "https://data.binance.vision/data/spot"
API_BASE = "https://api.binance.com/api/v3/klines"
S = requests.Session()
S.headers.update({"User-Agent":"master-btc-trend-v30-validation-v1/1.0.2"})


@dataclass
class DownloadRecord:
    kind: str; key: str; url: str; checksum_url: str
    http_status: int | None; checksum_http_status: int | None
    checksum_expected: str | None; checksum_actual: str | None
    checksum_ok: bool; rows: int; error: str | None = None


def norm_ms(s: pd.Series) -> pd.Series:
    """Binance Spot archive uses microseconds from 2025-01-01; floor to ms."""
    x = pd.Series(pd.array(pd.to_numeric(s, errors="coerce"), dtype="Int64"), index=s.index)
    m = x.notna() & (x > 100_000_000_000_000)
    if m.any(): x.loc[m] = pd.array(x.loc[m].astype("int64") // 1000, dtype="Int64")
    return x.astype("Int64")


def numeric(raw: pd.DataFrame) -> pd.DataFrame:
    x = raw.copy(); x["open_time"] = norm_ms(x.open_time); x["close_time"] = norm_ms(x.close_time)
    for c in ["open","high","low","close","volume","quote_volume","taker_buy_quote","trades"]:
        x[c] = pd.to_numeric(x[c], errors="coerce")
    return x


def get(url: str, attempts=4):
    last = None
    for k in range(attempts):
        try:
            r = S.get(url, timeout=60); last = r
            if r.status_code in (200,404,451): return r
        except Exception as e: last = e
        time.sleep(1.5*(k+1))
    if isinstance(last, requests.Response): return last
    raise RuntimeError(f"GET failed {url}: {last}")


def checked_zip(kind: str, key: str, url: str):
    cu = url + ".CHECKSUM"
    try:
        r, cr = get(url), get(cu)
        if r.status_code != 200:
            return None, DownloadRecord(kind,key,url,cu,r.status_code,cr.status_code,None,None,False,0,f"HTTP_{r.status_code}")
        exp = cr.text.strip().split()[0].lower() if cr.status_code == 200 and cr.text.strip() else None
        act = hashlib.sha256(r.content).hexdigest(); ok = bool(exp and len(exp)==64 and exp==act)
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not names: raise RuntimeError("zip contains no CSV")
            df = numeric(pd.read_csv(z.open(names[0]), header=None, names=COLS))
        return df, DownloadRecord(kind,key,url,cu,r.status_code,cr.status_code,exp,act,ok,len(df),None if ok else "CHECKSUM_FAIL")
    except Exception as e:
        return None, DownloadRecord(kind,key,url,cu,None,None,None,None,False,0,f"{type(e).__name__}:{e}")


def months():
    cur = pd.Timestamp(year=START.year,month=START.month,day=1,tz="UTC")
    last = pd.Timestamp(year=CUTOFF.year,month=CUTOFF.month,day=1,tz="UTC")
    while cur <= last:
        yield cur; cur = cur + pd.offsets.MonthBegin(1)


def needed_days(m):
    m2=m+pd.offsets.MonthBegin(1); lo=max(START,m); hi=min(CUTOFF,m2-pd.Timedelta(days=1))
    return [] if hi<lo else list(pd.date_range(lo.normalize(),hi.normalize(),freq="D",tz="UTC"))


def load_archive():
    frames=[]; recs=[]
    for m in months():
        ym=m.strftime("%Y-%m"); u=f"{ARCHIVE_BASE}/monthly/klines/{SYMBOL}/1d/{SYMBOL}-1d-{ym}.zip"
        df,r=checked_zip("monthly_1d",ym,u); recs.append(r)
        if df is not None and r.checksum_ok: frames.append(df); continue
        for d in needed_days(m):
            ds=d.strftime("%Y-%m-%d"); du=f"{ARCHIVE_BASE}/daily/klines/{SYMBOL}/1d/{SYMBOL}-1d-{ds}.zip"
            q,rr=checked_zip("daily_1d",ds,du); recs.append(rr)
            if q is not None and rr.checksum_ok: frames.append(q)
    if not frames: raise RuntimeError("no checksum-verified 1D source")
    x=pd.concat(frames,ignore_index=True); lo=int(START.timestamp()*1000); hi=int(CUTOFF.timestamp()*1000)
    return x[(x.open_time>=lo)&(x.open_time<=hi)].sort_values(["open_time","close_time"]).reset_index(drop=True), recs


def audit_rows(x):
    z=x.copy(); z["date"]=pd.to_datetime(z.open_time.astype("int64"),unit="ms",utc=True)
    z["close_delta_ms"]=z.close_time.astype("int64")-z.open_time.astype("int64")
    z["complete_1d_metadata"]=z.close_delta_ms==EXPECTED_CLOSE_DELTA_MS
    eps=1e-10
    z["row_core_ok"]=(
        (z.open_time.astype("int64")%DAY_MS==0) & (z.low<=z.high+eps) & (z.low<=z.open+eps) &
        (z.low<=z.close+eps) & (z.high+eps>=z.open) & (z.high+eps>=z.close) &
        (z.volume>=-eps) & (z.quote_volume>=-eps) & (z.taker_buy_quote>=-eps) &
        (z.taker_buy_quote<=z.quote_volume+np.maximum(1e-6,np.abs(z.quote_volume)*1e-10)) &
        z[["open","high","low","close","volume","quote_volume","taker_buy_quote"]].notna().all(axis=1)
    )
    return z


def repair_short_metadata(audited,recs):
    """
    A short close_time can be a historical exchange inactive-tail day, not an unfinished research candle.
    For each such date, aggregate ALL rows for that UTC date from the checksum-verified monthly 1m archive.
    The monthly file is the complete archive container, so absence of later 1m rows is itself historical no-trade evidence.
    """
    z=audited.copy(); z["provenance"]="BINANCE_1D_ARCHIVE"; logs=[]; cache={}
    for idx,r in z[~z.complete_1d_metadata].iterrows():
        day=pd.Timestamp(r.date).normalize(); ym=day.strftime("%Y-%m")
        if ym not in cache:
            u=f"{ARCHIVE_BASE}/monthly/klines/{SYMBOL}/1m/{SYMBOL}-1m-{ym}.zip"
            q,rr=checked_zip("monthly_1m_exception",ym,u); recs.append(rr); cache[ym]=q if q is not None and rr.checksum_ok else None
        q=cache[ym]
        log={"date":day.isoformat(),"original_close_delta_ms":int(r.close_delta_ms),"repair_ok":False,"minute_bars":0,"first_minute":None,"last_minute":None,"changed_required_fields":None,"classification":None}
        if q is None or q.empty:
            log["classification"]="REPAIR_SOURCE_UNAVAILABLE"; logs.append(log); continue
        lo=int(day.timestamp()*1000); hi=lo+DAY_MS; d=q[(q.open_time>=lo)&(q.open_time<hi)].sort_values("open_time")
        if d.empty:
            log["classification"]="NO_1M_ROWS"; logs.append(log); continue
        agg={"open":float(d.iloc[0].open),"high":float(d.high.max()),"low":float(d.low.min()),"close":float(d.iloc[-1].close),"volume":float(d.volume.sum()),"quote_volume":float(d.quote_volume.sum()),"taker_buy_quote":float(d.taker_buy_quote.sum())}
        changed=[c for c,v in agg.items() if not math.isclose(float(r[c]),v,rel_tol=1e-9,abs_tol=1e-8)]
        for c,v in agg.items(): z.at[idx,c]=v
        z.at[idx,"close_time"]=lo+EXPECTED_CLOSE_DELTA_MS; z.at[idx,"provenance"]="RECONSTRUCTED_UTC_DAY_FROM_CHECKED_1M"
        first=int(d.iloc[0].open_time); last=int(d.iloc[-1].open_time)
        log.update({"repair_ok":True,"minute_bars":int(len(d)),"first_minute":pd.to_datetime(first,unit="ms",utc=True).isoformat(),"last_minute":pd.to_datetime(last,unit="ms",utc=True).isoformat(),"changed_required_fields":",".join(changed),"classification":"HISTORICAL_INACTIVE_TAIL" if last<hi-60_000 else "NORMAL_FULL_DAY"})
        logs.append(log)
    y=audit_rows(z); y["provenance"]=z.provenance.values
    return y,pd.DataFrame(logs)


def coverage(x):
    exp=set(pd.date_range(START.normalize(),CUTOFF.normalize(),freq="D",tz="UTC")); act=set(pd.DatetimeIndex(pd.to_datetime(x.open_time.astype("int64"),unit="ms",utc=True)).normalize())
    return sorted(d.isoformat() for d in exp-act),sorted(d.isoformat() for d in act-exp),int(x.open_time.duplicated().sum())


def disallowed_downloads(df,repairs):
    allowed_monthly=set()
    for m in months():
        ym=m.strftime("%Y-%m"); mr=df[(df.kind=="monthly_1d")&(df.key==ym)]
        if len(mr) and not bool(mr.iloc[-1].checksum_ok):
            need=[d.strftime("%Y-%m-%d") for d in needed_days(m)]
            got=df[(df.kind=="daily_1d")&df.key.isin(need)&df.checksum_ok]
            if len(got)==len(need): allowed_monthly.add(ym)
    bad=[]
    for _,r in df[~df.checksum_ok].iterrows():
        if r.kind=="monthly_1d" and r.key in allowed_monthly: continue
        if r.kind=="monthly_1m_exception":
            fail=False
            if len(repairs): fail=bool(((pd.to_datetime(repairs.date,utc=True).dt.strftime("%Y-%m")==r.key)&(~repairs.repair_ok.fillna(False))).any())
            if not fail: continue
        bad.append({"kind":r.kind,"key":r.key,"error":r.error})
    return bad


def api_diag(final):
    """Optional diagnostic only; GitHub runner can return HTTP451 by region."""
    rows=[]; lo=int(START.timestamp()*1000); end=int((CUTOFF+pd.Timedelta(days=1)).timestamp()*1000); cur=lo
    try:
        while cur<end:
            r=S.get(API_BASE,params={"symbol":SYMBOL,"interval":"1d","startTime":cur,"endTime":end-1,"limit":1000},timeout=45)
            if r.status_code!=200: raise RuntimeError(f"HTTP {r.status_code}: {r.text[:180]}")
            b=r.json()
            if not b: break
            rows.extend(b); cur=int(b[-1][0])+DAY_MS
        api=numeric(pd.DataFrame(rows,columns=COLS)); mism=[]
        untouched=final[final.provenance=="BINANCE_1D_ARCHIVE"]
        mm=untouched.merge(api,on="open_time",suffixes=("_a","_b"))
        for c in REQUIRED[1:]:
            a=mm[f"{c}_a"].astype(float); b=mm[f"{c}_b"].astype(float)
            bad=~np.isclose(a,b,rtol=2e-9,atol=np.maximum(1e-8,np.abs(b)*2e-9))
            if bad.any(): mism.extend([(int(t),c) for t in mm.loc[bad,"open_time"]])
        return {"status":"PASS" if not mism and len(mm)==len(untouched) else "FAIL","rows":len(api),"compared_untouched_rows":len(mm),"mismatch_count":len(mism),"error":None}, not mism and len(mm)==len(untouched)
    except Exception as e:
        return {"status":"N/A_ENV_RESTRICTED_OR_UNAVAILABLE","rows":0,"compared_untouched_rows":0,"mismatch_count":0,"error":f"{type(e).__name__}:{e}"}, True


def main():
    raw,recs=load_archive(); ra=audit_rows(raw); miss0,extra0,dup0=coverage(ra)
    final,repairs=repair_short_metadata(ra,recs); miss,extra,dup=coverage(final)
    final=final.sort_values("open_time").drop_duplicates("open_time",keep=False).copy()
    dl=pd.DataFrame([asdict(r) for r in recs]); bad_dl=disallowed_downloads(dl,repairs)
    unrepaired=final[~final.complete_1d_metadata]; core_bad=final[~final.row_core_ok]
    exp_rows=int((CUTOFF.normalize()-START.normalize()).days+1); req_null=int(final[REQUIRED].isna().sum().sum())
    checks={"expected_rows":len(final)==exp_rows,"date_coverage":not miss and not extra,"duplicates_zero":dup==0,"required_nonnull_zero":req_null==0,"row_logic_pass":len(core_bad)==0,"short_metadata_resolved":len(unrepaired)==0,"checksums_pass":len(bad_dl)==0,"cutoff_exact":len(final)>0 and int(final.open_time.min())==int(START.timestamp()*1000) and int(final.open_time.max())==int(CUTOFF.timestamp()*1000)}
    api,api_gate=api_diag(final)
    if api["status"]=="FAIL": checks["api_untouched_match"]=False
    passed=all(checks.values()) and api_gate
    final["date"]=pd.to_datetime(final.open_time.astype("int64"),unit="ms",utc=True); final["taker_proxy_quote"]=2*final.taker_buy_quote-final.quote_volume; final["taker_proxy_name"]="SPOT_TAKER_PROXY_NOT_TRUE_CVD"
    final[REQUIRED+["date","taker_proxy_quote","taker_proxy_name","provenance"]].to_csv(OUT/"btc_usdt_1d_matrix.csv",index=False)
    ra[~ra.complete_1d_metadata].to_csv(OUT/"raw_short_metadata_rows.csv",index=False); repairs.to_csv(OUT/"exception_repairs.csv",index=False); dl.to_csv(OUT/"download_checksum_manifest.csv",index=False)
    sha=hashlib.sha256(final[REQUIRED].to_csv(index=False).encode()).hexdigest()
    summary={"engine":ENGINE,"status":STATUS,"protocol":"VALIDATION_PROTOCOL_V1_0_FINAL_LOCK","audit_impl":AUDIT_IMPL,"step":"1_DATA_INTEGRITY","start_utc":START.isoformat(),"cutoff_utc_inclusive":CUTOFF.isoformat(),"expected_rows":exp_rows,"final_rows":len(final),"matrix_sha256_required_fields":sha,"raw":{"missing":miss0,"extra":extra0,"duplicates":dup0,"short_metadata_rows":int((~ra.complete_1d_metadata).sum()),"short_metadata_dates":[pd.Timestamp(x).isoformat() for x in ra.loc[~ra.complete_1d_metadata,"date"]]},"repairs":{"attempted":len(repairs),"passed":int(repairs.repair_ok.fillna(False).sum()) if len(repairs) else 0,"failed":int((~repairs.repair_ok.fillna(False)).sum()) if len(repairs) else 0,"records":repairs.to_dict("records") if len(repairs) else []},"final_audit":{"missing":miss,"extra":extra,"duplicates":dup,"required_nonnull_cells":req_null,"row_logic_failures":len(core_bad),"unresolved_short_metadata":len(unrepaired),"disallowed_download_failures":bad_dl,"checks":checks},"api_crosscheck":{"policy":"diagnostic extension; not required by FINAL Protocol V1.0 when endpoint is environment-blocked",**api},"data_integrity":"PASS" if passed else "HOLD","next_step":"R1.2 LR/LC/SR/SC FULL DAILY ROLLING" if passed else "STOP_BEFORE_FULL_ROLLING","probability":"확률 산출보류","v2_6_modified":False,"promotion":"HOLD"}
    (OUT/"audit.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),encoding="utf-8"); print(json.dumps(summary,ensure_ascii=False,indent=2,default=str))

if __name__=="__main__": main()
