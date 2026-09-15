from __future__ import annotations

from bisect import bisect_right
import numpy as np

import run_daily_entry_v3_shadow as bt

# QUICK SHADOW SCREEN MODE
# Goal: fast, simple directional comparison before any full promotion test.
# Production canonical/UI unchanged.
# Research-only relaxations increase sample size while keeping the SAME
# structural-entry / closed-trigger / structural-SL / >=3R execution logic.
bt.ATR_BUFFERS = [0.10]
bt.TOUCH_SEARCH_HOURS = 24
bt.TRIGGER_SEARCH_BARS = 8
bt.PRE_SCORE_MIN = 65.0
bt.FINAL_SCORE_MIN = 70.0

# Performance-only caches.
_PIV = {}
_AVW = {}
_VPOC = {}


def fast_pivot_state(events, t):
    key = id(events)
    if key not in _PIV:
        confirms=[]; phs=[]; pls=[]; ph=None; pl=None
        for e in events:
            if e['type']=='H': ph=e
            else: pl=e
            confirms.append(e['confirm'].value)
            phs.append(ph); pls.append(pl)
        _PIV[key]=(confirms,phs,pls)
    confirms,phs,pls=_PIV[key]
    k=bisect_right(confirms,t.value)-1
    if k<0: return None,None,[]
    return phs[k],pls[k],[]


def fast_anchored_vwap(h1,pos,pivot_time):
    if pivot_time is None: return np.nan
    key=id(h1)
    if key not in _AVW:
        typ=((h1.high+h1.low+h1.close)/3).to_numpy(float)
        vol=h1.quote_volume.to_numpy(float)
        pv=np.cumsum(np.nan_to_num(typ*vol,nan=0.0))
        vv=np.cumsum(np.nan_to_num(vol,nan=0.0))
        _AVW[key]=(pv,vv,h1.index)
    pv,vv,idx=_AVW[key]
    p0=int(idx.searchsorted(pivot_time))
    if p0>=pos: return np.nan
    num=pv[pos]-(pv[p0-1] if p0>0 else 0.0)
    den=vv[pos]-(vv[p0-1] if p0>0 else 0.0)
    return float(num/den) if den>0 else np.nan


_orig_vpoc=bt.rolling_vpoc
bt.pivot_state=fast_pivot_state
bt.anchored_vwap=fast_anchored_vwap


def vpoc_cached(h1,pos,bins=24):
    key=(id(h1),int(pos),int(bins))
    if key not in _VPOC:
        _VPOC[key]=_orig_vpoc(h1,pos,bins)
    return _VPOC[key]


bt.rolling_vpoc=vpoc_cached

# Widen the research touch-zone symmetrically for BOTH baseline and V3.
# The original engine uses +/-0.15 ATR. Quick screen uses +/-0.25 ATR.
# Entry center, structural stop, targets and >=3R gate are NOT moved.
_orig_base = bt.baseline_setups
_orig_v3 = bt.v3_setups


def _widen(setups):
    for s in setups:
        old_width = float(s.zone_high - s.zone_low)
        if old_width > 0:
            atr_proxy = old_width / 0.30
            half = 0.25 * atr_proxy
            s.zone_low = s.entry_center - half
            s.zone_high = s.entry_center + half
    return setups


def quick_base(*args, **kwargs):
    return _widen(_orig_base(*args, **kwargs))


def quick_v3(*args, **kwargs):
    return _widen(_orig_v3(*args, **kwargs))


bt.baseline_setups = quick_base
bt.v3_setups = quick_v3

# Quick-screen verdict: this is NOT a Production promotion verdict.
# If favorable, only then run one final stricter confirmation test.
def quick_verdict(summary, boot, asset_metrics, regime_metrics):
    b = summary.get('BASELINE', {})
    v = summary.get('V3_FIB', {})
    nb, nv = b.get('n',0), v.get('n',0)
    if nb < 12 or nv < 12:
        return 'QUICK INCONCLUSIVE — SAMPLE STILL LOW', [f'OOS samples baseline={nb}, V3_FIB={nv}']
    exp_ok = v.get('expectancy_R',-99) > b.get('expectancy_R',99)
    pf_ok = v.get('PF',0) > b.get('PF',99)
    dd_ok = abs(v.get('maxDD_R',999)) <= abs(b.get('maxDD_R',0))*1.20 + 0.5
    fs_ok = v.get('false_start_rate',1) <= b.get('false_start_rate',0) + 0.05
    btc = asset_metrics.get('BTCUSDT',{})
    eth = asset_metrics.get('ETHUSDT',{})
    asset_ok = 0
    for m in [btc, eth]:
        bm, vm = m.get('BASELINE',{}), m.get('V3_FIB',{})
        if bm.get('n',0) >= 4 and vm.get('n',0) >= 4 and vm.get('expectancy_R',-99) >= bm.get('expectancy_R',99):
            asset_ok += 1
    reasons=[
        f"Expectancy {'PASS' if exp_ok else 'FAIL'}",
        f"PF {'PASS' if pf_ok else 'FAIL'}",
        f"MaxDD {'PASS' if dd_ok else 'FAIL'}",
        f"False-start {'PASS' if fs_ok else 'FAIL'}",
        f"Asset consistency {asset_ok}/2",
    ]
    if exp_ok and pf_ok and dd_ok and fs_ok and asset_ok >= 1:
        return 'QUICK FAVOR V3 — RUN ONE FINAL CONFIRMATION', reasons
    return 'QUICK NOT PROVEN — DO NOT PROMOTE', reasons


bt.verdict = quick_verdict

if __name__=='__main__':
    bt.main()
