from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
BTC_ROOT = HERE.parents[1]
sys.path.insert(0, str(BTC_ROOT))

from r20 import r20_historical_diagnostic_replay as base
from r20.r20_engine import feature_frame
from r21 import r21_historical_diagnostic_replay_runner as r21run

CANON = Path('forensic_inputs/canonical')
ROB = Path('forensic_inputs/robustness')
OUT = HERE / 'output/bear_short16_forensic'
OUT.mkdir(parents=True, exist_ok=True)

CFG_PATH = BTC_ROOT / 'r20/r20_frozen_config_rev2.json'
EXPECTED = {
    'bear_short_n': 16,
    'bear_short_mean_R': -0.23525625119728688,
    'robustness_run_id': 34129292873,
    'robustness_artifact_id': 10021432053,
    'robustness_artifact_sha256': '07f21af952e1517a34d97d7571857b5cc2af02736787d8ef8e6fabc34c335c6d',
    'canonical_run_id': 34084074525,
    'canonical_artifact_id': 10004727447,
    'canonical_artifact_sha256': '11362981be0938ec9c5f4d2aea00d1db5be5fc86ec41db7964f6fb83dc7bc8a3',
}


def locate_one(root: Path, name: str, must_contain: str | None = None) -> Path:
    hits = list(root.rglob(name))
    if must_contain is not None:
        hits = [p for p in hits if must_contain in p.as_posix()]
    if len(hits) != 1:
        raise SystemExit(f'INPUT_LOCATE_FAIL:{name}:{must_contain}:{[str(x) for x in hits]}')
    return hits[0]


def consecutive_true(s: pd.Series, i: int) -> int:
    n = 0
    for j in range(i, -1, -1):
        if bool(s.iloc[j]):
            n += 1
        else:
            break
    return n


def main() -> None:
    cfg = json.loads(CFG_PATH.read_text(encoding='utf-8'))
    if cfg.get('status') != 'FINAL_FROZEN_NO_REPLAY_REV2':
        raise SystemExit('R20_REV2_NOT_FROZEN')

    pos_path = locate_one(ROB, 'r20_positions.csv', 'BASELINE_IDENTITY')
    dpath = locate_one(CANON, 'btc_usdt_1d_matrix.csv')
    h4path = locate_one(CANON, 'btc_usdt_complete_4h_matrix.csv')
    h1path = locate_one(CANON, 'btc_usdt_1h_matrix.csv')

    p = pd.read_csv(pos_path)
    p['seed_time'] = pd.to_datetime(p['seed_time'], utc=True, format='mixed')
    p['exit_time'] = pd.to_datetime(p['exit_time'], utc=True, errors='coerce', format='mixed')
    p['marked_R_at_end'] = pd.to_numeric(p['marked_R_at_end'], errors='raise')

    daily = base.ohlcv_daily(pd.read_csv(dpath))
    h4 = r21run.ohlcv_tf_mixed_iso(pd.read_csv(h4path))
    h1 = r21run.ohlcv_tf_mixed_iso(pd.read_csv(h1path))

    df = feature_frame(daily, cfg)
    hf = feature_frame(h4, cfg)
    df['ema200'] = df['close'].ewm(span=200, adjust=False, min_periods=200).mean()
    df['ema200_slope20'] = df['ema200'] - df['ema200'].shift(20)
    hav = hf.copy(); hav.index = hav.index + pd.Timedelta(hours=4)

    def regime_at(ts: pd.Timestamp) -> str:
        i = base.daily_source_index_at(df, ts, cfg)
        if i < 0:
            return 'TRANSITION'
        r = df.iloc[i]
        vals = [r.close, r.ema200, r.ema200_slope20]
        if not all(pd.notna(v) and np.isfinite(float(v)) for v in vals):
            return 'TRANSITION'
        if float(r.close) > float(r.ema200) and float(r.ema200_slope20) > 0:
            return 'BULL'
        if float(r.close) < float(r.ema200) and float(r.ema200_slope20) < 0:
            return 'BEAR'
        return 'TRANSITION'

    shorts = p[p.direction == 'SHORT'].copy()
    shorts['regime'] = [regime_at(t) for t in shorts.seed_time]
    bear = shorts[shorts.regime == 'BEAR'].copy()
    if len(bear) != EXPECTED['bear_short_n']:
        raise SystemExit(f'BEAR_SHORT_COUNT_FAIL:{len(bear)}')
    mean_r = float(bear.marked_R_at_end.mean())
    if abs(mean_r - EXPECTED['bear_short_mean_R']) > 1e-12:
        raise SystemExit(f'BEAR_SHORT_MEAN_FAIL:{mean_r}')

    rows = []
    for _, q in bear.iterrows():
        t = pd.Timestamp(q.seed_time)
        hi = hav.index.searchsorted(t, side='right') - 1
        di = base.daily_source_index_at(df, t, cfg)
        if hi < 0 or di < 0:
            raise SystemExit(f'FEATURE_INDEX_FAIL:{q.episode_id}')
        hr = hav.iloc[hi]
        dr = df.iloc[di]
        risk = float(q.stop) - float(q.seed_entry)
        if risk <= 0:
            raise SystemExit(f'BAD_SHORT_RISK:{q.episode_id}')
        w60 = df.iloc[max(0, di-59):di+1]
        days_since_60d_high = int(len(w60)-1-int(np.argmax(w60.high.to_numpy())))
        out = {
            'episode_id': q.episode_id,
            'seed_time': t,
            'year': int(t.year),
            'route': q.route,
            'R': float(q.marked_R_at_end),
            'winner': bool(float(q.marked_R_at_end) > 0),
            'exit_reason': q.exit_reason,
            'days_to_exit': float((q.exit_time-t).total_seconds()/86400.0) if pd.notna(q.exit_time) else None,
            'stop_dist_pct': float(risk/float(q.seed_entry)*100.0),
            'h4_volume_ratio': float(hr.volume_ratio),
            'h4_break_depth_atr': float((hr.close-hr.prior20_low)/hr.atr14),
            'h4_close_location': float(hr.close_location),
            'daily_dist_prior20low_atr': float((dr.close-dr.prior20_low)/dr.atr14),
            'daily_close_vs_ema20_atr': float((dr.close-dr.ema20)/dr.atr14),
            'daily_close_vs_ema50_atr': float((dr.close-dr.ema50)/dr.atr14),
            'daily_close_vs_ema200_pct': float((dr.close/dr.ema200-1.0)*100.0),
            'daily_drawdown_from_prior20high_pct': float((dr.close/dr.prior20_high-1.0)*100.0),
            'days_below_ema20': consecutive_true(df.close < df.ema20, di),
            'days_below_ema50': consecutive_true(df.close < df.ema50, di),
            'days_below_ema200': consecutive_true(df.close < df.ema200, di),
            'days_since_60d_high': days_since_60d_high,
        }
        for days in [1,3,7,14]:
            z = h1[(h1.index >= t) & (h1.index <= t + pd.Timedelta(days=days))]
            out[f'mae_{days}d_R'] = float((z.high.max()-q.seed_entry)/risk) if len(z) else None
            out[f'mfe_{days}d_R'] = float((q.seed_entry-z.low.min())/risk) if len(z) else None
        rows.append(out)

    f = pd.DataFrame(rows).sort_values('seed_time')
    f.to_csv(OUT/'bear_short16_cases.csv', index=False)
    losers = f[~f.winner]
    winners = f[f.winner]
    cols = [
        'stop_dist_pct','h4_volume_ratio','h4_break_depth_atr','daily_dist_prior20low_atr',
        'daily_close_vs_ema20_atr','daily_close_vs_ema50_atr','daily_close_vs_ema200_pct',
        'daily_drawdown_from_prior20high_pct','days_below_ema20','days_below_ema50',
        'days_below_ema200','days_since_60d_high','mae_1d_R','mfe_1d_R','mae_3d_R','mfe_3d_R','mae_7d_R','mfe_7d_R'
    ]
    med = []
    for c in cols:
        med.append({'feature': c, 'loser_median': float(pd.to_numeric(losers[c]).median()), 'winner_median': float(pd.to_numeric(winners[c]).median())})
    pd.DataFrame(med).to_csv(OUT/'winner_loser_medians.csv', index=False)

    corr = []
    for c in cols:
        z = f[[c,'R']].dropna()
        corr.append({'feature': c, 'spearman_R': float(z.corr(method='spearman').iloc[0,1]) if len(z) >= 3 else None})
    pd.DataFrame(corr).sort_values('spearman_R').to_csv(OUT/'feature_spearman.csv', index=False)

    structural = losers[losers.exit_reason == 'STRUCTURAL_STOP']
    summary = {
        'status': 'R21_BEAR_SHORT16_FAST_FORENSIC_HISTORICAL_DIAGNOSTIC_ONLY',
        'model': 'MASTER_BTC_TREND_V3_R2_1',
        'source_identity': EXPECTED,
        'identity_checks': {'short_total': int(len(shorts)), 'bear_short_n': int(len(bear)), 'bear_short_mean_R': mean_r},
        'outcome': {
            'wins': int(f.winner.sum()), 'losses': int((~f.winner).sum()), 'win_rate': float(f.winner.mean()), 'mean_R': mean_r,
            'route_counts': f.route.value_counts().to_dict(), 'exit_reason_counts': f.exit_reason.value_counts().to_dict(),
            'structural_stop_losers': int(len(structural)),
            'structural_stops_within_1d': int((structural.days_to_exit <= 1).sum()),
            'structural_stops_within_3d': int((structural.days_to_exit <= 3).sum()),
            'structural_stops_within_7d': int((structural.days_to_exit <= 7).sum()),
            'structural_stops_within_14d': int((structural.days_to_exit <= 14).sum()),
            'loser_median_days_to_exit': float(losers.days_to_exit.median()),
            'winner_median_days_to_exit': float(winners.days_to_exit.median()),
        },
        'winner_loser_medians': {r['feature']: {'loser': r['loser_median'], 'winner': r['winner_median']} for r in med},
        'diagnostic_interpretation': {
            'primary': 'BEAR SHORT losses are dominated by rapid post-breakdown mean reversion / structural-stop events, not by long slow trend invalidation.',
            'maturity_signal': 'Winners occurred with materially fewer consecutive days below EMA200 and a smaller negative distance from EMA200 than losers; winner n=3 is small, so this is a hypothesis not a rule.',
            'local_break_quality': 'Winners show descriptively higher 4H volume ratio and a Daily close nearer the prior 20D low; this suggests decisive local participation matters, but no threshold is adopted.',
            'forbidden': 'No post-hoc threshold, BEAR veto, EMA200 distance rule, or volume rule may be added to frozen R2.1 from this forensic.'
        },
        'evidence_firewall': [
            'This forensic uses the same 2021-2026 historical window and cannot promote production.',
            'Winner sample is only 3; descriptive winner/loser differences are not causal proof.',
            'Frozen R2.1 remains unchanged.',
            'Any new execution rule requires a new separately frozen candidate before replay.'
        ]
    }
    (OUT/'bear_short16_audit.json').write_text(json.dumps(summary, indent=2, default=str), encoding='utf-8')
    print(json.dumps(summary, indent=2, default=str))


if __name__ == '__main__':
    main()
