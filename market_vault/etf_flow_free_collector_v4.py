from __future__ import annotations

import csv
import io
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

UTC=timezone.utc
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'market_vault'; OUT=BASE/'output'; STATE=BASE/'state'
OUT_PATH=OUT/'latest_etf_flows.json'; STATE_PATH=STATE/'etf_flow_state.json'
MIRROR={
 'BTC':'https://raw.githubusercontent.com/haturatu/crypto-etf-flow/main/etf_btc.csv',
 'ETH':'https://raw.githubusercontent.com/haturatu/crypto-etf-flow/main/etf_eth.csv',
}
SOURCE_PAGES={
 'BTC':'https://farside.co.uk/bitcoin-etf-flow-all-data/',
 'ETH':'https://farside.co.uk/eth/',
}
MIRROR_REPO='https://github.com/haturatu/crypto-etf-flow'
HEADERS={'User-Agent':'MASTER-MARKET-ETF-FLOW/4.0'}

def now_iso():return datetime.now(UTC).isoformat().replace('+00:00','Z')
def val(v:Any)->float|None:
    try:
        s=str(v or '').strip().replace(',','').replace('$','')
        if s in {'','-','—','–','N/A'}:return None
        neg=s.startswith('(') and s.endswith(')')
        if neg:s=s[1:-1]
        x=float(s)
        if not math.isfinite(x):return None
        return -x if neg else x
    except:return None
def parse_date(s:str)->datetime|None:
    for fmt in ('%d %b %Y','%d %B %Y'):
        try:return datetime.strptime(s.strip(),fmt).replace(tzinfo=UTC)
        except:pass
    return None

def collect(asset:str)->dict[str,Any]:
    r=requests.get(MIRROR[asset],headers=HEADERS,timeout=30);r.raise_for_status()
    rows=[]
    for row in csv.DictReader(io.StringIO(r.text)):
        d=parse_date(str(row.get('Date') or ''))
        if not d:continue
        # Holiday/non-session rows in the mirror can have Total=0.0 but all fund cells blank.
        fund_keys=[k for k in row.keys() if k not in {'Date','Total'}]
        nums=[val(row.get(k)) for k in fund_keys]
        if not any(x is not None for x in nums):continue
        total=val(row.get('Total'))
        if total is None:
            known=[x for x in nums if x is not None]
            if not known:continue
            total=sum(known)
        rows.append({'date':d,'total_usd_m':total})
    rows.sort(key=lambda x:x['date'])
    if len(rows)<20:raise RuntimeError(f'{asset}: only {len(rows)} valid trading rows')
    def w(n):return round(sum(float(x['total_usd_m']) for x in rows[-n:]),4)
    latest=rows[-1]
    return {
      'asset':asset,
      'source':'Farside Investors ETF flow data via public GitHub mirror',
      'source_url':SOURCE_PAGES[asset],
      'mirror_url':MIRROR_REPO,
      'mirror_raw_url':MIRROR[asset],
      'unit':'USD millions',
      'latest_trading_date':latest['date'].strftime('%Y-%m-%d'),
      'flow_1d_usd_m':round(float(latest['total_usd_m']),4),
      'flow_3d_usd_m':w(3),'flow_5d_usd_m':w(5),'flow_20d_usd_m':w(20),
      'trading_rows_available':len(rows),
      'window_rule':'Actual trading rows only; all-blank holiday/non-session rows excluded; no calendar-day interpolation.',
      'source_policy':'Mirror is transport/cache only. Farside remains the stated data source; latest date/value should be cross-checked against public Farside/secondary confirmation in OFFICIAL when material.',
      'last_20_trading_days':[{'date':x['date'].strftime('%Y-%m-%d'),'flow_usd_m':round(float(x['total_usd_m']),4)} for x in rows[-20:]],
    }

def main():
    OUT.mkdir(parents=True,exist_ok=True);STATE.mkdir(parents=True,exist_ok=True)
    assets=[];fail={}
    for a in ('BTC','ETH'):
        try:assets.append(collect(a))
        except Exception as e:fail[a]=f'{type(e).__name__}: {str(e)[:350]}'
    p={'engine':'MASTER_MARKET_FREE_ETF_FLOW_V1','schema_version':'1.3','generated_at_utc':now_iso(),'score_weight':0,'role':'Free public ETF-flow evidence for existing MASTER MARKET institution-flow axis.','windows':['1D','3D','5D','20D'],'assets':assets,'failures':fail}
    OUT_PATH.write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8')
    s={'engine':p['engine'],'schema_version':p['schema_version'],'last_run_utc':p['generated_at_utc'],'ok_assets':[x['asset'] for x in assets],'failures':fail};STATE_PATH.write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(s,ensure_ascii=False))
if __name__=='__main__':main()
