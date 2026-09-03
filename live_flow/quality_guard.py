#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

BASE=Path(__file__).resolve().parent
SUMMARY=BASE/'output'/'live_large_flow_summary.json'
MIN_LARGE_TRADES=3


def qualify_window(w):
    if not isinstance(w,dict): return
    count=w.get('large_trade_count')
    raw=w.get('large_cvd')
    w['large_cvd_raw']=raw
    if count is None:
        w['large_activity_quality']='BUILDING_HISTORY'
        w['directional_eligible']=False
        w['large_cvd_signal']=None
    elif count == 0:
        w['large_activity_quality']='NO_LARGE_TRADES'
        w['directional_eligible']=False
        w['large_cvd_signal']=None
    elif count < MIN_LARGE_TRADES:
        w['large_activity_quality']='LOW_ACTIVITY'
        w['directional_eligible']=False
        w['large_cvd_signal']=None
    else:
        w['large_activity_quality']='VALID'
        w['directional_eligible']=True
        w['large_cvd_signal']=raw


def state(a):
    if a.get('status')!='OK': return 'N/A'
    one=a.get('1h') or {}; four=a.get('4h') or {}; day=a.get('24h') or {}
    x=one.get('large_cvd_signal'); y=four.get('large_cvd_signal'); z=day.get('large_cvd_signal')
    if one.get('large_activity_quality')=='BUILDING_HISTORY': return 'BUILDING_HISTORY'
    if x is None:
        return 'LOW_ACTIVITY_1H'
    if four.get('large_activity_quality')=='BUILDING_HISTORY': return 'LIVE_1H_ONLY'
    if y is None: return 'LOW_ACTIVITY_4H'
    if day.get('large_activity_quality')=='BUILDING_HISTORY':
        if x>0 and y<=0: return 'EARLY_BUY_TURN'
        if x<0 and y>=0: return 'EARLY_SELL_TURN'
        return 'BUILDING_24H'
    if z is None: return 'LOW_ACTIVITY_24H'
    if x>0 and y>0 and z>0 and x>y>z: return 'BUY_ACCELERATION'
    if x<0 and y<0 and z<0 and x<y<z: return 'SELL_ACCELERATION'
    if x>0 and y<=0: return 'EARLY_BUY_TURN'
    if x<0 and y>=0: return 'EARLY_SELL_TURN'
    if x>0 and y>0 and z>0: return 'BUY_DOMINANT'
    if x<0 and y<0 and z<0: return 'SELL_DOMINANT'
    return 'MIXED'


def apply():
    p=json.loads(SUMMARY.read_text(encoding='utf-8'))
    for a in p.get('assets',[]):
        for k in ('1h','4h','24h'): qualify_window(a.get(k))
        a['flow_state']=state(a)
    p['quality_guard']={
        'status':'PASS',
        'min_large_trade_count_for_direction':MIN_LARGE_TRADES,
        'rule':'Raw CVD may be stored, but fewer than 3 Large trades cannot become a directional Live Flow signal.'
    }
    SUMMARY.write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8')
    print('QUALITY GUARD PASS')


def self_test():
    a={'status':'OK','1h':{'large_cvd':100.0,'large_trade_count':1},'4h':{'large_cvd':None,'large_trade_count':None},'24h':{'large_cvd':None,'large_trade_count':None}}
    for k in ('1h','4h','24h'): qualify_window(a[k])
    assert a['1h']['large_cvd_raw']==100.0 and a['1h']['large_cvd_signal'] is None
    assert state(a)=='LOW_ACTIVITY_1H'
    b={'status':'OK','1h':{'large_cvd':50.0,'large_trade_count':3},'4h':{'large_cvd':None,'large_trade_count':None},'24h':{'large_cvd':None,'large_trade_count':None}}
    for k in ('1h','4h','24h'): qualify_window(b[k])
    assert b['1h']['large_cvd_signal']==50.0 and state(b)=='LIVE_1H_ONLY'
    print('QUALITY GUARD SELF TEST PASS')

if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--self-test',action='store_true'); args=ap.parse_args()
    self_test() if args.self_test else apply()
