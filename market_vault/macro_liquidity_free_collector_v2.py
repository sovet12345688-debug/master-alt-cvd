from __future__ import annotations

import csv
import json
import math
import re
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

UTC=timezone.utc
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'market_vault'; DATA=BASE/'data'; OUT=BASE/'output'; STATE=BASE/'state'
HISTORY=DATA/'macro_liquidity_free_history.csv'; SUMMARY=OUT/'latest_macro_liquidity.json'; STATE_PATH=STATE/'macro_liquidity_state.json'
H41='https://www.federalreserve.gov/releases/h41/current/'
FISCAL_TGA='https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/dts/operating_cash_balance'
BUYBACK_API='https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/buybacks_operations'
QRA_RECENT='https://home.treasury.gov/policy-issues/financing-the-government/quarterly-refunding/most-recent-quarterly-refunding-documents'
HEADERS={'User-Agent':'MASTER-MARKET-FREE-MACRO/2.0'}
FIELDS=['snapshot_hour_utc','retrieved_at_utc','metric','value','unit','source','source_url','source_observation_time','source_frequency','status','note']

class TableParser(HTMLParser):
    def __init__(self): super().__init__(); self.tables=[]; self.t=None; self.r=None; self.c=None
    def handle_starttag(self,tag,attrs):
        t=tag.lower()
        if t=='table': self.t=[]
        elif t=='tr' and self.t is not None: self.r=[]
        elif t in {'td','th'} and self.r is not None: self.c=[]
    def handle_data(self,data):
        if self.c is not None:self.c.append(data)
    def handle_endtag(self,tag):
        t=tag.lower()
        if t in {'td','th'} and self.c is not None and self.r is not None:
            self.r.append(re.sub(r'\s+',' ',' '.join(self.c)).strip()); self.c=None
        elif t=='tr' and self.r is not None and self.t is not None:
            if self.r:self.t.append(self.r)
            self.r=None
        elif t=='table' and self.t is not None:
            if self.t:self.tables.append(self.t)
            self.t=None

class LinkParser(HTMLParser):
    def __init__(self): super().__init__(); self.links=[]; self.href=None; self.text=[]
    def handle_starttag(self,tag,attrs):
        if tag.lower()=='a': self.href=dict(attrs).get('href'); self.text=[]
    def handle_data(self,data):
        if self.href is not None:self.text.append(data)
    def handle_endtag(self,tag):
        if tag.lower()=='a' and self.href is not None:
            self.links.append((self.href,re.sub(r'\s+',' ',' '.join(self.text)).strip())); self.href=None; self.text=[]

def now():return datetime.now(UTC)
def hour():return now().replace(minute=0,second=0,microsecond=0)
def iso(d):return d.isoformat().replace('+00:00','Z') if d else None
def parse_iso(s):
    try:return datetime.fromisoformat(str(s).replace('Z','+00:00'))
    except:return None
def f(v):
    try:
        x=float(str(v).replace(',','').replace('$','').strip()); return x if math.isfinite(x) else None
    except:return None
def get(url,params=None,timeout=35):
    r=requests.get(url,params=params,headers=HEADERS,timeout=timeout); r.raise_for_status(); return r

def nums(cells):
    out=[]
    for c in cells:
        s=c.replace('\u00a0',' ').replace('$','').strip()
        if not s:continue
        m=re.fullmatch(r'([+-]?)\s*([\d,]+(?:\.\d+)?)',s)
        if m:
            x=float(m.group(2).replace(',',''))
            if m.group(1)=='-':x=-x
            out.append(x)
    return out

def h41_metrics():
    html=get(H41,timeout=40).text
    p=TableParser();p.feed(html)
    release=None
    m=re.search(r'Release Date:\s*([A-Za-z]+\s+\d{1,2},\s+20\d{2})',re.sub(r'<[^>]+>',' ',html),re.I)
    if m:
        try:release=datetime.strptime(m.group(1),'%B %d, %Y').replace(tzinfo=UTC)
        except:pass
    candidates={}
    for t in p.tables:
        for row in t:
            if not row:continue
            name=re.sub(r'\s+',' ',row[0]).strip().lower()
            vals=nums(row[1:])
            if not vals:continue
            if name=='total assets' and len(vals)>=1:
                # Table 5 row has current system total as first large positive value after elimination placeholder.
                large=[x for x in vals if abs(x)>1_000_000]
                if large:candidates['FED_TOTAL_ASSETS']=large[0]
            elif name.startswith('reverse repurchase agreements'):
                large=[x for x in vals if abs(x)>1_000]
                if large:candidates['FED_RRP_TOTAL']=large[0]
            elif name=='other deposits held by depository institutions':
                large=[x for x in vals if abs(x)>1_000_000]
                if large:candidates['FED_RESERVE_BALANCES']=large[0]
            elif name.startswith('u.s. treasury, general account'):
                large=[x for x in vals if abs(x)>100_000]
                if large:candidates['FED_TGA_H41']=large[0]
    required={'FED_TOTAL_ASSETS','FED_RRP_TOTAL','FED_RESERVE_BALANCES','FED_TGA_H41'}
    miss=required-set(candidates)
    if miss:raise RuntimeError(f'H41 parse missing {sorted(miss)}')
    src='Federal Reserve H.4.1 current release'
    rows=[]
    notes={
      'FED_TOTAL_ASSETS':'Consolidated Federal Reserve Banks total assets.',
      'FED_RRP_TOTAL':'H.4.1 total reverse repurchase agreements; official weekly balance-sheet measure.',
      'FED_RESERVE_BALANCES':'Depository institution reserve balances at Federal Reserve Banks.',
      'FED_TGA_H41':'H.4.1 weekly U.S. Treasury General Account balance used only for same-release liquidity proxy.'}
    for k,v in candidates.items():
        rows.append({'metric':k,'value':v,'unit':'USD millions','source':src,'source_url':H41,'source_observation_time':iso(release),'source_frequency':'weekly','status':'OK','note':notes[k]})
    net=candidates['FED_TOTAL_ASSETS']-candidates['FED_TGA_H41']-candidates['FED_RRP_TOTAL']
    rows.append({'metric':'US_NET_LIQUIDITY_PROXY','value':net,'unit':'USD millions','source':'Federal Reserve H.4.1 components','source_url':H41,'source_observation_time':iso(release),'source_frequency':'weekly','status':'OK','note':'Free official-source proxy = Fed total assets - H.4.1 TGA - H.4.1 reverse repos. Proxy, not an official Fed-published index.'})
    return rows

def tga_metric():
    doc=get(FISCAL_TGA,{'sort':'-record_date','page[size]':'100','fields':'record_date,account_type,close_today_bal,open_today_bal','format':'json'}).json()
    rows=doc.get('data',[])
    for d in sorted({str(r.get('record_date')) for r in rows if r.get('record_date')},reverse=True):
        for r in rows:
            if str(r.get('record_date'))!=d:continue
            if 'treasury general account' not in str(r.get('account_type') or '').lower():continue
            val=f(r.get('close_today_bal'))
            if val is None:val=f(r.get('open_today_bal'))
            if val is not None:return {'metric':'TGA_CLOSING_BALANCE','value':val,'unit':'USD millions','source':'US Treasury FiscalData DTS','source_url':FISCAL_TGA,'source_observation_time':d+'T00:00:00Z','source_frequency':'business_daily','status':'OK','note':'Daily Treasury General Account closing balance.'}
    raise RuntimeError('no TGA value')

def strip_html(h):
    h=re.sub(r'(?is)<script.*?>.*?</script>|<style.*?>.*?</style>',' ',h);h=re.sub(r'<[^>]+>',' ',h);return re.sub(r'\s+',' ',h).strip()

def qra_metrics():
    html=get(QRA_RECENT,timeout=35).text;p=LinkParser();p.feed(html)
    links=[(urljoin(QRA_RECENT,h),t) for h,t in p.links if t.lower().startswith('financing estimates:')]
    if not links:raise RuntimeError('Financing Estimates link not found on most-recent QRA page')
    url=links[0][0]; text=strip_html(get(url,timeout=35).text)
    # Use the first two expectation sentences on the current Financing Estimates release.
    pat=re.compile(r'During the ([^\.]{1,90}?) quarter, Treasury expects to borrow \$([\d,.]+) billion[^\.]{0,300}?end-of-([A-Za-z]+) cash balance of \$([\d,.]+) billion',re.I)
    hits=pat.findall(text)
    if len(hits)<2:raise RuntimeError('could not parse two QRA expected-borrowing quarters')
    dm=re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+20\d{2}',text)
    obs=None
    if dm:
        try:obs=datetime.strptime(dm.group(0),'%B %d, %Y').replace(tzinfo=UTC)
        except:pass
    out=[]
    for i,(q,borrow,month,cash) in enumerate(hits[:2]):
        label='CURRENT_Q' if i==0 else 'NEXT_Q'
        out.append({'metric':f'QRA_NET_MARKETABLE_BORROWING_{label}','value':float(borrow.replace(',','')),'unit':'USD billions','source':'US Treasury Financing Estimates','source_url':url,'source_observation_time':iso(obs),'source_frequency':'quarterly','status':'OK','note':f'Quarter: {q.strip()}.'})
        out.append({'metric':f'QRA_END_CASH_BALANCE_{label}','value':float(cash.replace(',','')),'unit':'USD billions','source':'US Treasury Financing Estimates','source_url':url,'source_observation_time':iso(obs),'source_frequency':'quarterly','status':'OK','note':f'Quarter: {q.strip()}; end-{month} assumption.'})
    return out

def key_by_label(meta,needles):
    labels=meta.get('labels') or {}
    for k,label in labels.items():
        n=re.sub(r'[^a-z0-9]','',str(label).lower())
        if all(x in n for x in needles):return k
    return None

def buyback_metrics():
    doc=get(BUYBACK_API,{'sort':'-operation_date','page[size]':'100','filter':'operation_date:gte:2026-01-01'},timeout=35).json()
    data=doc.get('data') or []; meta=doc.get('meta') or {}
    if not data:raise RuntimeError('buyback FiscalData returned no rows')
    labels=meta.get('labels') or {}
    opkey=key_by_label(meta,['operation','date']) or 'operation_date'
    accepted_key=key_by_label(meta,['total','par','accepted'])
    offered_key=key_by_label(meta,['total','par','offered'])
    # Some rows are security-level; choose latest operation, then use aggregate fields if present, else sum security accepted/offered fields.
    latest=max(str(r.get(opkey) or '') for r in data)
    group=[r for r in data if str(r.get(opkey) or '')==latest]
    def first_numeric(key):
        if not key:return None
        for r in group:
            v=f(r.get(key))
            if v is not None:return v
        return None
    accepted=first_numeric(accepted_key); offered=first_numeric(offered_key)
    if accepted is None:
        indiv=key_by_label(meta,['par','accepted'])
        vals=[f(r.get(indiv)) for r in group] if indiv else []
        vals=[x for x in vals if x is not None]
        if vals:accepted=sum(vals)
    if offered is None:
        indiv=key_by_label(meta,['par','offered'])
        vals=[f(r.get(indiv)) for r in group] if indiv else []
        vals=[x for x in vals if x is not None]
        if vals:offered=sum(vals)
    if accepted is None:raise RuntimeError(f'buyback accepted amount field not found; labels={list(labels.values())[:20]}')
    out=[{'metric':'TREASURY_BUYBACK_ACTUAL_ACCEPTED','value':accepted,'unit':'USD par amount','source':'US Treasury FiscalData buybacks_operations','source_url':BUYBACK_API,'source_observation_time':latest+'T00:00:00Z','source_frequency':'per_operation','status':'OK','note':'Latest operation total par amount accepted; actual result, not announced cap.'}]
    if offered is not None:out.append({'metric':'TREASURY_BUYBACK_ACTUAL_OFFERED','value':offered,'unit':'USD par amount','source':'US Treasury FiscalData buybacks_operations','source_url':BUYBACK_API,'source_observation_time':latest+'T00:00:00Z','source_frequency':'per_operation','status':'OK','note':'Latest operation total par amount offered.'})
    return out

def read_hist():
    if not HISTORY.exists():return []
    with HISTORY.open(encoding='utf-8',newline='') as fh:return list(csv.DictReader(fh))
def write_hist(rows):
    DATA.mkdir(parents=True,exist_ok=True)
    with HISTORY.open('w',encoding='utf-8',newline='') as fh:
        w=csv.DictWriter(fh,fieldnames=FIELDS);w.writeheader();[w.writerow({k:r.get(k,'') for k in FIELDS}) for r in rows]
def nearest(rows,metric,source,target,tol,before):
    best=None;gap=None
    for r in rows:
        if r.get('metric')!=metric or r.get('source')!=source or r.get('status')!='OK':continue
        d=parse_iso(r.get('snapshot_hour_utc'))
        if not d or d>=before:continue
        g=abs((d-target).total_seconds())/3600
        if g<=tol and (gap is None or g<gap):best=r;gap=g
    return best
def delta(cur,p):
    if not p:return None
    pv=f(p.get('value'))
    if pv is None:return None
    return {'previous_value':pv,'delta':cur-pv,'delta_pct':((cur-pv)/abs(pv)*100) if pv else None,'previous_snapshot_hour_utc':p.get('snapshot_hour_utc'),'previous_source_observation_time':p.get('source_observation_time')}
def build(history,snap,fail):
    cur=[r for r in history if r.get('snapshot_hour_utc')==iso(snap) and r.get('status')=='OK']
    out=[]
    for r in cur:
        v=f(r.get('value'))
        if v is None:continue
        metric,source=r['metric'],r['source']
        out.append({**{k:r.get(k) for k in ('metric','value','unit','source','source_url','source_observation_time','source_frequency','note')},'vs_1d':delta(v,nearest(history,metric,source,snap-timedelta(days=1),2,snap)),'vs_3d':delta(v,nearest(history,metric,source,snap-timedelta(days=3),3,snap)),'vs_7d':delta(v,nearest(history,metric,source,snap-timedelta(days=7),4,snap))})
    return {'engine':'MASTER_MARKET_FREE_MACRO_LIQUIDITY_V2','schema_version':'2.0','generated_at_utc':iso(now()),'snapshot_hour_utc':iso(snap),'score_weight':0,'role':'Free official-source evidence for existing MASTER MARKET macro/liquidity axes; not a standalone direction engine.','net_liquidity_formula':'H.4.1 Fed total assets - H.4.1 TGA - H.4.1 total reverse repurchase agreements, USD millions. Weekly official-source proxy.','metrics':sorted(out,key=lambda x:x['metric']),'failures':fail}
def main():
    DATA.mkdir(parents=True,exist_ok=True);OUT.mkdir(parents=True,exist_ok=True);STATE.mkdir(parents=True,exist_ok=True)
    snap=hour();retr=now();rows=[];fail={}
    for name,fn in [('FED_H41',h41_metrics),('TGA',lambda:[tga_metric()]),('QRA',qra_metrics),('BUYBACK_ACTUAL',buyback_metrics)]:
        try:rows.extend(fn())
        except Exception as e:fail[name]=f'{type(e).__name__}: {str(e)[:450]}'
    wrapped=[{'snapshot_hour_utc':iso(snap),'retrieved_at_utc':iso(retr),**r} for r in rows]
    old=read_hist();key={(r.get('snapshot_hour_utc'),r.get('metric'),r.get('source')):r for r in old}
    for r in wrapped:key[(r['snapshot_hour_utc'],r['metric'],r['source'])]=r
    cutoff=snap-timedelta(days=120);hist=[r for r in key.values() if (parse_iso(r.get('snapshot_hour_utc')) or snap)>=cutoff];hist.sort(key=lambda r:(r.get('snapshot_hour_utc',''),r.get('metric',''),r.get('source','')));write_hist(hist)
    payload=build(hist,snap,fail);SUMMARY.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    state={'engine':payload['engine'],'schema_version':payload['schema_version'],'last_run_utc':iso(retr),'snapshot_hour_utc':iso(snap),'collected_metrics':sorted({r['metric'] for r in wrapped}),'failures':fail,'history_rows':len(hist)};STATE_PATH.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(state,ensure_ascii=False))
if __name__=='__main__':main()
