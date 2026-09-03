from __future__ import annotations

import csv
import io
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
import collector as base

UTC = timezone.utc
UA = {"User-Agent":"MASTER-MARKET-DATA-VAULT/1.1"}


def safe_float(v: Any) -> float | None:
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except (TypeError,ValueError):
        return None


def http_get(url: str, params=None, timeout: int=12):
    last=None
    for i in range(2):
        try:
            r=requests.get(url,params=params,headers=UA,timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last=e
            if i==0: time.sleep(0.8)
    raise RuntimeError(str(last))


def fred_one(metric: str, spec: dict[str,Any]) -> dict[str,Any]:
    sid=str(spec['id'])
    start=(datetime.now(UTC).date()-timedelta(days=75)).isoformat()
    r=http_get(base.FRED_CSV,{"id":sid,"cosd":start},12)
    reader=csv.DictReader(io.StringIO(r.text))
    best_v=None; best_d=None
    for row in reader:
        d=row.get('observation_date') or row.get('DATE') or next(iter(row.values()),None)
        v=safe_float(row.get(sid))
        if d and v is not None:
            best_v,best_d=v,str(d)
    if best_v is None or best_d is None:
        raise RuntimeError(f'no numeric FRED observation for {sid}')
    return {"metric":metric,"value":best_v,"unit":spec.get('unit'),"source":f"FRED:{sid}","source_observation_time":best_d+'T00:00:00Z',"source_frequency":spec.get('frequency'),"timestamp_quality":"official_observation_date","status":"OK"}


def stable_fast() -> list[dict[str,Any]]:
    data=http_get(base.DEFILLAMA_STABLES,{"includePrices":"true"},15).json()
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
    now=base.iso(datetime.now(UTC)); out=[]
    for sym in ('USDT','USDC'):
        if wanted[sym] is not None:
            out.append({"metric":f"{sym}_SUPPLY","value":wanted[sym],"unit":"USD","source":"DefiLlama Stablecoins","source_observation_time":now,"source_frequency":"current","timestamp_quality":"retrieval_time_proxy","status":"OK"})
    if total>0:
        out.append({"metric":"STABLECOIN_TOTAL_SUPPLY","value":total,"unit":"USD","source":"DefiLlama Stablecoins","source_observation_time":now,"source_frequency":"current","timestamp_quality":"retrieval_time_proxy","status":"OK"})
    return out


def cg_fast() -> list[dict[str,Any]]:
    doc=http_get(base.COINGECKO_GLOBAL,timeout=15).json(); d=doc.get('data') if isinstance(doc,dict) else None
    if not isinstance(d,dict): raise RuntimeError('CoinGecko global payload missing data')
    cap=d.get('total_market_cap') or {}; vol=d.get('total_volume') or {}; pct=d.get('market_cap_percentage') or {}; updated=safe_float(d.get('updated_at'))
    obs=base.iso(datetime.fromtimestamp(updated,tz=UTC)) if updated else base.iso(datetime.now(UTC))
    vals=[('CRYPTO_TOTAL_MCAP',safe_float(cap.get('usd')),'USD'),('CRYPTO_24H_VOLUME',safe_float(vol.get('usd')),'USD'),('BTC_DOMINANCE',safe_float(pct.get('btc')),'percent'),('ETH_DOMINANCE',safe_float(pct.get('eth')),'percent')]
    return [{"metric":m,"value":v,"unit":u,"source":"CoinGecko Global","source_observation_time":obs,"source_frequency":"current","timestamp_quality":"source_updated_at" if updated else "retrieval_time_proxy","status":"OK"} for m,v,u in vals if v is not None]


def collect_fast() -> None:
    cfg=base.load_config(); base.DATA_DIR.mkdir(parents=True,exist_ok=True); base.OUT_DIR.mkdir(parents=True,exist_ok=True); base.STATE_DIR.mkdir(parents=True,exist_ok=True)
    snapshot=base.now_hour(); retrieved=datetime.now(UTC); rows=[]; failures={}
    fred=cfg.get('fred_series',{}); mandatory_total=len(fred); mandatory_ok=0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs={ex.submit(fred_one,m,s):m for m,s in fred.items()}
        for fut in as_completed(futs):
            metric=futs[fut]; spec=fred[metric]
            try:
                rows.append({"snapshot_hour_utc":base.iso(snapshot),"retrieved_at_utc":base.iso(retrieved),**fut.result()}); mandatory_ok+=1
            except Exception as e:
                failures[metric]=str(e)[:200]
                rows.append({"snapshot_hour_utc":base.iso(snapshot),"retrieved_at_utc":base.iso(retrieved),"metric":metric,"value":"","unit":spec.get('unit'),"source":f"FRED:{spec['id']}","source_observation_time":"","source_frequency":spec.get('frequency'),"timestamp_quality":"official_observation_date","status":"ERROR"})
    optional_total=0; optional_ok=0
    with ThreadPoolExecutor(max_workers=2) as ex:
        opts={}
        if cfg.get('optional_sources',{}).get('defillama_stablecoins'):
            optional_total+=1; opts[ex.submit(stable_fast)]='DEFILLAMA_STABLECOINS'
        if cfg.get('optional_sources',{}).get('coingecko_global'):
            optional_total+=1; opts[ex.submit(cg_fast)]='COINGECKO_GLOBAL'
        for fut in as_completed(opts):
            name=opts[fut]
            try:
                ext=fut.result(); optional_ok+=1 if ext else 0
                for x in ext: rows.append({"snapshot_hour_utc":base.iso(snapshot),"retrieved_at_utc":base.iso(retrieved),**x})
            except Exception as e:
                failures[name]=str(e)[:200]

    old=base.read_history(); keyed={(r.get('snapshot_hour_utc'),r.get('metric'),r.get('source')):r for r in old}
    for r in rows: keyed[(r.get('snapshot_hour_utc'),r.get('metric'),r.get('source'))]=r
    history=list(keyed.values()); history.sort(key=lambda r:(r.get('snapshot_hour_utc',''),r.get('metric',''),r.get('source','')))
    cutoff=snapshot-timedelta(days=int(cfg.get('history_retention_days',45))); history=[r for r in history if (base.parse_iso(r.get('snapshot_hour_utc')) or snapshot)>=cutoff]; base.write_history(history)
    meta={"collector_version":"1.1-parallel","mandatory_ok":mandatory_ok,"mandatory_total":mandatory_total,"mandatory_coverage_pct":mandatory_ok/mandatory_total*100.0 if mandatory_total else 0.0,"optional_sources_ok":optional_ok,"optional_sources_total":optional_total,"failures":failures,"history_rows":len(history)}
    summary=base.build_summary(history,snapshot,cfg,meta); summary['collector_version']='1.1-parallel'; base.SUMMARY_PATH.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    state={"engine":"MASTER_MARKET_DATA_VAULT_V1","schema_version":"1.0","collector_version":"1.1-parallel","last_run_utc":base.iso(retrieved),"snapshot_hour_utc":base.iso(snapshot),**meta}; base.STATE_PATH.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(state,ensure_ascii=False))


def main():
    import sys
    if '--self-test' in sys.argv:
        base.self_test(); print('FAST_COLLECTOR_SELF_TEST=PASS')
    elif '--validate' in sys.argv:
        base.validate()
    else:
        collect_fast()

if __name__=='__main__': main()
