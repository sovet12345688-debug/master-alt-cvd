from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

import run_phase11_12_13 as b
import run_phase11_12_13_fast as f13

OUT = Path('btc_trend_v30/output/phase14')
OUT.mkdir(parents=True, exist_ok=True)


def _clip100(x):
    return max(0.0, min(100.0, float(x)))


def add_turn_scores(x: pd.DataFrame) -> pd.DataFrame:
    z = x.copy()
    z['long_turn'] = (
        0.52 * z.long_confirm
        + 0.14 * z.hl_l
        + 0.14 * z.retest_l
        + 0.12 * z.momentum_l
        + 0.08 * z.candle_l
    ).clip(0, 100)
    z['short_turn'] = (
        0.52 * z.short_confirm
        + 0.14 * z.lh_s
        + 0.14 * z.retest_s
        + 0.12 * z.momentum_s
        + 0.08 * z.candle_s
    ).clip(0, 100)
    return z


def soft_candidate(row: pd.Series, side: str, fractal_weight: float) -> float:
    if side == 'LOW':
        base = float(row.long_candidate_v11)
        fr = row.get('fractal_long_prior', np.nan)
    else:
        base = float(row.short_candidate_v11)
        fr = row.get('fractal_short_prior', np.nan)
    # Missing fractal is neutral, never a failure. Fractal can rank/tilt but never hard-delete a setup.
    frv = 50.0 if pd.isna(fr) else float(fr)
    return _clip100((1.0 - fractal_weight) * base + fractal_weight * frv)


def recovery_evidence(row: pd.Series, side: str) -> float:
    if side == 'LOW':
        return max(
            float(row.base_l), float(row.hl_l), float(row.retest_l),
            float(row.momentum_l), float(row.absorb_l), float(row.candle_l),
        )
    return max(
        float(row.base_s), float(row.lh_s), float(row.retest_s),
        float(row.momentum_s), float(row.absorb_s), float(row.candle_s),
    )


def build_origin_signals(
    x: pd.DataFrame,
    side: str,
    candidate_threshold: float,
    turn_threshold: float,
    fractal_weight: float,
    lookback_days: int = 35,
) -> list[dict]:
    ctxc = 'prior_down_context' if side == 'LOW' else 'prior_up_context'
    locc = 'near_low' if side == 'LOW' else 'near_high'
    turnc = 'long_turn' if side == 'LOW' else 'short_turn'
    out: list[dict] = []
    last_confirm = None

    for t, r in x.iterrows():
        if float(r[turnc]) < turn_threshold:
            continue
        hist = x[(x.index >= t - pd.Timedelta(days=lookback_days)) & (x.index <= t)]
        if hist.empty:
            continue

        vals = hist.apply(lambda rr: soft_candidate(rr, side, fractal_weight), axis=1)
        bt = vals.idxmax()
        br = hist.loc[bt]
        sc = float(vals.loc[bt])
        if sc < candidate_threshold:
            continue

        # Recall recovery: context/location are safeguards, not high hard gates.
        # A real recovery pattern may compensate for a merely moderate location/context score.
        ctx = float(br[ctxc]); loc = float(br[locc]); rec = recovery_evidence(br, side)
        if ctx < 22 and rec < 70:
            continue
        if loc < 18 and rec < 75:
            continue

        # Falling-knife / blow-off is only a veto when there is no simultaneous recovery evidence.
        if side == 'LOW' and float(br.falling_knife_l) >= 100 and rec < 75:
            continue
        if side == 'HIGH' and float(br.blowoff_s) >= 100 and rec < 75:
            continue

        quality = 0.55 * sc + 0.45 * float(r[turnc])
        sig = {
            'origin_time': bt,
            'confirm_time': t,
            'origin_price': float(br.close),
            'confirm_price': float(r.close),
            'candidate_soft': round(sc, 3),
            'turn_score': round(float(r[turnc]), 3),
            'fractal_weight': fractal_weight,
            'fractal_prior': None if pd.isna(br.get('fractal_long_prior' if side == 'LOW' else 'fractal_short_prior', np.nan)) else float(br.get('fractal_long_prior' if side == 'LOW' else 'fractal_short_prior')),
            'quality': float(quality),
        }
        if last_confirm is None or (t - last_confirm).days > 28:
            out.append(sig)
            last_confirm = t
        elif quality > out[-1]['quality']:
            out[-1] = sig
            last_confirm = t
    return out


def fbeta(p: float, r: float, beta: float = 1.25) -> float:
    if p <= 0 or r <= 0:
        return 0.0
    b2 = beta * beta
    return (1 + b2) * p * r / (b2 * p + r)


def choose_origin_params(train: pd.DataFrame, truth: pd.DataFrame, side: str, allow_fractal: bool):
    best = None
    weights = [0.0, 0.10, 0.20, 0.30] if allow_fractal else [0.0]
    for ct in [38, 42, 46, 50, 55, 60]:
        for tt in [42, 48, 54, 60, 66]:
            for fw in weights:
                sig = build_origin_signals(train, side, ct, tt, fw)
                ev = b.eval_sig(sig, truth, side)
                p = float(ev['precision_pct'] or 0.0)
                r = float(ev['recall_pct'] or 0.0)
                # Favor recovering missed trend origins without allowing precision to collapse.
                score = fbeta(p, r, 1.25) + 0.10 * min(p, 40.0) - 0.08 * max(0, len(sig) - 30)
                if p < 22:
                    score -= 20
                z = (score, p, r, -len(sig), ct, tt, fw)
                if best is None or z > best:
                    best = z
    return {'candidate_threshold': best[4], 'turn_threshold': best[5], 'fractal_weight': best[6]}


def oos_origin(x: pd.DataFrame, truth: pd.DataFrame, side: str, allow_fractal: bool):
    rows = []
    weights = []
    for y in range(2020, 2027):
        tr = x[x.index.year < y]
        te = x[x.index.year == y]
        tt = truth[truth.time.dt.year < y]
        ty = truth[truth.time.dt.year == y]
        if len(tr) < 500 or len(te) < 100:
            continue
        pars = choose_origin_params(tr, tt, side, allow_fractal)
        sig = build_origin_signals(te, side, pars['candidate_threshold'], pars['turn_threshold'], pars['fractal_weight'])
        ev = b.eval_sig(sig, ty, side)
        ev.update({'year': y, **pars})
        rows.append(ev)
        weights.append(pars['fractal_weight'])
    agg = b.v02.aggregate(rows)
    agg['years'] = len(rows)
    agg['mean_selected_fractal_weight'] = round(float(np.mean(weights)), 3) if weights else None
    agg['years_using_fractal'] = int(sum(w > 0 for w in weights))
    return rows, agg


def source_score(sources: list[str], side: str) -> float:
    score = 0.0
    for s in sources:
        if s == 'SMA200' or s.endswith('120'):
            score = max(score, 100.0)
        elif s == 'SMA50' or s.endswith('60'):
            score = max(score, 82.0)
        elif s == 'EMA20' or s.endswith('20'):
            score = max(score, 58.0)
    return score


def resolve_zone(D: pd.DataFrame, t: pd.Timestamp, side: str, z: dict):
    fut = D[(D.index > t) & (D.index <= t + pd.Timedelta(days=90))]
    for tt, a in fut.iterrows():
        if a.low <= z['high'] and a.high >= z['low']:
            aft = D[(D.index >= tt) & (D.index <= tt + pd.Timedelta(days=45))]
            if aft.empty:
                return None
            for rt, q in aft.iterrows():
                if side == 'LOW':
                    good = q.high >= z['center'] * 1.15
                    bad = q.low <= z['center'] * 0.85
                else:
                    good = q.low <= z['center'] * 0.85
                    bad = q.high >= z['center'] * 1.15
                if good and bad:
                    return {'touch_time': tt, 'resolved_time': rt, 'success': np.nan}
                if good:
                    return {'touch_time': tt, 'resolved_time': rt, 'success': 1.0}
                if bad:
                    return {'touch_time': tt, 'resolved_time': rt, 'success': 0.0}
            return {'touch_time': tt, 'resolved_time': aft.index.max(), 'success': np.nan}
    return None


def build_zone_registry(D: pd.DataFrame, f: pd.DataFrame, side: str) -> pd.DataFrame:
    rows = []
    # 14-day registration cadence gives more independent opportunity than monthly-only while avoiding daily duplication.
    last_t = None
    for t, r in f[f.index.year >= 2019].iterrows():
        if last_t is not None and (t - last_t).days < 14:
            continue
        last_t = t
        for z in b.zone_candidates(D, t, side):
            p = float(r.close)
            dist = abs(p - z['center']) / p
            con = _clip100(z['confluence'] / 3.0 * 100)
            dscore = _clip100((0.20 - dist) / 0.20 * 100)
            trend = _clip100((float(r.drawdown180) if side == 'LOW' else float(r.rally180)) / (0.35 if side == 'LOW' else 0.70) * 100)
            origin = float(r.long_candidate_v11 if side == 'LOW' else r.short_candidate_v11)
            fr = r.get('fractal_long_prior' if side == 'LOW' else 'fractal_short_prior', np.nan)
            fr = 50.0 if pd.isna(fr) else float(fr)
            src = source_score(z['sources'], side)
            res = resolve_zone(D, t, side, z)
            rows.append({
                'time': t, 'side': side, 'zone': float(z['center']),
                'confluence_score': con, 'distance_score': dscore, 'trend_score': trend,
                'origin_score': origin, 'fractal_score': fr, 'source_score': src,
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


def weight_grid(n=6, units=4):
    # All non-negative weight vectors summing to 1.0 in 0.25 increments.
    for cuts in itertools.combinations_with_replacement(range(n), units):
        counts = [0] * n
        for c in cuts:
            counts[c] += 1
        yield np.array(counts, dtype=float) / float(units)


def raw_zone_score(frame: pd.DataFrame, w: np.ndarray) -> np.ndarray:
    cols = ['confluence_score', 'distance_score', 'trend_score', 'origin_score', 'fractal_score', 'source_score']
    return frame[cols].astype(float).values @ w


def select_zone_weights(train: pd.DataFrame) -> np.ndarray:
    ev = train[train.success.notna()].copy()
    if len(ev) < 40:
        return np.array([0.20, 0.20, 0.20, 0.20, 0.05, 0.15])
    best = None
    for w in weight_grid():
        sc = raw_zone_score(ev, w)
        lo = np.quantile(sc, 0.35); hi = np.quantile(sc, 0.65)
        low = ev.success.values[sc <= lo]
        high = ev.success.values[sc >= hi]
        if len(low) < 10 or len(high) < 10:
            continue
        gap = 100 * (float(np.mean(high)) - float(np.mean(low)))
        overall = 100 * float(np.mean(high))
        # Reward separation and usable high-zone reaction rate; avoid degenerate one-feature fits.
        diversity = int(np.sum(w > 0))
        obj = gap + 0.20 * overall + 0.5 * min(diversity, 4)
        z = (obj, gap, overall, diversity, tuple(w))
        if best is None or z > best:
            best = z
    return np.array(best[-1], dtype=float) if best else np.array([0.20, 0.20, 0.20, 0.20, 0.05, 0.15])


def calibrate_score(train: pd.DataFrame, test: pd.DataFrame, w: np.ndarray) -> np.ndarray:
    tr = raw_zone_score(train, w)
    te = raw_zone_score(test, w)
    if len(tr) < 20:
        return np.clip(te, 0, 100)
    p10, p90 = np.quantile(tr, [0.10, 0.90])
    if p90 <= p10:
        return np.full(len(te), 50.0)
    return np.clip((te - p10) / (p90 - p10) * 80.0 + 10.0, 0, 100)


def zone_oos(R: pd.DataFrame):
    all_test = []
    years = []
    for y in range(2020, 2027):
        cutoff = pd.Timestamp(f'{y}-01-01', tz='UTC')
        # Only outcomes fully resolved before the test year may train that year's scorer.
        train = R[(R.resolved_time < cutoff) & R.success.notna()].copy()
        test = R[(R.time.dt.year == y) & R.success.notna()].copy()
        if len(train) < 40 or test.empty:
            continue
        w = select_zone_weights(train)
        test['quality_v14'] = calibrate_score(train, test, w)
        all_test.append(test)
        years.append({'year': y, 'train_n': int(len(train)), 'test_n': int(len(test)), 'weights': [round(float(v), 2) for v in w]})
    O = pd.concat(all_test, ignore_index=True) if all_test else pd.DataFrame()
    buckets = []
    if not O.empty:
        for lo, hi in [(0, 49), (50, 64), (65, 79), (80, 100)]:
            g = O[(O.quality_v14 >= lo) & (O.quality_v14 <= hi)]
            buckets.append({'bucket': f'{lo}-{hi}', 'evaluable': int(len(g)), 'success_pct': round(float(g.success.mean() * 100), 2) if len(g) else None})
        low = O[O.quality_v14 < 65]
        high = O[O.quality_v14 >= 65]
        comparison = {
            'low_n': int(len(low)), 'low_success_pct': round(float(low.success.mean() * 100), 2) if len(low) else None,
            'high_n': int(len(high)), 'high_success_pct': round(float(high.success.mean() * 100), 2) if len(high) else None,
            'high_minus_low_pp': round(float((high.success.mean() - low.success.mean()) * 100), 2) if len(low) and len(high) else None,
        }
    else:
        comparison = {'low_n': 0, 'low_success_pct': None, 'high_n': 0, 'high_success_pct': None, 'high_minus_low_pp': None}
    return O, buckets, comparison, years


def gate_origin(long_agg: dict, short_agg: dict) -> bool:
    return bool(
        (long_agg.get('precision_pct') or 0) >= 30
        and (long_agg.get('recall_pct') or 0) >= 30
        and (short_agg.get('precision_pct') or 0) >= 30
        and (short_agg.get('recall_pct') or 0) >= 25
    )


def gate_zone(comp: dict) -> bool:
    return bool(
        comp.get('high_n', 0) >= 20
        and (comp.get('high_success_pct') or 0) >= 50
        and (comp.get('high_minus_low_pp') or -999) >= 8
    )


def main():
    D, H4, S, T, fail = b.prepare()
    p11 = b.phase11_scores(D, S)
    p12 = f13.phase12_fast(D, p11)
    x = add_turn_scores(p12)

    # Baseline without fractal vs soft-fractal OOS comparison.
    lb_rows, lb = oos_origin(x, T, 'LOW', False)
    sb_rows, sb = oos_origin(x, T, 'HIGH', False)
    ls_rows, ls = oos_origin(x, T, 'LOW', True)
    ss_rows, ss = oos_origin(x, T, 'HIGH', True)

    # Pre-touch zone learning with strictly prior matured reactions only.
    zl = build_zone_registry(D, x, 'LOW')
    zs = build_zone_registry(D, x, 'HIGH')
    zlo, zlb, zlc, zly = zone_oos(zl)
    zso, zsb, zsc, zsy = zone_oos(zs)

    zl.to_csv(OUT / 'zone_registry_long.csv', index=False)
    zs.to_csv(OUT / 'zone_registry_short.csv', index=False)
    zlo.to_csv(OUT / 'zone_oos_long.csv', index=False)
    zso.to_csv(OUT / 'zone_oos_short.csv', index=False)
    x.tail(900).to_csv(OUT / 'recent_origin_scores.csv')

    origin_pass = gate_origin(ls, ss)
    zone_long_pass = gate_zone(zlc)
    zone_short_pass = gate_zone(zsc)
    zone_pass = bool(zone_long_pass and zone_short_pass)

    def f1agg(a):
        p = float(a.get('precision_pct') or 0); r = float(a.get('recall_pct') or 0)
        return round(fbeta(p, r, 1.0), 2)

    fractal_utility = {
        'LONG_baseline_F1': f1agg(lb), 'LONG_soft_F1': f1agg(ls),
        'LONG_delta_F1': round(f1agg(ls) - f1agg(lb), 2),
        'SHORT_baseline_F1': f1agg(sb), 'SHORT_soft_F1': f1agg(ss),
        'SHORT_delta_F1': round(f1agg(ss) - f1agg(sb), 2),
        'rule': 'fractal is a soft ranking weight selected only on prior years; missing fractal=neutral 50, never a hard gate',
    }
    fractal_nonharm = bool(fractal_utility['LONG_delta_F1'] >= -1.0 and fractal_utility['SHORT_delta_F1'] >= -1.0)

    summary = {
        'engine': 'MASTER_BTC_TREND_V3_PHASE1_4_V0_1',
        'status': 'RESEARCH_ONLY_NOT_LIVE',
        'data_failures': fail,
        'phase_1_4': {
            'design': 'LONG recall recovery + fractal soft-weight + reaction-learned pre-touch zone quality',
            'origin_gate_pass': origin_pass,
            'LONG_NO_FRACTAL_OOS': lb,
            'LONG_SOFT_FRACTAL_OOS': ls,
            'SHORT_NO_FRACTAL_OOS': sb,
            'SHORT_SOFT_FRACTAL_OOS': ss,
            'LONG_years_soft': ls_rows,
            'SHORT_years_soft': ss_rows,
            'fractal_utility': fractal_utility,
            'fractal_nonharm_pass': fractal_nonharm,
            'zone_long': {'pass': zone_long_pass, 'buckets': zlb, 'comparison': zlc, 'years': zly},
            'zone_short': {'pass': zone_short_pass, 'buckets': zsb, 'comparison': zsc, 'years': zsy},
            'zone_gate_pass': zone_pass,
        },
        'overall_stage': 'PHASE1_4_VALIDATED' if origin_pass and fractal_nonharm and zone_pass else 'RESEARCH_PARTIAL_OR_FAIL',
        'master_btc_trend_modified': False,
        'integration': 'FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL',
        'anti_leakage': {
            'origin_thresholds': 'selected on prior years only',
            'fractal': 'analogs <= query-181D; only fully-known 180D outcomes',
            'zone_learning': 'test-year weights trained only on zone outcomes resolved before Jan-1 of test year',
        },
    }
    (OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
