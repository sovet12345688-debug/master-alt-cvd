from __future__ import annotations

from bisect import bisect_right
import numpy as np

import run_daily_entry_v3_shadow as bt

# QUICK SHADOW MODE
# - Production canonical/UI unchanged.
# - Same historical window and analytical rules.
# - Keep only one fixed ATR buffer so the first-pass backtest is much faster.
# - Full 4-buffer research can still be run with run_daily_entry_v3_shadow.py.
bt.ATR_BUFFERS = [0.10]

# Performance-only monkey patches. Analytical rules are unchanged.
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

if __name__=='__main__':
    bt.main()
