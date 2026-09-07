from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

UTC=timezone.utc
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'market_vault'; OUT=BASE/'output'; STATE=BASE/'state'
OUT_PATH=OUT/'latest_etf_flows.json'; STATE_PATH=STATE/'etf_flow_state.json'
DIRECT={
 'BTC':'https://farside.co.uk/bitcoin-etf-flow-all-data/',
 'ETH':'https://farside.co.uk/eth/',
}
JINA={k:'https://r.jina.ai/'+v for k,v in DIRECT.items()}
HEADERS={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36','Accept':'text/html,text/plain,*/*'}
DATE_RE=re.compile(r'^\s*(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+20\d{2})\s*\|',re.I)


def now_iso(): return datetime.now(UTC).isoformat().replace('+00:00','Z')

def money(s:str)->float|None:
    s=s.strip().replace(',','').replace('$','').replace('**','')
    if s in {'','-','—','–','N/A'}: return None
    neg=s.startswith('(') and s.endswith(')')
    if neg: s=s[1:-1]
    try: x=float(s)
    except: return None
    if not math.isfinite(x): return None
    return -x if neg else x

def dt_parse(s:str)->datetime|None:
    s=re.sub(r'\s+',' ',s.strip())
    for fmt in ('%d %b %Y','%d %B %Y'):
        try:return datetime.strptime(s,fmt).replace(tzinfo=UTC)
        except:pass
    return None

def fetch(url:str)->str:
    r=requests.get(url,headers=HEADERS,timeout=35,allow_redirects=True); r.raise_for_status(); return r.text

def parse_markdown(text:str)->list[dict[str,Any]]:
    rows=[]
    for raw in text.splitlines():
        line=raw.strip().replace('\\|','|')
        m=DATE_RE.match(line)
        if not m: continue
        dt=dt_parse(m.group(1))
        if not dt: continue
        cells=[c.strip() for c in line.split('|')]
        if len(cells)<3: continue
        vals=[money(c) for c in cells[1:]]
        nums=[x for x in vals if x is not None]
        # all-dash holiday row commonly contains only Total=0.0; require >=2 numeric cells.
        if len(nums)<2: continue
        total=money(cells[-1])
        if total is None: total=nums[-1]
        rows.append({'date':dt,'total_usd_m':total})
    return rows

def strip_tags(html:str)->str:
    t=re.sub(r'(?is)<script.*?>.*?</script>|<style.*?>.*?</style>',' ',html)
    t=re.sub(r'(?i)</tr>|<br\s*/?>','\n',t)
    t=re.sub(r'(?i)</t[dh]>',' | ',t)
    t=re.sub(r'(?s)<[^>]+>',' ',t)
    return re.sub(r'[ \t]+',' ',t)

def parse_direct_html(text:str)->list[dict[str,Any]]:
    return parse_markdown(strip_tags(text))

def collect(asset:str)->dict[str,Any]:
    transport='direct'
    direct_error=None
    try:
        text=fetch(DIRECT[asset]); rows=parse_direct_html(text)
        if len(rows)<20: raise RuntimeError(f'direct parse rows={len(rows)}')
    except Exception as e:
        direct_error=f'{type(e).__name__}: {str(e)[:180]}'
        transport='Jina Reader transport; source content remains Farside'
        text=fetch(JINA[asset]); rows=parse_markdown(text)
    dedup={r['date'].strftime('%Y-%m-%d'):r for r in rows}
    rows=sorted(dedup.values(),key=lambda r:r['date'])
    if len(rows)<20: raise RuntimeError(f'{asset}: fewer than 20 valid trading rows ({len(rows)}); direct_error={direct_error}')
    def win(n:int): return round(sum(float(r['total_usd_m']) for r in rows[-n:]),4)
    latest=rows[-1]
    return {
      'asset':asset,'source':'Farside Investors US ETF flow table','source_url':DIRECT[asset],
      'transport':transport,'direct_fetch_error':direct_error,'unit':'USD millions',
      'latest_trading_date':latest['date'].strftime('%Y-%m-%d'),'flow_1d_usd_m':round(float(latest['total_usd_m']),4),
      'flow_3d_usd_m':win(3),'flow_5d_usd_m':win(5),'flow_20d_usd_m':win(20),'trading_rows_available':len(rows),
      'window_rule':'Actual trading rows only; all-dash holiday/non-session rows excluded; no calendar-day interpolation.',
      'last_20_trading_days':[{'date':r['date'].strftime('%Y-%m-%d'),'flow_usd_m':round(float(r['total_usd_m']),4)} for r in rows[-20:]],
    }

def main():
    OUT.mkdir(parents=True,exist_ok=True); STATE.mkdir(parents=True,exist_ok=True)
    assets=[]; failures={}
    for a in ('BTC','ETH'):
        try: assets.append(collect(a))
        except Exception as e: failures[a]=f'{type(e).__name__}: {str(e)[:350]}'
    payload={'engine':'MASTER_MARKET_FREE_ETF_FLOW_V1','schema_version':'1.2','generated_at_utc':now_iso(),'score_weight':0,
             'role':'Free public ETF-flow evidence for existing MASTER MARKET institution-flow axis. Farside is the data source; text proxy is transport fallback only.',
             'windows':['1D','3D','5D','20D'],'assets':assets,'failures':failures}
    OUT_PATH.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    state={'engine':payload['engine'],'schema_version':payload['schema_version'],'last_run_utc':payload['generated_at_utc'],'ok_assets':[x['asset'] for x in assets],'failures':failures}
    STATE_PATH.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(state,ensure_ascii=False))

if __name__=='__main__': main()
