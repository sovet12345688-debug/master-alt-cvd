from __future__ import annotations

import json
import math
from pathlib import Path
import numpy as np
import pandas as pd

import run_phase15 as p15

OUT=Path('btc_trend_v30/output/phase16'); OUT.mkdir(parents=True,exist_ok=True)


def wilson_lower(k,n,z=1.959963984540054):
    if n<=0: return None
    p=k/n; den=1+z*z/n
    center=p+z*z/(2*n); adj=z*math.sqrt((p*(1-p)+z*z/(4*n))/n)
    return (center-adj)/den


def zone_family(sources):
    s=str(sources)
    if 'SMA200' in s or 'LOW120' in s or 'HIGH120' in s:
        return 'REGIME_LONG_TERM'
    if 'SMA50' in s or 'LOW60' in s or 'HIGH60' in s:
        return 'SWING_CORE'
    return 'FAST_RETEST'


def utility_stats(O,score_col='quality_v15'):
    if O.empty: return {}
    q=O[O.success.notna()].copy(); low=q[q[score_col]<65]; high=q[q[score_col]>=65]
    def one(g):
        n=len(g); k=int(g.success.sum()) if n else 0; p=k/n if n else np.nan
        return {'n':n,'wins':k,'success_pct':round(100*p,2) if n else None,'expectancy_R':round(4*p-1,3) if n else None,'wilson95_lower_pct':round(100*wilson_lower(k,n),2) if n else None}
    a=one(low); b=one(high)
    gap=None if not a['n'] or not b['n'] else round(b['success_pct']-a['success_pct'],2)
    # 3R win / 1R loss break-even = 25%. Require OOS lower confidence bound above break-even and useful discrimination.
    utility=bool(b['n']>=30 and b['wilson95_lower_pct'] is not None and b['wilson95_lower_pct']>25 and (b['expectancy_R'] or -99)>=0.20 and (gap or -99)>=8)
    return {'low':a,'high':b,'high_minus_low_pp':gap,'utility_pass':utility,'rule':'3R/1R break-even=25%; high n>=30, Wilson95 lower>25%, expectancy>=+0.20R, high-low gap>=8pp'}


def bucket_monotonic(O,score_col='quality_v15'):
    vals=[]
    for lo,hi in [(0,49),(50,64),(65,79),(80,100)]:
        g=O[(O[score_col]>=lo)&(O[score_col]<=hi)&O.success.notna()]
        vals.append({'bucket':f'{lo}-{hi}','n':len(g),'success_pct':round(float(g.success.mean()*100),2) if len(g) else None})
    populated=[v for v in vals if v['n']>=15 and v['success_pct'] is not None]
    mono=all(populated[i+1]['success_pct']+3>=populated[i]['success_pct'] for i in range(len(populated)-1)) if len(populated)>=3 else False
    return vals,mono


def by_family(O,score_col='quality_v15'):
    if O.empty:return []
    q=O.copy(); q['family']=q.sources.map(zone_family); out=[]
    for fam,g in q.groupby('family'):
        u=utility_stats(g,score_col); b,m=bucket_monotonic(g,score_col)
        out.append({'family':fam,'evaluable':int(g.success.notna().sum()),'utility':u,'buckets':b,'monotonic_pass':m})
    return out


def main():
    D,H4,x,T,fail=p15.prepare()
    zl=p15.build_zone_registry(D,x,'LOW'); zs=p15.build_zone_registry(D,x,'HIGH')
    zlo,zlb,zlc,zly=p15.zone_oos(zl); zso,zsb,zsc,zsy=p15.zone_oos(zs)
    long_u=utility_stats(zlo); short_u=utility_stats(zso); lb,lmono=bucket_monotonic(zlo); sb,smono=bucket_monotonic(zso)
    lf=by_family(zlo); sf=by_family(zso)
    zlo.to_csv(OUT/'zone_oos_long.csv',index=False); zso.to_csv(OUT/'zone_oos_short.csv',index=False)
    d={'engine':'MASTER_BTC_TREND_V3_PHASE1_6_V0_1','status':'RESEARCH_ONLY_NOT_LIVE','data_failures':fail,
       'purpose':'separate economically useful reservation-zone discrimination from full 0-100 score calibration',
       'fixed_trade_outcome':'3R target before 1R structural stop after first touch',
       'break_even_success_pct':25.0,
       'LONG':{'utility':long_u,'buckets':lb,'monotonic_score_pass':lmono,'families':lf},
       'SHORT':{'utility':short_u,'buckets':sb,'monotonic_score_pass':smono,'families':sf},
       'coarse_zone_utility_pass':bool(long_u.get('utility_pass') and short_u.get('utility_pass')),
       'full_0_100_calibration_pass':bool(lmono and smono),
       'overall_stage':'ZONE_COARSE_UTILITY_VALIDATED' if long_u.get('utility_pass') and short_u.get('utility_pass') else 'RESEARCH_PARTIAL_OR_FAIL',
       'master_btc_trend_modified':False,'integration':'FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL',
       'interpretation_rule':'A 45% hit rate at fixed 3R is not weak: expectancy=4p-1. Do not require arbitrary 50% hit rate; require statistically above 25% break-even plus OOS discrimination. 0-100 score still requires monotonic calibration separately.'}
    (OUT/'summary.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(d,ensure_ascii=False))

if __name__=='__main__': main()
