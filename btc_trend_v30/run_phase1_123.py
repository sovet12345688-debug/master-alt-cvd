from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import run_phase1_origin_chart as base
import run_phase1_origin_chart_v02 as v02

OUT = Path('btc_trend_v30/output/phase123')
OUT.mkdir(parents=True, exist_ok=True)
MATURITY_DAYS = 180


def _aggregate(rows: list[dict]) -> dict:
    sig = sum(int(r.get('signals') or 0) for r in rows)
    mat = sum(int(r.get('matched') or 0) for r in rows)
    tru = sum(int(r.get('truth_events') or 0) for r in rows)
    return {
        'signals': sig,
        'matched': mat,
        'truth_events': tru,
        'precision_pct': round(100 * mat / sig, 2) if sig else None,
        'recall_pct': round(100 * mat / tru, 2) if tru else None,
        'years': len(rows),
    }


def _f05(p: float, r: float) -> float:
    return 1.25 * p * r / (0.25 * p + r) if p > 0 and r > 0 else 0.0


def _mature_truth(truth: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    cutoff = pd.Timestamp(cutoff)
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize('UTC')
    return truth[truth.time <= cutoff - pd.Timedelta(days=MATURITY_DAYS)].copy()


# -----------------------------------------------------------------------------
# PHASE 1.1 — LONG Trend Origin false-positive reduction and origin localization
# -----------------------------------------------------------------------------

def build_long_signals_v03(s: pd.DataFrame, cand_th: float, conf_th: float) -> list[dict]:
    signals: list[dict] = []
    last_confirm = None
    for t, r in s.iterrows():
        if float(r.long_confirm) < conf_th:
            continue
        hist = s[(s.index >= t - pd.Timedelta(days=28)) & (s.index <= t)].copy()
        if hist.empty:
            continue
        elig = hist[
            (hist.long_candidate >= cand_th)
            & (hist.prior_down_context >= 45)
            & (hist.near_low >= 45)
            & ((hist.wave_l >= 35) | (hist.absorb_l >= 45) | (hist.momentum_l >= 45))
        ].copy()
        if elig.empty:
            continue

        # At confirmation time the entire preceding window is already known.  Pick the
        # lowest-price qualified candidate as the origin, rather than the highest score day.
        # This improves localization without looking beyond the confirmation timestamp.
        elig = elig.sort_values(['close', 'long_candidate'], ascending=[True, False])
        best_t = elig.index[0]
        best = elig.iloc[0]
        recovery = float(r.close / best.close - 1.0)
        if recovery < 0.015 or recovery > 0.22:
            continue
        if float(best.prior_down_context + best.near_low) < 100:
            continue

        exhaustion = max(float(best.wave_l), float(best.absorb_l), float(best.momentum_l))
        quality = (
            0.38 * float(best.long_candidate)
            + 0.32 * float(r.long_confirm)
            + 0.20 * exhaustion
            + 0.10 * min(100.0, recovery / 0.10 * 100.0)
        )
        sig = {
            'origin_time': best_t,
            'confirm_time': t,
            'origin_price': float(best.close),
            'confirm_price': float(r.close),
            'candidate': float(best.long_candidate),
            'confirmation': float(r.long_confirm),
            'recovery_pct': round(recovery * 100, 2),
            'quality': round(float(quality), 3),
        }
        if last_confirm is None or (t - last_confirm).days > 35:
            signals.append(sig)
            last_confirm = t
        elif quality > float(signals[-1]['quality']):
            signals[-1] = sig
            last_confirm = t
    return signals


def choose_long_params_v03(train: pd.DataFrame, truth: pd.DataFrame) -> tuple[int, int]:
    opts = []
    for ct in [45, 50, 55, 60, 65]:
        for ft in [50, 55, 60, 65, 70]:
            sig = build_long_signals_v03(train, ct, ft)
            ev = v02.evaluate(sig, truth, 'LOW')
            p = float(ev.get('precision_pct') or 0)
            r = float(ev.get('recall_pct') or 0)
            utility = _f05(p, r)
            # Prefer viable recall before shaving the signal count too aggressively.
            if r >= 25:
                utility += 3.0
            opts.append((utility, p, r, -len(sig), ct, ft))
    best = max(opts)
    return int(best[4]), int(best[5])


def oos_phase11(s: pd.DataFrame, truth: pd.DataFrame) -> list[dict]:
    rows = []
    for y in range(2020, 2027):
        start = pd.Timestamp(f'{y}-01-01', tz='UTC')
        train = s[s.index < start]
        test = s[s.index.year == y]
        tr = _mature_truth(truth, start)
        te = truth[truth.time.dt.year == y]
        if len(train) < 500 or len(test) < 100 or len(tr[tr.side == 'LOW']) < 3:
            continue
        ct, ft = choose_long_params_v03(train, tr)
        sig = build_long_signals_v03(test, ct, ft)
        ev = v02.evaluate(sig, te, 'LOW')
        ev.update({'year': y, 'candidate_threshold': ct, 'confirm_threshold': ft})
        rows.append(ev)
    return rows


# -----------------------------------------------------------------------------
# PHASE 1.2 — strict point-in-time historical price-fractal prior
# No PR #7 code or unvalidated score is imported.  Only the validated research
# principles are reused: independent historical episodes, matured outcomes, analog
# diversity, abstention when evidence is thin, and no MASTER effect.
# -----------------------------------------------------------------------------
FRACTAL_COLS = [
    'prior_down_context', 'prior_up_context', 'near_low', 'near_high',
    'wave_l', 'wave_s', 'momentum_l', 'momentum_s',
    'absorb_l', 'absorb_s', 'candle_l', 'candle_s',
]


def _vec(row: pd.Series) -> np.ndarray:
    return np.array([float(row[c]) / 100.0 for c in FRACTAL_COLS], dtype=float)


def make_fractal_engine(s: pd.DataFrame, truth: pd.DataFrame):
    cache: dict[pd.Timestamp, dict] = {}

    def at(t: pd.Timestamp) -> dict:
        t = pd.Timestamp(t)
        if t.tzinfo is None:
            t = t.tz_localize('UTC')
        if t in cache:
            return cache[t]
        hist_truth = truth[truth.time <= t - pd.Timedelta(days=MATURITY_DAYS)].copy()
        qhist = s[s.index <= t]
        if qhist.empty:
            out = {'status': 'N/A', 'long_score': None, 'short_score': None, 'analogs': 0, 'years': 0}
            cache[t] = out
            return out
        q = _vec(qhist.iloc[-1])
        analogs = []
        for _, ev in hist_truth.iterrows():
            et = pd.Timestamp(ev.time)
            z = s[s.index <= et]
            if z.empty:
                continue
            v = _vec(z.iloc[-1])
            dist = float(np.sqrt(np.mean((q - v) ** 2)))
            sim = math.exp(-dist / 0.35)
            analogs.append((dist, sim, str(ev.side), et))
        analogs.sort(key=lambda x: x[0])
        top = analogs[:9]
        years = len({x[3].year for x in top})
        if len(top) < 6 or years < 2:
            out = {'status': 'N/A', 'long_score': None, 'short_score': None, 'analogs': len(top), 'years': years}
            cache[t] = out
            return out
        den = sum(x[1] for x in top)
        low = sum(x[1] for x in top if x[2] == 'LOW')
        high = sum(x[1] for x in top if x[2] == 'HIGH')
        out = {
            'status': 'OK',
            'long_score': round(100 * low / den, 2) if den else None,
            'short_score': round(100 * high / den, 2) if den else None,
            'analogs': len(top),
            'years': years,
            'median_distance': round(float(np.median([x[0] for x in top])), 4),
            'top_dates': [x[3].date().isoformat() for x in top],
        }
        cache[t] = out
        return out

    return at


def attach_fractal(signals: list[dict], fractal_at) -> list[dict]:
    out = []
    for sig in signals:
        x = dict(sig)
        f = fractal_at(pd.Timestamp(sig['origin_time']))
        x['fractal_long'] = f.get('long_score')
        x['fractal_analogs'] = f.get('analogs')
        x['fractal_years'] = f.get('years')
        x['fractal_status'] = f.get('status')
        out.append(x)
    return out


def filter_fractal(signals: list[dict], threshold: float) -> list[dict]:
    return [
        x for x in signals
        if x.get('fractal_status') == 'OK'
        and x.get('fractal_long') is not None
        and float(x['fractal_long']) >= threshold
        and int(x.get('fractal_analogs') or 0) >= 6
        and int(x.get('fractal_years') or 0) >= 2
    ]


def choose_fractal_threshold(train_signals: list[dict], truth: pd.DataFrame) -> int:
    opts = []
    for th in [45, 50, 55, 60, 65, 70]:
        sig = filter_fractal(train_signals, th)
        ev = v02.evaluate(sig, truth, 'LOW')
        p = float(ev.get('precision_pct') or 0)
        r = float(ev.get('recall_pct') or 0)
        utility = _f05(p, r)
        if p >= 35:
            utility += 2.0
        opts.append((utility, p, r, -len(sig), th))
    return int(max(opts)[4])


def oos_phase12(s: pd.DataFrame, truth: pd.DataFrame, fractal_at) -> tuple[list[dict], list[dict]]:
    rows, base_rows = [], []
    for y in range(2020, 2027):
        start = pd.Timestamp(f'{y}-01-01', tz='UTC')
        train = s[s.index < start]
        test = s[s.index.year == y]
        tr = _mature_truth(truth, start)
        te = truth[truth.time.dt.year == y]
        if len(train) < 500 or len(test) < 100 or len(tr[tr.side == 'LOW']) < 3:
            continue
        ct, ft = choose_long_params_v03(train, tr)
        train_sig = attach_fractal(build_long_signals_v03(train, ct, ft), fractal_at)
        fth = choose_fractal_threshold(train_sig, tr)
        test_base = build_long_signals_v03(test, ct, ft)
        base_ev = v02.evaluate(test_base, te, 'LOW')
        base_ev.update({'year': y, 'candidate_threshold': ct, 'confirm_threshold': ft})
        base_rows.append(base_ev)
        test_sig = attach_fractal(test_base, fractal_at)
        filtered = filter_fractal(test_sig, fth)
        ev = v02.evaluate(filtered, te, 'LOW')
        ev.update({
            'year': y, 'candidate_threshold': ct, 'confirm_threshold': ft,
            'fractal_threshold': fth,
            'fractal_available_signals': sum(1 for z in test_sig if z.get('fractal_status') == 'OK'),
        })
        rows.append(ev)
    return rows, base_rows


# -----------------------------------------------------------------------------
# PHASE 1.3 — Pre-touch Zone Quality
# Scores are computed before price touches the zone.  Post-touch Reaction Strength
# is intentionally excluded here and belongs to the later Entry Quality stage.
# -----------------------------------------------------------------------------
def _cluster_levels(items: list[tuple[str, float]], tol: float = 0.025) -> list[dict]:
    vals = [(n, float(v)) for n, v in items if pd.notna(v) and float(v) > 0]
    vals.sort(key=lambda z: z[1])
    clusters: list[dict] = []
    for name, level in vals:
        hit = None
        for c in clusters:
            if abs(level - c['center']) / c['center'] <= tol:
                hit = c
                break
        if hit is None:
            clusters.append({'center': level, 'levels': [level], 'names': [name]})
        else:
            hit['levels'].append(level)
            hit['names'].append(name)
            hit['center'] = float(np.mean(hit['levels']))
    return clusters


def _past_reaction_score(d: pd.DataFrame, t: pd.Timestamp, center: float, side: str) -> tuple[float | None, int]:
    hist = d[(d.index >= t - pd.Timedelta(days=240)) & (d.index < t - pd.Timedelta(days=1))]
    touch_times = []
    last = None
    loz, hiz = center * 0.985, center * 1.015
    for tt, r in hist.iterrows():
        if float(r.low) <= hiz and float(r.high) >= loz:
            if last is None or (tt - last).days >= 10:
                touch_times.append(tt)
                last = tt
    touch_times = touch_times[-5:]
    outcomes = []
    for tt in touch_times:
        fut = d[(d.index > tt) & (d.index <= min(t - pd.Timedelta(days=1), tt + pd.Timedelta(days=20)))]
        result = None
        for _, r in fut.iterrows():
            if side == 'LONG':
                good = float(r.high) >= center * 1.08
                bad = float(r.low) <= center * 0.94
            else:
                good = float(r.low) <= center * 0.92
                bad = float(r.high) >= center * 1.06
            if good and bad:
                result = None
                break
            if good:
                result = 1
                break
            if bad:
                result = 0
                break
        if result is not None:
            outcomes.append(result)
    if not outcomes:
        return None, len(touch_times)
    return round(100 * float(np.mean(outcomes)), 2), len(touch_times)


def _zone_score(components: dict[str, float | None]) -> float:
    weights = {'confluence': 0.35, 'tf': 0.15, 'distance': 0.15, 'history': 0.20, 'structure': 0.15}
    valid = [(k, v) for k, v in components.items() if v is not None]
    den = sum(weights[k] for k, _ in valid)
    if den <= 0:
        return 0.0
    return round(sum(weights[k] * float(v) for k, v in valid) / den, 2)


def best_zone_at(d: pd.DataFrame, h4: pd.DataFrame, s: pd.DataFrame, t: pd.Timestamp, side: str, fractal_at) -> dict | None:
    dd = d[d.index <= t]
    hh = h4[h4.index <= t + pd.Timedelta(hours=23, minutes=59)]
    ss = s[s.index <= t]
    if dd.empty or hh.empty or ss.empty:
        return None
    r, r4, sr = dd.iloc[-1], hh.iloc[-1], ss.iloc[-1]
    p = float(r.close)
    if side == 'LONG':
        raw = [
            ('D1_EMA20', r.ema20), ('D1_SMA50', r.sma50), ('D1_SMA200', r.sma200),
            ('D1_LOW20', r.low20_prev), ('D1_LOW60', r.low60_prev),
            ('4H_EMA20', r4.ema20), ('4H_SMA50', r4.sma50), ('4H_SMA200', r4.sma200),
        ]
        items = [(n, v) for n, v in raw if pd.notna(v) and p * 0.60 < float(v) < p * 0.995]
    else:
        raw = [
            ('D1_EMA20', r.ema20), ('D1_SMA50', r.sma50), ('D1_SMA200', r.sma200),
            ('D1_HIGH20', r.high20_prev), ('D1_HIGH60', r.high60_prev),
            ('4H_EMA20', r4.ema20), ('4H_SMA50', r4.sma50), ('4H_SMA200', r4.sma200),
        ]
        items = [(n, v) for n, v in raw if pd.notna(v) and p * 1.005 < float(v) < p * 1.60]
    clusters = _cluster_levels(items)
    if not clusters:
        return None
    candidates = []
    for c in clusters:
        center = float(c['center'])
        atrv = float(r.atr14) if pd.notna(r.atr14) else center * 0.02
        half = min(max(center * 0.008, atrv * 0.25), center * 0.02)
        distance = abs(p - center) / p
        if 0.03 <= distance <= 0.12:
            dscore = 100.0
        elif distance < 0.03:
            dscore = max(35.0, 100.0 - (0.03 - distance) / 0.03 * 65.0)
        else:
            dscore = max(20.0, 100.0 - (distance - 0.12) / 0.18 * 80.0)
        conf = min(100.0, len(c['names']) / 4.0 * 100.0)
        tf = 100.0 if any(x.startswith('D1_') for x in c['names']) and any(x.startswith('4H_') for x in c['names']) else 55.0
        hist_score, hist_touches = _past_reaction_score(d, t, center, side)
        if side == 'LONG':
            structure = 40 * float(r.close > r.ema20) + 30 * float(r.ema20_slope5 > 0) + 30 * float(sr.long_confirm >= 50)
            f = fractal_at(t).get('long_score')
        else:
            structure = 40 * float(r.close < r.ema20) + 30 * float(r.ema20_slope5 < 0) + 30 * float(sr.short_confirm >= 50)
            f = fractal_at(t).get('short_score')
        comps = {'confluence': conf, 'tf': tf, 'distance': dscore, 'history': hist_score, 'structure': structure}
        score = _zone_score(comps)
        candidates.append({
            'plan_time': t, 'side': side, 'plan_price': p,
            'zone_low': round(center - half, 2), 'zone_high': round(center + half, 2),
            'center': round(center, 2), 'zone_score': score,
            'distance_pct': round(distance * 100, 2), 'sources': '|'.join(c['names']),
            'historical_touch_samples': hist_touches,
            'fractal_context': f,
            **{f'component_{k}': (None if v is None else round(float(v), 2)) for k, v in comps.items()},
        })
    return max(candidates, key=lambda x: (x['zone_score'], -x['distance_pct']))


def build_zone_plans(d: pd.DataFrame, h4: pd.DataFrame, s: pd.DataFrame, fractal_at) -> pd.DataFrame:
    rows = []
    idx = s[s.index >= pd.Timestamp('2020-01-01', tz='UTC')].index
    for i, t in enumerate(idx):
        if i % 14 != 0:
            continue
        for side in ('LONG', 'SHORT'):
            z = best_zone_at(d, h4, s, t, side, fractal_at)
            if z is not None:
                rows.append(z)
    return pd.DataFrame(rows)


def evaluate_zone_plan(d: pd.DataFrame, row: pd.Series) -> dict:
    t = pd.Timestamp(row.plan_time)
    future = d[(d.index > t) & (d.index <= t + pd.Timedelta(days=30))]
    touch = None
    for tt, r in future.iterrows():
        if float(r.low) <= float(row.zone_high) and float(r.high) >= float(row.zone_low):
            touch = tt
            break
    out = dict(row)
    out['touch_time'] = touch
    out['lead_days'] = None if touch is None else int((touch - t).days)
    out['result'] = 'NO_TOUCH' if touch is None else 'UNRESOLVED'
    if touch is None:
        return out
    fut = d[(d.index > touch) & (d.index <= touch + pd.Timedelta(days=60))]
    center = float(row.center)
    for _, r in fut.iterrows():
        if row.side == 'LONG':
            good = float(r.high) >= center * 1.12
            bad = float(r.low) <= center * 0.92
        else:
            good = float(r.low) <= center * 0.88
            bad = float(r.high) >= center * 1.08
        if good and bad:
            out['result'] = 'AMBIG'
            break
        if good:
            out['result'] = 'HIT'
            break
        if bad:
            out['result'] = 'ADVERSE'
            break
    return out


def zone_bucket_summary(eval_df: pd.DataFrame, side: str) -> list[dict]:
    x = eval_df[eval_df.side == side].copy()
    x['bucket'] = pd.cut(x.zone_score, [-1, 54.999, 69.999, 101], labels=['LOW_<55', 'MID_55_69', 'HIGH_70+'])
    out = []
    for b in ['LOW_<55', 'MID_55_69', 'HIGH_70+']:
        z = x[x.bucket == b]
        touched = z[z.result != 'NO_TOUCH']
        ev = z[z.result.isin(['HIT', 'ADVERSE'])]
        out.append({
            'side': side, 'bucket': b, 'plans': int(len(z)), 'touched': int(len(touched)),
            'evaluable': int(len(ev)), 'hit_rate_pct': round(100 * float((ev.result == 'HIT').mean()), 2) if len(ev) else None,
            'median_lead_days': round(float(touched.lead_days.dropna().median()), 2) if len(touched) else None,
        })
    return out


def _bucket_lookup(rows: list[dict], side: str, bucket: str) -> dict:
    for r in rows:
        if r['side'] == side and r['bucket'] == bucket:
            return r
    return {}


def main() -> None:
    d0, fd = base.load_interval('1d')
    h0, f4 = base.load_interval('4h')
    d, h4 = v02.enrich_context(base.add_features(d0, '1D'), base.add_features(h0, '4H'))
    s = v02.stage_scores(d, h4)
    truth = base.build_truth_events(d)
    truth['time'] = pd.to_datetime(truth.time, utc=True)

    # 1.1
    p11_years = oos_phase11(s, truth)
    p11 = _aggregate(p11_years)
    baseline_years = []
    for y in range(2020, 2027):
        start = pd.Timestamp(f'{y}-01-01', tz='UTC')
        train = s[s.index < start]
        test = s[s.index.year == y]
        tr = _mature_truth(truth, start)
        te = truth[truth.time.dt.year == y]
        if len(train) < 500 or len(test) < 100 or len(tr[tr.side == 'LOW']) < 3:
            continue
        ct, ft = v02.choose_threshold(train, tr, 'LOW')
        sig = v02.build_two_stage_signals(test, 'LOW', ct, ft)
        ev = v02.evaluate(sig, te, 'LOW')
        ev.update({'year': y, 'candidate_threshold': ct, 'confirm_threshold': ft})
        baseline_years.append(ev)
    baseline = _aggregate(baseline_years)
    p11_gain = None if p11['precision_pct'] is None or baseline['precision_pct'] is None else round(p11['precision_pct'] - baseline['precision_pct'], 2)
    p11_gates = {
        'long_precision_ge_30': (p11['precision_pct'] or 0) >= 30,
        'long_recall_ge_25': (p11['recall_pct'] or 0) >= 25,
        'precision_not_worse_than_matured_v02': p11_gain is not None and p11_gain >= 0,
        'matured_training_labels_only': True,
        'origin_selected_only_from_pre_confirmation_window': True,
    }
    phase11 = {
        'engine': 'MASTER_BTC_TREND_V3_PHASE1_1_LONG_ORIGIN_V0_3',
        'status': 'RESEARCH_ONLY_NOT_LIVE',
        'baseline_matured_v02': baseline,
        'oos': p11,
        'precision_gain_vs_matured_v02_pp': p11_gain,
        'years': p11_years,
        'gates': p11_gates,
        'stage': 'PASS_CONTINUE' if all(p11_gates.values()) else 'RESEARCH_FAIL_OR_CONTINUE',
    }
    (OUT / 'phase11_summary.json').write_text(json.dumps(phase11, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

    # 1.2
    fractal_at = make_fractal_engine(s, truth)
    p12_years, p12_base_years = oos_phase12(s, truth, fractal_at)
    p12 = _aggregate(p12_years)
    p12_base = _aggregate(p12_base_years)
    p12_gain = None if p12['precision_pct'] is None or p12_base['precision_pct'] is None else round(p12['precision_pct'] - p12_base['precision_pct'], 2)
    p12_gates = {
        'precision_ge_35': (p12['precision_pct'] or 0) >= 35,
        'recall_ge_15': (p12['recall_pct'] or 0) >= 15,
        'precision_gain_vs_phase11_ge_3pp': p12_gain is not None and p12_gain >= 3,
        'point_in_time_matured_analogs_only': True,
        'min_6_analogs_and_2_years': True,
        'unvalidated_pr7_scores_not_imported': True,
    }
    phase12 = {
        'engine': 'MASTER_BTC_TREND_V3_PHASE1_2_PIT_PRICE_FRACTAL_V0_1',
        'status': 'RESEARCH_ONLY_NOT_LIVE',
        'phase11_same_year_baseline': p12_base,
        'oos': p12,
        'precision_gain_vs_phase11_pp': p12_gain,
        'years': p12_years,
        'gates': p12_gates,
        'stage': 'PASS_CONTINUE' if all(p12_gates.values()) else 'RESEARCH_FAIL_OR_CONTINUE',
        'note': 'Historical analog shares are evidence measures, not calibrated probabilities.',
    }
    (OUT / 'phase12_summary.json').write_text(json.dumps(phase12, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

    # 1.3
    plans = build_zone_plans(d, h4, s, fractal_at)
    evaluated = pd.DataFrame([evaluate_zone_plan(d, r) for _, r in plans.iterrows()]) if len(plans) else pd.DataFrame()
    buckets = zone_bucket_summary(evaluated, 'LONG') + zone_bucket_summary(evaluated, 'SHORT') if len(evaluated) else []
    zone_gates = {}
    for side in ('LONG', 'SHORT'):
        high = _bucket_lookup(buckets, side, 'HIGH_70+')
        mid = _bucket_lookup(buckets, side, 'MID_55_69')
        hp = high.get('hit_rate_pct')
        mp = mid.get('hit_rate_pct')
        zone_gates[f'{side.lower()}_high_evaluable_ge_10'] = int(high.get('evaluable') or 0) >= 10
        zone_gates[f'{side.lower()}_high_hit_rate_ge_55'] = hp is not None and hp >= 55
        zone_gates[f'{side.lower()}_high_gain_vs_mid_ge_5pp'] = hp is not None and mp is not None and hp - mp >= 5
        zone_gates[f'{side.lower()}_median_pre_touch_lead_ge_1d'] = high.get('median_lead_days') is not None and high.get('median_lead_days') >= 1
    zone_gates['pre_touch_only_features'] = True
    zone_gates['post_touch_reaction_excluded_from_score'] = True
    phase13 = {
        'engine': 'MASTER_BTC_TREND_V3_PHASE1_3_PRETOUCH_ZONE_QUALITY_V0_1',
        'status': 'RESEARCH_ONLY_NOT_LIVE',
        'plans': int(len(plans)),
        'evaluated_rows': int(len(evaluated)),
        'buckets': buckets,
        'gates': zone_gates,
        'stage': 'PASS_CONTINUE' if all(zone_gates.values()) else 'RESEARCH_FAIL_OR_CONTINUE',
        'definition': 'Score is frozen before first touch. Evaluation = +/-12% target before +/-8% adverse within 60D after first touch.',
    }
    (OUT / 'phase13_summary.json').write_text(json.dumps(phase13, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

    s.reset_index().to_csv(OUT / 'phase123_daily_state.csv', index=False)
    truth.to_csv(OUT / 'truth_events.csv', index=False)
    if len(plans):
        plans.to_csv(OUT / 'phase13_zone_plans.csv', index=False)
    if len(evaluated):
        evaluated.to_csv(OUT / 'phase13_zone_evaluated.csv', index=False)

    final = {
        'engine': 'MASTER_BTC_TREND_V3_PHASE1_1_TO_1_3',
        'status': 'RESEARCH_ONLY_NOT_LIVE',
        'data': {'start': str(d.index.min()), 'end': str(d.index.max()), 'daily_rows': len(d), 'h4_rows': len(h4), 'failures': fd + f4},
        'phase11': {'stage': phase11['stage'], 'oos': p11, 'gates': p11_gates},
        'phase12': {'stage': phase12['stage'], 'oos': p12, 'gates': p12_gates},
        'phase13': {'stage': phase13['stage'], 'gates': zone_gates, 'buckets': buckets},
        'master_effect': '0%',
        'merge': 'FORBIDDEN_PENDING_MANUAL_REVIEW_AND_EXPLICIT_USER_APPROVAL',
    }
    (OUT / 'phase123_summary.json').write_text(json.dumps(final, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps(final, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
