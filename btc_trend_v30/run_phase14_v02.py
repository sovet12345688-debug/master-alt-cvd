from __future__ import annotations

import numpy as np
import pandas as pd

import run_phase14 as p14


def build_zone_registry_fixed(D: pd.DataFrame, f: pd.DataFrame, side: str) -> pd.DataFrame:
    """Phase1.4 hotfix: trend context must come from the enriched daily frame D.

    run_phase14.phase11_scores intentionally joins only a compact subset of D columns,
    so drawdown180/rally180 are not present in f. Reading them from D.loc[t] preserves
    the original point-in-time design and does not use future information.
    """
    rows = []
    last_t = None
    for t, r in f[f.index.year >= 2019].iterrows():
        if last_t is not None and (t - last_t).days < 14:
            continue
        last_t = t
        if t not in D.index:
            continue
        dr = D.loc[t]
        for z in p14.b.zone_candidates(D, t, side):
            price = float(r.close)
            dist = abs(price - z['center']) / price
            con = p14._clip100(z['confluence'] / 3.0 * 100)
            dscore = p14._clip100((0.20 - dist) / 0.20 * 100)
            if side == 'LOW':
                trend_raw = float(dr.drawdown180) if pd.notna(dr.drawdown180) else 0.0
                trend = p14._clip100(trend_raw / 0.35 * 100)
                origin = float(r.long_candidate_v11)
                fr = r.get('fractal_long_prior', np.nan)
            else:
                trend_raw = float(dr.rally180) if pd.notna(dr.rally180) else 0.0
                trend = p14._clip100(trend_raw / 0.70 * 100)
                origin = float(r.short_candidate_v11)
                fr = r.get('fractal_short_prior', np.nan)
            fr = 50.0 if pd.isna(fr) else float(fr)
            src = p14.source_score(z['sources'], side)
            res = p14.resolve_zone(D, t, side, z)
            rows.append({
                'time': t,
                'side': side,
                'zone': float(z['center']),
                'confluence_score': con,
                'distance_score': dscore,
                'trend_score': trend,
                'origin_score': origin,
                'fractal_score': fr,
                'source_score': src,
                'sources': '+'.join(z['sources']),
                'touch_time': None if res is None else res['touch_time'],
                'resolved_time': None if res is None else res['resolved_time'],
                'success': np.nan if res is None else res['success'],
            })
    R = pd.DataFrame(rows)
    if not R.empty:
        R['time'] = pd.to_datetime(R.time, utc=True)
        R['touch_time'] = pd.to_datetime(R.touch_time, utc=True)
        R['resolved_time'] = pd.to_datetime(R.resolved_time, utc=True)
    return R


if __name__ == '__main__':
    p14.build_zone_registry = build_zone_registry_fixed
    p14.main()
