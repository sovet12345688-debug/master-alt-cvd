from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import run_phase11_12_13 as b
import run_phase11_12_13_fast as f13
import run_phase14 as p14
import run_phase14_v04 as v04

OUT = Path('btc_trend_v30/output/phase15')
OUT.mkdir(parents=True, exist_ok=True)


def clip100(v):
    return float(max(0.0, min(100.0, float(v))))


def f1(a):
    p = float(a.get('precision_pct') or 0.0)
    r = float(a.get('recall_pct') or 0.0)
    return round(2 * p * r / (p + r), 2) if p > 0 and r > 0 else 0.0


def prepare():
    D, H4, S, T, fail = b.prepare()
    p11 = b.phase11_scores(D, S)
    p12 = f13.phase12_fast(D, p11)
    x = p14.add_turn_scores(p12)
    add = [c for c in ['drawdown180', 'rally180', 'atr14', 'ret30', 'ret60'] if c in D.columns and c not in x.columns]
    if add:
        x = x.join(D[add], how='left')
    return D, H4, x, T, fail


def match_signal(sig, truth, side):
    ot = pd.Timestamp(sig['origin_time'])
    op = float(sig['origin_price'])
    cand = truth[(truth.side == side) & (truth.time >= ot - pd.Timedelta(days=10)) & (truth.time <= ot + pd.Timedelta(days=10))].copy()
    if cand.empty:
        return None
    cand['price_err_pct'] = (op / cand.price - 1).abs() * 100
    cand = cand[cand.price_err_pct <= 15]
    if cand.empty:
        return None
    cand['time_err_days'] = (cand.time - ot).abs().dt.total_seconds() / 86400
    return cand.sort_values(['time_err_days', 'price_err_pct']).iloc[0]


def origin_failure_audit(x, T, side):
    rows = []
    missed = []
    used_truth = set()
    for y in range(2020, 2027):
        tr = x[x.index.year < y]
        te = x[x.index.year == y]
        tt = T[T.time.dt.year < y]
        ty = T[(T.time.dt.year == y) & (T.side == side)]
        if len(tr) < 500 or len(te) < 100:
            continue
        pars = v04.choose_base(tr, tt, side)
        fw = v04.choose_fractal_weight(tr, tt, side, pars)
        sigs = v04.build_episode_signals(te, side, pars['ct'], pars['tt'], pars['cooldown'], fw)
        for s in sigs:
            m = match_signal(s, T, side)
            matched = m is not None and pd.Timestamp(m.time).year == y
            key = None if m is None else pd.Timestamp(m.time).isoformat()
            if matched:
                used_truth.add(key)
            rows.append({
                'year': y, 'side': side, 'origin_time': s['origin_time'], 'confirm_time': s['confirm_time'],
                'origin_price': s['origin_price'], 'quality': s['quality'], 'fractal_prior': s.get('fractal_prior'),
                'matched': bool(matched), 'matched_truth_time': None if not matched else m.time,
                'truth_price_error_pct': None if not matched else round(float(m.price_err_pct), 3),
                'ct': pars['ct'], 'tt': pars['tt'], 'cooldown': pars['cooldown'], 'fractal_weight': fw,
            })
        for _, q in ty.iterrows():
            key = pd.Timestamp(q.time).isoformat()
            if key not in used_truth:
                missed.append({'year': y, 'side': side, 'truth_time': q.time, 'truth_price': float(q.price)})
    return pd.DataFrame(rows), pd.DataFrame(missed)


def source_score(sources):
    score = 0.0
    for s in sources:
        if s == 'SMA200' or s.endswith('120'):
            score = max(score, 100.0)
        elif s == 'SMA50' or s.endswith('60'):
            score = max(score, 82.0)
        elif s == 'EMA20' or s.endswith('20'):
            score = max(score, 60.0)
    return score


def prior_touch_stats(D, t, side, center, atr, lookback_days=365):
    hist = D[(D.index < t) & (D.index >= t - pd.Timedelta(days=lookback_days))].copy()
    if hist.empty:
        return {'episodes': 0, 'freshness': 100.0, 'days_since_touch': 365.0, 'hold_rate': 50.0, 'median_rr': 1.0}
    tol = max(0.012, min(0.025, float(atr / center) * 0.45))
    touch = (hist.low <= center * (1 + tol)) & (hist.high >= center * (1 - tol))
    starts = list(hist.index[touch & ~touch.shift(1, fill_value=False)])
    episodes = len(starts)
    days_since = 365.0 if not starts else max(0.0, (t - starts[-1]).total_seconds() / 86400.0)
    fresh = 100.0 if episodes == 0 else (90.0 if episodes == 1 else (70.0 if episodes == 2 else (45.0 if episodes == 3 else 20.0)))
    outcomes = []
    rrvals = []
    risk = max(float(atr), center * 0.018)
    for st in starts[-5:]:
        end = min(t - pd.Timedelta(days=1), st + pd.Timedelta(days=45))
        aft = D[(D.index >= st) & (D.index <= end)]
        if len(aft) < 3:
            continue
        if side == 'LOW':
            mfe = max(0.0, float(aft.high.max()) - center)
            mae = max(0.0, center - float(aft.low.min()))
        else:
            mfe = max(0.0, center - float(aft.low.min()))
            mae = max(0.0, float(aft.high.max()) - center)
        rr = mfe / max(risk, mae, 1e-9)
        rrvals.append(rr)
        outcomes.append(float(rr >= 2.0))
    hold = 50.0 if not outcomes else 100.0 * float(np.mean(outcomes))
    medrr = 1.0 if not rrvals else float(np.median(rrvals))
    return {'episodes': episodes, 'freshness': fresh, 'days_since_touch': days_since, 'hold_rate': hold, 'median_rr': medrr}


def approach_score(D, t, side, center):
    h = D.loc[:t].tail(21)
    if len(h) < 10:
        return 50.0
    r5 = float(h.close.iloc[-1] / h.close.iloc[-6] - 1)
    r20 = float(h.close.iloc[-1] / h.close.iloc[0] - 1)
    if side == 'LOW':
        # A support reserve zone is better when price is above it and not already cascading into it.
        cascade = max(0.0, -r5 - 0.05) + 0.5 * max(0.0, -r20 - 0.12)
    else:
        cascade = max(0.0, r5 - 0.05) + 0.5 * max(0.0, r20 - 0.12)
    return clip100(100 - cascade / 0.22 * 100)


def trend_alignment(D, t, side):
    r = D.loc[:t].iloc[-1]
    if side == 'LOW':
        return clip100(30 * float(r.close > r.sma200) + 25 * float(r.sma50 > r.sma200) + 25 * float(r.ema20 > r.sma50) + 20 * float(r.ema20_slope5 > 0))
    return clip100(30 * float(r.close < r.sma200) + 25 * float(r.sma50 < r.sma200) + 25 * float(r.ema20 < r.sma50) + 20 * float(r.ema20_slope5 < 0))


def resolve_3r(D, t, side, z, atr):
    fut = D[(D.index > t) & (D.index <= t + pd.Timedelta(days=90))]
    entry = float(z['center'])
    if side == 'LOW':
        stop = min(float(z['low']), entry - 0.45 * atr) - 0.35 * atr
        risk = entry - stop
        target = entry + 3.0 * risk
    else:
        stop = max(float(z['high']), entry + 0.45 * atr) + 0.35 * atr
        risk = stop - entry
        target = entry - 3.0 * risk
    if risk <= 0 or risk / entry > 0.15:
        return None
    for tt, a in fut.iterrows():
        touched = a.low <= z['high'] and a.high >= z['low']
        if not touched:
            continue
        aft = D[(D.index >= tt) & (D.index <= tt + pd.Timedelta(days=60))]
        for rt, q in aft.iterrows():
            if side == 'LOW':
                good = q.high >= target
                bad = q.low <= stop
            else:
                good = q.low <= target
                bad = q.high >= stop
            if good and bad:
                return {'touch_time': tt, 'resolved_time': rt, 'success': np.nan, 'entry': entry, 'stop': stop, 'target': target, 'rr': 3.0}
            if good:
                return {'touch_time': tt, 'resolved_time': rt, 'success': 1.0, 'entry': entry, 'stop': stop, 'target': target, 'rr': 3.0}
            if bad:
                return {'touch_time': tt, 'resolved_time': rt, 'success': 0.0, 'entry': entry, 'stop': stop, 'target': target, 'rr': 3.0}
        return {'touch_time': tt, 'resolved_time': aft.index.max(), 'success': np.nan, 'entry': entry, 'stop': stop, 'target': target, 'rr': 3.0}
    return None


def build_zone_registry(D, x, side):
    rows = []
    last_t = None
    for t, r in x[x.index.year >= 2018].iterrows():
        if last_t is not None and (t - last_t).days < 14:
            continue
        last_t = t
        dr = D.loc[:t].iloc[-1]
        atr = float(dr.atr14) if pd.notna(dr.atr14) else float(dr.close) * 0.03
        for z in b.zone_candidates(D, t, side):
            p = float(r.close)
            dist = abs(p - float(z['center'])) / p
            pts = prior_touch_stats(D, t, side, float(z['center']), atr)
            origin = float(r.long_candidate_v11 if side == 'LOW' else r.short_candidate_v11)
            frcol = 'fractal_long_prior' if side == 'LOW' else 'fractal_short_prior'
            fr = 50.0 if pd.isna(r.get(frcol, np.nan)) else float(r.get(frcol))
            trend = float(r.drawdown180 if side == 'LOW' else r.rally180)
            trend_score = clip100(trend / (0.35 if side == 'LOW' else 0.70) * 100)
            role = 45.0
            hist = D[(D.index < t) & (D.index >= t - pd.Timedelta(days=120))]
            if len(hist) >= 20:
                zc = float(z['center'])
                if side == 'LOW':
                    role = 100.0 if ((hist.close < zc).rolling(30, min_periods=10).mean().iloc[-1] > 0.20 and float(dr.close) > zc) else 45.0
                else:
                    role = 100.0 if ((hist.close > zc).rolling(30, min_periods=10).mean().iloc[-1] > 0.20 and float(dr.close) < zc) else 45.0
            res = resolve_3r(D, t, side, z, atr)
            rows.append({
                'time': t, 'side': side, 'zone': float(z['center']), 'zone_low': float(z['low']), 'zone_high': float(z['high']),
                'atr_pct': 100 * atr / float(z['center']), 'distance_score': clip100((0.22 - dist) / 0.22 * 100),
                'confluence_score': clip100(float(z['confluence']) / 3 * 100), 'source_score': source_score(z['sources']),
                'trend_score': trend_score, 'origin_score': origin, 'fractal_score': fr,
                'freshness_score': pts['freshness'], 'touch_count': pts['episodes'],
                'days_since_touch_score': clip100(pts['days_since_touch'] / 180 * 100),
                'prior_hold_rate': pts['hold_rate'], 'prior_rr_score': clip100(pts['median_rr'] / 3 * 100),
                'alignment_score': trend_alignment(D, t, side), 'approach_score': approach_score(D, t, side, float(z['center'])),
                'roleflip_score': role, 'sources': '+'.join(z['sources']),
                'touch_time': None if res is None else res['touch_time'], 'resolved_time': None if res is None else res['resolved_time'],
                'success': np.nan if res is None else res['success'], 'entry': None if res is None else res['entry'],
                'stop': None if res is None else res['stop'], 'target': None if res is None else res['target'], 'rr': None if res is None else res['rr'],
            })
    R = pd.DataFrame(rows)
    if not R.empty:
        for c in ['time', 'touch_time', 'resolved_time']:
            R[c] = pd.to_datetime(R[c], utc=True)
    return R


FEATURES = [
    'confluence_score', 'source_score', 'trend_score', 'origin_score', 'fractal_score',
    'freshness_score', 'days_since_touch_score', 'prior_hold_rate', 'prior_rr_score',
    'alignment_score', 'approach_score', 'roleflip_score', 'atr_pct', 'distance_score'
]


def feature_matrix(frame):
    X = frame[FEATURES].astype(float).copy()
    X['atr_pct'] = np.clip(X['atr_pct'] / 8.0 * 100, 0, 100)
    X = X.fillna(50.0).values / 100.0
    # Hand-declared interactions; no test-year tuning.
    extra = np.c_[
        X[:, 0] * X[:, 5],   # confluence x freshness
        X[:, 1] * X[:, 7],   # source importance x prior hold
        X[:, 3] * X[:, 8],   # origin x prior R quality
        X[:, 9] * X[:, 11],  # trend alignment x role flip
        X[:, 5] * X[:, 10],  # freshness x non-cascade approach
    ]
    return np.c_[X, extra]


def fit_logit(train, l2=4.0):
    X = feature_matrix(train); y = train.success.astype(float).values
    mu = X.mean(0); sd = X.std(0); sd[sd < 1e-6] = 1.0
    Z = (X - mu) / sd; Z = np.c_[np.ones(len(Z)), Z]
    w = np.zeros(Z.shape[1])
    for _ in range(1400):
        a = np.clip(Z @ w, -18, 18); p = 1 / (1 + np.exp(-a))
        g = Z.T @ (p - y) / len(y); g[1:] += l2 * w[1:] / len(y)
        w -= 0.035 * g
    return mu, sd, w


def predict(frame, model):
    mu, sd, w = model; X = feature_matrix(frame); Z = (X - mu) / sd; Z = np.c_[np.ones(len(Z)), Z]
    return 1 / (1 + np.exp(-np.clip(Z @ w, -18, 18)))


def zone_oos(R):
    outs = []; years = []
    for y in range(2020, 2027):
        cutoff = pd.Timestamp(f'{y}-01-01', tz='UTC')
        tr = R[(R.resolved_time < cutoff) & R.success.notna()].copy()
        te = R[(R.time.dt.year == y) & R.success.notna()].copy()
        if len(tr) < 70 or len(te) < 8:
            continue
        m = fit_logit(tr)
        ptr = predict(tr, m); pte = predict(te, m)
        te['quality_v15'] = 100 * np.searchsorted(np.sort(ptr), pte, side='right') / len(ptr)
        outs.append(te)
        years.append({'year': y, 'train_n': int(len(tr)), 'test_n': int(len(te))})
    O = pd.concat(outs, ignore_index=True) if outs else pd.DataFrame()
    buckets = []
    if O.empty:
        return O, buckets, {'low_n': 0, 'high_n': 0, 'high_minus_low_pp': None}, years
    for lo, hi in [(0,49), (50,64), (65,79), (80,100)]:
        g = O[(O.quality_v15 >= lo) & (O.quality_v15 <= hi)]
        buckets.append({'bucket': f'{lo}-{hi}', 'evaluable': int(len(g)), 'success_pct': round(float(g.success.mean() * 100), 2) if len(g) else None})
    low = O[O.quality_v15 < 65]; high = O[O.quality_v15 >= 65]
    comp = {
        'low_n': int(len(low)), 'low_success_pct': round(float(low.success.mean()*100),2) if len(low) else None,
        'high_n': int(len(high)), 'high_success_pct': round(float(high.success.mean()*100),2) if len(high) else None,
        'high_minus_low_pp': round(float((high.success.mean()-low.success.mean())*100),2) if len(low) and len(high) else None,
    }
    return O, buckets, comp, years


def zone_gate(comp):
    return bool(comp.get('high_n', 0) >= 20 and (comp.get('high_success_pct') or 0) >= 50 and (comp.get('high_minus_low_pp') or -999) >= 8)


def main():
    D, H4, x, T, fail = prepare()
    # Keep the Phase1.4 origin engine frozen; Phase1.5 audits it rather than re-fitting on test data.
    lrows, lf = v04.oos_origin(x, T, 'LOW', True)
    srows, sf = v04.oos_origin(x, T, 'HIGH', True)
    la, lm = origin_failure_audit(x, T, 'LOW')
    sa, sm = origin_failure_audit(x, T, 'HIGH')
    la.to_csv(OUT/'long_origin_signal_audit.csv', index=False); lm.to_csv(OUT/'long_origin_missed_truth.csv', index=False)
    sa.to_csv(OUT/'short_origin_signal_audit.csv', index=False); sm.to_csv(OUT/'short_origin_missed_truth.csv', index=False)

    zl = build_zone_registry(D, x, 'LOW'); zs = build_zone_registry(D, x, 'HIGH')
    zlo, zlb, zlc, zly = zone_oos(zl); zso, zsb, zsc, zsy = zone_oos(zs)
    zl.to_csv(OUT/'zone_registry_long.csv', index=False); zs.to_csv(OUT/'zone_registry_short.csv', index=False)
    zlo.to_csv(OUT/'zone_oos_long.csv', index=False); zso.to_csv(OUT/'zone_oos_short.csv', index=False)

    og = v04.origin_gate(lf, sf)
    zlg = zone_gate(zlc); zsg = zone_gate(zsc)
    d = {
        'engine': 'MASTER_BTC_TREND_V3_PHASE1_5_V0_1',
        'status': 'RESEARCH_ONLY_NOT_LIVE',
        'data_failures': fail,
        'phase_1_5': {
            'origin_frozen_from_phase14': {'pass': og, 'LONG': lf, 'SHORT': sf},
            'origin_audit': {
                'LONG_false_signals': int((~la.matched).sum()) if len(la) else 0,
                'LONG_missed_truth': int(len(lm)),
                'SHORT_false_signals': int((~sa.matched).sum()) if len(sa) else 0,
                'SHORT_missed_truth': int(len(sm)),
            },
            'zone_outcome_definition': 'pre-touch reservation zone succeeds only if 3R target is reached before structural 1R stop after first touch; same-bar both=ambiguous',
            'zone_long': {'pass': zlg, 'buckets': zlb, 'comparison': zlc, 'years': zly},
            'zone_short': {'pass': zsg, 'buckets': zsb, 'comparison': zsc, 'years': zsy},
            'zone_gate_pass': bool(zlg and zsg),
        },
        'overall_stage': 'PHASE1_FINAL_CANDIDATE' if og and zlg and zsg else 'RESEARCH_PARTIAL_OR_FAIL',
        'master_btc_trend_modified': False,
        'integration': 'FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL',
        'anti_leakage': {
            'origin': 'Phase1.4 prior-year walk-forward engine reused unchanged',
            'zone_features': 'all zone features are computed strictly at registration time from prior/current data only',
            'zone_training': 'test-year scorer trains only on zone outcomes resolved before Jan 1 of test year',
            'outcome': '3R-vs-1R rule fixed by research spec, not optimized on test years',
        },
    }
    (OUT/'summary.json').write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(d, ensure_ascii=False))


if __name__ == '__main__':
    main()
