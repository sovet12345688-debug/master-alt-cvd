from __future__ import annotations

import argparse, json
from datetime import datetime, timezone
from pathlib import Path

import liquidity_macro_engine_r2 as base


def _numeric(*values):
    for raw in values:
        if raw is None:
            continue
        text=str(raw).strip().replace(',','')
        if not text or text.lower() in {'null','none','nan'}:
            continue
        try:
            return float(text)
        except ValueError:
            continue
    return None


def tga_history_fixed(start, end):
    params={
        'sort':'record_date',
        'page[size]':'5000',
        'fields':'record_date,account_type,close_today_bal,open_today_bal',
        'format':'json',
        'filter':f'record_date:gte:{start.date()},record_date:lte:{end.date()}',
    }
    doc=base.get_retry(base.FISCAL_TGA,params=params,timeout=45).json()
    rows=doc.get('data',[]) if isinstance(doc,dict) else []
    bydate={}
    for r in rows:
        typ=str(r.get('account_type') or '').lower()
        if 'treasury general account' not in typ or 'closing balance' not in typ:
            continue
        val=_numeric(r.get('close_today_bal'),r.get('open_today_bal'))
        if val is None:
            continue
        try:
            dt=datetime.strptime(str(r.get('record_date')),'%Y-%m-%d').replace(tzinfo=timezone.utc)
        except Exception:
            continue
        bydate[dt]=val
    if not bydate:
        sample=[{k:r.get(k) for k in ('record_date','account_type','close_today_bal','open_today_bal')} for r in rows[:8]]
        raise RuntimeError(f'no TGA history after fixed parser; rows={len(rows)} sample={sample!r}')
    return bydate


base.tga_history=tga_history_fixed


def build():
    out=base.build()
    out['schema_version']='2.1'
    out['engine_id']='BTC_TREND_V26_LIQUIDITY_MACRO_R21'
    out['parser_fix']='FiscalData string null does not block open_today_bal fallback; closing-balance rows only'
    if isinstance(out.get('feature'),dict):
        lin=out['feature'].setdefault('lineage',{})
        lin['engine']='BTC_TREND_V26_LIQUIDITY_MACRO_R21'
        lin['tga_parser']='R21_NULL_STRING_SAFE_CLOSING_BALANCE'
    out['production_approved']=False
    out['official_state_write_allowed']=False
    return out


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    try: out=build()
    except Exception as e:
        out={'schema_version':'2.1','engine_id':'BTC_TREND_V26_LIQUIDITY_MACRO_R21','status':'VALIDATION_FAIL','error':f'{type(e).__name__}: {e}','production_approved':False,'official_state_write_allowed':False}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps(out,indent=2))
    return 0 if out.get('status')!='VALIDATION_FAIL' else 2

if __name__=='__main__': raise SystemExit(main())
