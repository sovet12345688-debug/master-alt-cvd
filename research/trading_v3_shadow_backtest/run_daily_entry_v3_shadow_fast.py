from __future__ import annotations

from bisect import bisect_right
import numpy as np

import run_daily_entry_v3_shadow as bt

# QUICK SHADOW SCREEN MODE V2
# Goal: fast, simple comparison of the current MASTER core proxy vs DAILY ENTRY ENGINE V3.
# Research branch only. Production canonical/UI unchanged.
# Hard execution logic is preserved: completed trigger, structural SL, non-chase and core RR>=3.
bt.ATR_BUFFERS = [0.10]
bt.TOUCH_SEARCH_HOURS = 24
bt.TRIGGER_SEARCH_BARS = 8
bt.PRE_SCORE_MIN = 65.0
bt.FINAL_SCORE_MIN = 70.0

# -----------------------------------------------------------------------------
# Performance caches
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# Fib fairness patch
# V3 design says Fib is AUXILIARY. It must not create a setup by itself and must
# not manufacture a distant TP just to satisfy RR.
# -----------------------------------------------------------------------------
class FamilySet(set):
    # Keep FIB membership so its explicit +1 score still works, but do not let
    # FIB count toward the >=2 independent-family setup qualification.
    def __len__(self):
        return sum(1 for x in self if x != 'FIB')


_orig_cluster_refs = bt.cluster_refs


def cluster_refs_no_fib_gate(refs, atr1):
    out = _orig_cluster_refs(refs, atr1)
    for c in out:
        c['families'] = FamilySet(c['families'])
    return out


bt.cluster_refs = cluster_refs_no_fib_gate

_orig_fib_levels = bt.fib_levels


def fib_retracement_only(ph, pl, direction):
    retr, _ext = _orig_fib_levels(ph, pl, direction)
    return retr, []  # Extension cannot be a standalone TP creator in quick screen.


bt.fib_levels = fib_retracement_only

# -----------------------------------------------------------------------------
# More representative CURRENT MASTER CORE proxy.
# Old historical MASTER trades were never durably stored, so this is explicitly
# a reproducible proxy using the old core: MTF trend + EMA/MA + recent structure.
# It intentionally excludes V3-only VWAP/AVWAP/VP/Fib/order-flow scoring.
# -----------------------------------------------------------------------------
def quick_baseline(asset, h1, h4, d1):
    out=[]
    last=-999
    for i,(t,r) in enumerate(h1.iterrows()):
        if t < bt.TRAIN_START or i < 220 or i-last < 6:
            continue
        r4=bt.before(h4,t)
        rd=bt.before(d1,t)
        if r4 is None or rd is None:
            continue
        vals=[r.ema20,r.ma50,r.atr,r.lo12,r.hi12,r4.ema20,r4.ma50]
        if any(np.isnan(float(x)) for x in vals):
            continue
        reg=bt.regime_4h(r4)
        long_ok = reg=='BULL_TREND' and r.close>r.ema20 and r.ema20>=r.ma50*0.99
        short_ok = reg=='BEAR_TREND' and r.close<r.ema20 and r.ema20<=r.ma50*1.01
        if not (long_ok or short_ok):
            continue
        direction='LONG' if long_ok else 'SHORT'
        av=float(r.atr)
        if av<=0: continue

        if direction=='LONG':
            refs=[float(r.ema20),float(r.ma50),float(r4.ema20),float(r4.ma50),float(r.lo12)]
            refs=[x for x in refs if x<r.close and r.close-x<=2.5*av]
            if not refs: continue
            center=max(refs)  # nearest structural support below price
            structure=min(float(r.lo12), center-0.60*av)
            stop=structure-0.10*av
            risk=center-stop
            if risk<=0 or risk/center>0.08: continue
            tp1=center+risk; tp2=center+3*risk; tp3=center+4*risk
        else:
            refs=[float(r.ema20),float(r.ma50),float(r4.ema20),float(r4.ma50),float(r.hi12)]
            refs=[x for x in refs if x>r.close and x-r.close<=2.5*av]
            if not refs: continue
            center=min(refs)  # nearest structural resistance above price
            structure=max(float(r.hi12), center+0.60*av)
            stop=structure+0.10*av
            risk=stop-center
            if risk<=0 or risk/center>0.08: continue
            tp1=center-risk; tp2=center-3*risk; tp3=center-4*risk

        half=0.25*av
        out.append(bt.Setup(asset,'BASELINE',t,direction,float(center),float(center-half),float(center+half),
                            float(stop),float(tp1),float(tp2),float(tp3),np.nan,1.0,reg,False,0.0,
                            'MTF_TREND+EMA_MA+RECENT_STRUCTURE'))
        last=i
    return out


_orig_v3 = bt.v3_setups


def _widen_v3(setups):
    # Same +/-0.25 ATR touch width as baseline quick proxy.
    for s in setups:
        old_width=float(s.zone_high-s.zone_low)
        if old_width>0:
            atr_proxy=old_width/0.30
            half=0.25*atr_proxy
            s.zone_low=s.entry_center-half
            s.zone_high=s.entry_center+half
    return setups


def quick_v3(*args,**kwargs):
    return _widen_v3(_orig_v3(*args,**kwargs))


bt.baseline_setups=quick_baseline
bt.v3_setups=quick_v3

# -----------------------------------------------------------------------------
# Quick-screen verdict. This is screening, NOT Production promotion.
# -----------------------------------------------------------------------------
def quick_verdict(summary, boot, asset_metrics, regime_metrics):
    b=summary.get('BASELINE',{})
    v=summary.get('V3_FIB',{})
    nb,nv=b.get('n',0),v.get('n',0)
    if nb<12 or nv<12:
        return 'QUICK INCONCLUSIVE — SAMPLE STILL LOW',[f'OOS samples baseline={nb}, V3_FIB={nv}']
    exp_ok=v.get('expectancy_R',-99)>b.get('expectancy_R',99)
    pf_ok=v.get('PF',0)>b.get('PF',99)
    dd_ok=abs(v.get('maxDD_R',999))<=abs(b.get('maxDD_R',0))*1.20+0.5
    fs_ok=v.get('false_start_rate',1)<=b.get('false_start_rate',0)+0.05
    asset_ok=0
    for a in ['BTCUSDT','ETHUSDT']:
        m=asset_metrics.get(a,{})
        bm,vm=m.get('BASELINE',{}),m.get('V3_FIB',{})
        if bm.get('n',0)>=4 and vm.get('n',0)>=4 and vm.get('expectancy_R',-99)>=bm.get('expectancy_R',99):
            asset_ok+=1
    reasons=[f"Expectancy {'PASS' if exp_ok else 'FAIL'}",
             f"PF {'PASS' if pf_ok else 'FAIL'}",
             f"MaxDD {'PASS' if dd_ok else 'FAIL'}",
             f"False-start {'PASS' if fs_ok else 'FAIL'}",
             f"Asset consistency {asset_ok}/2"]
    if exp_ok and pf_ok and dd_ok and fs_ok and asset_ok>=1:
        return 'QUICK FAVOR V3 — RUN ONE FINAL CONFIRMATION',reasons
    return 'QUICK NOT PROVEN — DO NOT PROMOTE',reasons


bt.verdict=quick_verdict

if __name__=='__main__':
    bt.main()
