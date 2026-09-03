from __future__ import annotations

import argparse
import csv
import json
import math
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "market_vault"
DATA_DIR = BASE / "data"
OUT_DIR = BASE / "output"
STATE_DIR = BASE / "state"
CONFIG_PATH = BASE / "config.json"
HISTORY_PATH = DATA_DIR / "market_history.csv"
SUMMARY_PATH = OUT_DIR / "latest_summary.json"
STATE_PATH = STATE_DIR / "vault_state.json"

TREASURY_XML = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
NYFED_LATEST = "https://markets.newyorkfed.org/api/rates/all/latest.json"
FISCAL_TGA = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/dts/operating_cash_balance"
DEFILLAMA_STABLES = "https://stablecoins.llama.fi/stablecoins"
COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"
HEADERS = {"User-Agent":"MASTER-MARKET-DATA-VAULT/1.2"}
FIELDS = ["snapshot_hour_utc","retrieved_at_utc","metric","value","unit","source","source_observation_time","source_frequency","timestamp_quality","status"]


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00","Z")


def parse_iso(v: str | None) -> datetime | None:
    if not v: return None
    try: return datetime.fromisoformat(v.replace("Z","+00:00"))
    except Exception: return None


def safe_float(v: Any) -> float | None:
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except (TypeError,ValueError): return None


def now_hour() -> datetime:
    n=datetime.now(UTC)
    return n.replace(minute=0,second=0,microsecond=0)


def load_config() -> dict[str,Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def get(url: str, params: dict[str,Any] | None=None, timeout: int=20) -> requests.Response:
    last=None
    for i in range(3):
        try:
            r=requests.get(url,params=params,headers=HEADERS,timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last=e
            if i<2: time.sleep(0.8*(i+1))
    raise RuntimeError(str(last))


def local_name(tag: str) -> str:
    return tag.rsplit('}',1)[-1] if '}' in tag else tag


def treasury_latest(data_key: str, field_map: dict[str,str]) -> list[dict[str,Any]]:
    year=datetime.now(UTC).year
    r=get(TREASURY_XML,{"data":data_key,"field_tdr_date_value":str(year)},20)
    root=ET.fromstring(r.content)
    best_date: datetime | None=None
    best: dict[str,str]={}
    for elem in root.iter():
        if local_name(elem.tag)!="properties": continue
        vals={local_name(c.tag):(c.text or '').strip() for c in list(elem)}
        d=parse_iso(vals.get('NEW_DATE'))
        if d is None: continue
        if best_date is None or d>best_date:
            best_date=d; best=vals
    if best_date is None: raise RuntimeError(f"no Treasury observations for {data_key}")
    out=[]
    for metric,tag in field_map.items():
        v=safe_float(best.get(tag))
        if v is not None:
            out.append({"metric":metric,"value":v,"unit":"percent","source":f"US Treasury:{data_key}","source_observation_time":iso(best_date),"source_frequency":"daily","timestamp_quality":"official_observation_date","status":"OK"})
    return out


def nyfed_latest() -> list[dict[str,Any]]:
    doc=get(NYFED_LATEST,timeout=15).json()
    rows=doc.get('refRates',[]) if isinstance(doc,dict) else []
    out=[]
    for typ in ('EFFR','SOFR'):
        candidates=[x for x in rows if isinstance(x,dict) and x.get('type')==typ and safe_float(x.get('percentRate')) is not None]
        if not candidates: continue
        candidates.sort(key=lambda x:str(x.get('effectiveDate') or ''),reverse=True)
        x=candidates[0]; d=str(x.get('effectiveDate'))
        out.append({"metric":typ,"value":safe_float(x.get('percentRate')),"unit":"percent","source":"New York Fed Markets API","source_observation_time":d+'T00:00:00Z',"source_frequency":"business_daily","timestamp_quality":"official_effective_date","status":"OK"})
    return out


def fiscal_tga_latest() -> list[dict[str,Any]]:
    params={"sort":"-record_date","page[size]":"100","fields":"record_date,account_type,close_today_bal,open_today_bal","format":"json"}
    doc=get(FISCAL_TGA,params,20).json()
    rows=doc.get('data',[]) if isinstance(doc,dict) else []
    if not rows: raise RuntimeError('FiscalData returned no TGA rows')
    dates=sorted({str(r.get('record_date')) for r in rows if r.get('record_date')},reverse=True)
    for d in dates:
        same=[r for r in rows if str(r.get('record_date'))==d]
        preferred=[r for r in same if 'treasury general account' in str(r.get('account_type') or '').lower() and 'closing' in str(r.get('account_type') or '').lower()]
        candidates=preferred or [r for r in same if 'treasury general account' in str(r.get('account_type') or '').lower()]
        for r in candidates:
            # FiscalData's Table I layout may populate the closing-balance row in open_today_bal; prefer explicit close field then fallback.
            v=safe_float(r.get('close_today_bal'))
            if v is None: v=safe_float(r.get('open_today_bal'))
            if v is not None:
                return [{"metric":"TGA_CLOSING_BALANCE","value":v,"unit":"USD millions","source":"US Treasury FiscalData:DTS operating_cash_balance","source_observation_time":d+'T00:00:00Z',"source_frequency":"business_daily","timestamp_quality":"official_record_date","status":"OK"}]
    raise RuntimeError('no numeric TGA closing balance found')


def stablecoin_metrics() -> list[dict[str,Any]]:
    data=get(DEFILLAMA_STABLES,{"includePrices":"true"},15).json()
    assets=data.get('peggedAssets',[]) if isinstance(data,dict) else []
    wanted={"USDT":None,"USDC":None}; total=0.0
    for a in assets:
        if not isinstance(a,dict): continue
        circ=a.get('circulating') or {}
        usd=safe_float(circ.get('peggedUSD')) if isinstance(circ,dict) else None
        if usd is None: continue
        total+=usd
        sym=str(a.get('symbol') or '').upper()
        if sym in wanted: wanted[sym]=usd
    obs=iso(datetime.now(UTC)); out=[]
    for sym in ('USDT','USDC'):
        if wanted[sym] is not None:
            out.append({"metric":f"{sym}_SUPPLY","value":wanted[sym],"unit":"USD","source":"DefiLlama Stablecoins","source_observation_time":obs,"source_frequency":"current","timestamp_quality":"retrieval_time_proxy","status":"OK"})
    if total>0:
        out.append({"metric":"STABLECOIN_TOTAL_SUPPLY","value":total,"unit":"USD","source":"DefiLlama Stablecoins","source_observation_time":obs,"source_frequency":"current","timestamp_quality":"retrieval_time_proxy","status":"OK"})
    return out


def coingecko_metrics() -> list[dict[str,Any]]:
    doc=get(COINGECKO_GLOBAL,timeout=15).json(); d=doc.get('data') if isinstance(doc,dict) else None
    if not isinstance(d,dict): raise RuntimeError('CoinGecko global payload missing data')
    cap=d.get('total_market_cap') or {}; vol=d.get('total_volume') or {}; pct=d.get('market_cap_percentage') or {}; updated=safe_float(d.get('updated_at'))
    obs=iso(datetime.fromtimestamp(updated,tz=UTC)) if updated else iso(datetime.now(UTC))
    vals=[('CRYPTO_TOTAL_MCAP',safe_float(cap.get('usd')),'USD'),('CRYPTO_24H_VOLUME',safe_float(vol.get('usd')),'USD'),('BTC_DOMINANCE',safe_float(pct.get('btc')),'percent'),('ETH_DOMINANCE',safe_float(pct.get('eth')),'percent')]
    return [{"metric":m,"value":v,"unit":u,"source":"CoinGecko Global","source_observation_time":obs,"source_frequency":"current","timestamp_quality":"source_updated_at" if updated else "retrieval_time_proxy","status":"OK"} for m,v,u in vals if v is not None]


def read_history() -> list[dict[str,str]]:
    if not HISTORY_PATH.exists(): return []
    with HISTORY_PATH.open('r',encoding='utf-8',newline='') as f: return list(csv.DictReader(f))


def write_history(rows: list[dict[str,Any]]) -> None:
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    with HISTORY_PATH.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS); w.writeheader()
        for r in rows: w.writerow({k:r.get(k,'') for k in FIELDS})


def prior_snapshot(rows,metric,source,before):
    best=None; best_dt=None
    for r in rows:
        if r.get('metric')!=metric or r.get('source')!=source or r.get('status')!='OK' or safe_float(r.get('value')) is None: continue
        dt=parse_iso(r.get('snapshot_hour_utc'))
        if dt is None or dt>=before: continue
        if best_dt is None or dt>best_dt: best,best_dt=r,dt
    return best


def nearest_history(rows,metric,source,target,tolerance_min,before):
    best=None; gapbest=None
    for r in rows:
        if r.get('metric')!=metric or r.get('source')!=source or r.get('status')!='OK' or safe_float(r.get('value')) is None: continue
        dt=parse_iso(r.get('snapshot_hour_utc'))
        if dt is None or dt>=before: continue
        gap=abs((dt-target).total_seconds())/60
        if gap<=tolerance_min and (gapbest is None or gap<gapbest): best,gapbest=r,gap
    return best,gapbest


def delta_block(cur,prior,gap=None):
    if not prior: return None
    pv=safe_float(prior.get('value'))
    if pv is None: return None
    d=cur-pv; pct=d/abs(pv)*100 if pv else None
    return {"previous_value":pv,"delta":d,"delta_pct":pct,"previous_snapshot_hour_utc":prior.get('snapshot_hour_utc'),"source_observation_time":prior.get('source_observation_time'),"target_gap_minutes":gap}


def build_summary(history,snapshot,cfg,meta):
    current=[r for r in history if r.get('snapshot_hour_utc')==iso(snapshot) and r.get('status')=='OK' and safe_float(r.get('value')) is not None]
    t24=int(cfg.get('compare_tolerance_minutes',{}).get('24h',90)); t7=int(cfg.get('compare_tolerance_minutes',{}).get('7d',180)); metrics=[]
    for r in current:
        cur=safe_float(r.get('value'))
        if cur is None: continue
        metric,source=r.get('metric',''),r.get('source',''); prev=prior_snapshot(history,metric,source,snapshot); p24,g24=nearest_history(history,metric,source,snapshot-timedelta(hours=24),t24,snapshot); p7,g7=nearest_history(history,metric,source,snapshot-timedelta(days=7),t7,snapshot)
        obs=r.get('source_observation_time'); prevobs=prev.get('source_observation_time') if prev else None
        metrics.append({"metric":metric,"value":cur,"unit":r.get('unit'),"source":source,"source_observation_time":obs,"source_frequency":r.get('source_frequency'),"timestamp_quality":r.get('timestamp_quality'),"new_source_observation_since_prior_snapshot":bool(prev and obs and prevobs and obs!=prevobs),"vs_prior_snapshot":delta_block(cur,prev),"vs_24h":delta_block(cur,p24,g24),"vs_7d":delta_block(cur,p7,g7)})
    metrics.sort(key=lambda x:x['metric'])
    return {"engine":"MASTER_MARKET_DATA_VAULT_V1","schema_version":"1.1","collector_version":"1.2-official-keyless","snapshot_hour_utc":iso(snapshot),"generated_at_utc":iso(datetime.now(UTC)),"role":"historical comparison cache only; not a score and not a current-market substitute","comparison_rule":"same metric + same source only; repeated retrieval of the same official observation is not new movement","metrics":metrics,"collection":meta}


def collect() -> None:
    cfg=load_config(); DATA_DIR.mkdir(parents=True,exist_ok=True); OUT_DIR.mkdir(parents=True,exist_ok=True); STATE_DIR.mkdir(parents=True,exist_ok=True)
    snapshot=now_hour(); retrieved=datetime.now(UTC); rows=[]; failures={}
    adapters=[('TREASURY_NOMINAL',lambda:treasury_latest('daily_treasury_yield_curve',{'US2Y':'BC_2YEAR','US10Y':'BC_10YEAR','US30Y':'BC_30YEAR'})),('TREASURY_REAL',lambda:treasury_latest('daily_treasury_real_yield_curve',{'US10Y_REAL':'TC_10YEAR'})),('NYFED',nyfed_latest),('TGA',fiscal_tga_latest)]
    for name,fn in adapters:
        try:
            ext=fn()
            for x in ext: rows.append({"snapshot_hour_utc":iso(snapshot),"retrieved_at_utc":iso(retrieved),**x})
        except Exception as e: failures[name]=str(e)[:250]
    if cfg.get('optional_sources',{}).get('defillama_stablecoins'):
        try:
            for x in stablecoin_metrics(): rows.append({"snapshot_hour_utc":iso(snapshot),"retrieved_at_utc":iso(retrieved),**x})
        except Exception as e: failures['DEFILLAMA']=str(e)[:250]
    if cfg.get('optional_sources',{}).get('coingecko_global'):
        try:
            for x in coingecko_metrics(): rows.append({"snapshot_hour_utc":iso(snapshot),"retrieved_at_utc":iso(retrieved),**x})
        except Exception as e: failures['COINGECKO']=str(e)[:250]
    old=read_history(); keyed={(r.get('snapshot_hour_utc'),r.get('metric'),r.get('source')):r for r in old}
    for r in rows: keyed[(r.get('snapshot_hour_utc'),r.get('metric'),r.get('source'))]=r
    history=list(keyed.values()); history.sort(key=lambda r:(r.get('snapshot_hour_utc',''),r.get('metric',''),r.get('source',''))); cutoff=snapshot-timedelta(days=int(cfg.get('history_retention_days',45))); history=[r for r in history if (parse_iso(r.get('snapshot_hour_utc')) or snapshot)>=cutoff]; write_history(history)
    current_metrics={r.get('metric') for r in rows if r.get('status')=='OK'}; mandatory=list(cfg.get('mandatory_metrics',[])); mandatory_ok=sum(1 for m in mandatory if m in current_metrics); secondary=list(cfg.get('secondary_metrics',[])); secondary_ok=sum(1 for m in secondary if m in current_metrics)
    meta={"mandatory_ok":mandatory_ok,"mandatory_total":len(mandatory),"mandatory_coverage_pct":mandatory_ok/len(mandatory)*100 if mandatory else 0,"secondary_ok":secondary_ok,"secondary_total":len(secondary),"all_collected_metrics":sorted(current_metrics),"failures":failures,"history_rows":len(history)}
    summary=build_summary(history,snapshot,cfg,meta); SUMMARY_PATH.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8'); state={"engine":"MASTER_MARKET_DATA_VAULT_V1","schema_version":"1.1","collector_version":"1.2-official-keyless","last_run_utc":iso(retrieved),"snapshot_hour_utc":iso(snapshot),**meta}; STATE_PATH.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(state,ensure_ascii=False))


def self_test():
    xml=b'''<feed xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices" xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"><entry><content><m:properties><d:NEW_DATE>2026-09-01T00:00:00</d:NEW_DATE><d:BC_2YEAR>3.1</d:BC_2YEAR><d:BC_10YEAR>3.7</d:BC_10YEAR></m:properties></content></entry></feed>'''
    root=ET.fromstring(xml); vals=None
    for e in root.iter():
        if local_name(e.tag)=='properties': vals={local_name(c.tag):(c.text or '').strip() for c in list(e)}
    assert vals and vals['BC_2YEAR']=='3.1' and vals['BC_10YEAR']=='3.7'
    now=datetime(2026,9,3,5,tzinfo=UTC); rows=[{'snapshot_hour_utc':iso(now-timedelta(hours=24)),'metric':'X','source':'S','status':'OK','value':'100'},{'snapshot_hour_utc':iso(now-timedelta(hours=1)),'metric':'X','source':'S','status':'OK','value':'105'}]; r,g=nearest_history(rows,'X','S',now-timedelta(hours=24),90,now); assert r and safe_float(r['value'])==100 and g==0; assert nearest_history(rows,'X','OTHER',now-timedelta(hours=24),90,now)[0] is None
    print('MARKET_DATA_VAULT_OFFICIAL_SELF_TEST=PASS')


def validate():
    s=json.loads(STATE_PATH.read_text(encoding='utf-8')); p=json.loads(SUMMARY_PATH.read_text(encoding='utf-8'))
    if s.get('schema_version')!='1.1' or p.get('schema_version')!='1.1': raise SystemExit('wrong schema')
    if s.get('mandatory_total')!=6 or s.get('mandatory_ok',0)<5: raise SystemExit('mandatory official coverage <5/6')
    got=set(s.get('all_collected_metrics') or []); required={'US2Y','US10Y','US30Y','US10Y_REAL'}
    if not required.issubset(got): raise SystemExit('Treasury curve incomplete')
    if 'EFFR' not in got: raise SystemExit('EFFR missing')
    metrics=p.get('metrics') or []; keys={(x.get('metric'),x.get('source')) for x in metrics}
    if len(keys)!=len(metrics): raise SystemExit('duplicate latest metric')
    print('MARKET_DATA_VAULT_OFFICIAL_VALIDATION=PASS')


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--mode',choices=['collect','self-test','validate'],default='collect'); a=ap.parse_args()
    if a.mode=='self-test': self_test()
    elif a.mode=='validate': validate()
    else: collect()

if __name__=='__main__': main()
