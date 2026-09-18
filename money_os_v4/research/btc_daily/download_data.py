"""Public BTCUSDT data only; full-day cutoff frozen at 2026-09-18 00:00 UTC."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import argparse,hashlib,io,json,threading,zipfile
import pandas as pd
import requests

START=pd.Timestamp('2023-10-01',tz='UTC')
END=pd.Timestamp('2026-09-18',tz='UTC')
LOCAL=threading.local()
def get(url):
 if not hasattr(LOCAL,'session'):LOCAL.session=requests.Session()
 r=LOCAL.session.get(url,timeout=20);r.raise_for_status();return r
def fetch(task,cache):
 key,path=task;out=cache/(key+'.zip');out.parent.mkdir(parents=True,exist_ok=True)
 url='https://data.binance.vision/data/'+path;rec={'key':key,'url':url}
 for attempt in range(3):
  try:
   raw=out.read_bytes() if out.exists() else get(url).content
   cp=out.with_suffix('.CHECKSUM');check=cp.read_bytes() if cp.exists() else get(url+'.CHECKSUM').content
   sha=hashlib.sha256(raw).hexdigest()
   assert sha==check.decode().split()[0]
   with zipfile.ZipFile(io.BytesIO(raw)) as z:assert z.testzip() is None
   if not out.exists():out.write_bytes(raw)
   if not cp.exists():cp.write_bytes(check)
   rec.update(status='ok',sha256=sha,checksum_verified=True,bytes=len(raw));return rec
  except Exception as e:rec.update(status='error',error=str(e))
 return rec
def main():
 p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--cache',required=True);a=p.parse_args()
 out=Path(a.out).resolve();cache=Path(a.cache).resolve()
 for d in ['data','provenance','results']:(out/d).mkdir(parents=True,exist_ok=True)
 tasks=[]
 for ym in pd.period_range('2023-10','2026-08',freq='M').astype(str):
  for kind,path in [('futures15m',f'klines/BTCUSDT/15m/BTCUSDT-15m-{ym}.zip'),('funding',f'fundingRate/BTCUSDT/BTCUSDT-fundingRate-{ym}.zip')]:
   tasks.append((f'{kind}/BTCUSDT/{ym}',f'futures/um/monthly/{path}'))
 for d in pd.date_range('2026-09-01','2026-09-17').strftime('%Y-%m-%d'):
  tasks.append((f'futures15m/BTCUSDT/{d}',f'futures/um/daily/klines/BTCUSDT/15m/BTCUSDT-15m-{d}.zip'))
 records=[]
 with ThreadPoolExecutor(16) as ex:
  for f in as_completed([ex.submit(fetch,t,cache) for t in tasks]):records.append(f.result())
 (out/'provenance/archive_sources.json').write_text(json.dumps(records,indent=2))
 bad=[r for r in records if r['status']!='ok']
 print('archives',len(records),'failed',len(bad),flush=True)
 if bad:print(bad,flush=True);raise RuntimeError('Incomplete archive')
 cols=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_buy_base','taker_buy_quote','ignore']
 keep=['open','high','low','close','volume','quote_volume','taker_buy_quote']
 frames=[];fund_arch=[]
 for key,path in tasks:
  with zipfile.ZipFile(cache/(key+'.zip')) as z:
   with z.open(z.namelist()[0]) as f:
    if key.startswith('funding'):
     x=pd.read_csv(f);x['time']=pd.to_datetime(x.calc_time,unit='ms',utc=True);fund_arch.append(x.set_index('time'))
    else:
     x=pd.read_csv(f,names=cols,header=None,dtype=str);ts=pd.to_numeric(x.open_time,errors='coerce');x=x[ts.notna()].copy();ts=ts[ts.notna()].astype('int64');ts=ts.where(ts<10**14,ts//1000)
     x['time']=pd.to_datetime(ts,unit='ms',utc=True)
     for c in keep:x[c]=pd.to_numeric(x[c],errors='raise')
     frames.append(x.set_index('time')[keep])
 bars=pd.concat(frames).sort_index();assert bars.index.min()==START and bars.index.max()+pd.Timedelta(minutes=15)==END
 assert not bars.index.duplicated().any() and not bars.isna().any().any()
 assert (bars.index.to_series().diff().dropna()==pd.Timedelta(minutes=15)).all()
 assert (bars.high>=bars[['open','low','close']].max(axis=1)).all() and (bars.low<=bars[['open','high','close']].min(axis=1)).all()
 bars.to_csv(out/'data/BTCUSDT_15m.csv.gz',compression='gzip',float_format='%.15g')
 # Exact historical settlement marks as well as rates. Each page is hashed and retained.
 funding=[];pages=[];cursor=int(START.timestamp()*1000);endms=int(END.timestamp()*1000)
 while cursor<endms:
  url=f'https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&startTime={cursor}&endTime={endms-1}&limit=1000'
  r=get(url);rows=r.json();assert isinstance(rows,list)
  pth=out/'provenance'/f'funding_api_page_{len(pages)+1}.json';pth.write_bytes(r.content)
  pages.append(dict(url=url,rows=len(rows),sha256=hashlib.sha256(r.content).hexdigest(),filename=pth.name))
  if not rows:break
  funding+=rows;cursor=int(rows[-1]['fundingTime'])+1
  if len(rows)<1000:break
 fund=pd.DataFrame(funding);fund['time']=pd.to_datetime(fund.fundingTime,unit='ms',utc=True)
 fund=fund.set_index('time').sort_index()
 for c in ['fundingRate','markPrice']:fund[c]=pd.to_numeric(fund[c],errors='raise')
 active=fund[fund.index>=pd.Timestamp('2024-01-01',tz='UTC')]
 assert not fund.index.duplicated().any() and (active.markPrice>0).all()
 assert fund.index.min()-START<pd.Timedelta(seconds=1) and END-fund.index.max()<pd.Timedelta(hours=9)
 # Public monthly rates must agree with REST records at the same second.
 archived=pd.concat(fund_arch).sort_index();archived.index=archived.index.floor('s')
 rest=fund.copy();rest.index=rest.index.floor('s')
 joined=archived.last_funding_rate.to_frame('archive_rate').join(rest.fundingRate.rename('api_rate'),how='left')
 assert not joined.isna().any().any()
 assert (joined.archive_rate-joined.api_rate).abs().max()<1e-12
 fund[['fundingRate','markPrice']].to_csv(out/'data/BTCUSDT_funding.csv.gz',compression='gzip',float_format='%.15g')
 (out/'provenance/funding_api_sources.json').write_text(json.dumps(pages,indent=2))
 profile=dict(start=str(START),data_end_exclusive=str(END),bars=len(bars),funding_rows=len(fund),price_duplicates=0,price_gaps=0,price_nulls=0,monthly_funding_rows_reconciled=len(joined),funding_mark='actual historical API markPrice for every tested trade',warmup_only_missing_marks=int(fund.markPrice.isna().sum()),test_period_missing_marks=int(active.markPrice.isna().sum()),archives=len(records))
 (out/'results/data_quality.json').write_text(json.dumps(profile,indent=2))
 print(json.dumps(profile,indent=2),flush=True)
if __name__=='__main__':main()
