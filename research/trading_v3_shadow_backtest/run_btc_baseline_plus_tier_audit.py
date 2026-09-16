from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

import run_daily_entry_v3_shadow as bt
import run_v3_ab_quick as finalv3

OUT = Path('research/trading_v3_shadow_backtest/baseline_plus_output')
OUT.mkdir(parents=True, exist_ok=True)

# Same point-in-time window as final V3 confirmation.
bt.START = pd.Timestamp('2025-03-01T00:00:00Z')
bt.TRAIN_START = pd.Timestamp('2025-06-01T00:00:00Z')
bt.END = pd.Timestamp('2026-09-01T00:00:00Z')
DEV_END = pd.Timestamp('2026-01-01T00:00:00Z')


def metrics(x: pd.DataFrame) -> dict:
    if x.empty:
        return {'n': 0}
    r = x.R.astype(float)
    neg = -r[r < 0].sum(); pos = r[r > 0].sum()
    pf = float(pos / neg) if neg > 0 else float('inf')
    eq = r.cumsum(); dd = float((eq - eq.cummax()).min())
    return {
        'n': int(len(x)),
        'expectancy_R': float(r.mean()),
        'PF': pf,
        'maxDD_R': dd,
        'win_rate': float((r > 0).mean()),
        'sl_rate': float((x.outcome == 'SL').mean()),
        'false_start_rate': float(x.false_start.mean()),
        'avg_MFE_R': float(x.MFE.mean()),
        'avg_MAE_R': float(x.MAE.mean()),
    }


def main():
    m15, failures = bt.load_15m('BTCUSDT')
    m15 = bt.add_session_vwap(bt.enrich(m15))
    h1 = bt.enrich(bt.resample(m15, '1h'))
    h4 = bt.enrich(bt.resample(m15, '4h'))
    d1 = bt.enrich(bt.resample(m15, '1d'))
    sv1 = m15.session_vwap.resample('1h', label='right', closed='right').last().dropna()
    pdl = bt.prior_day_levels(d1)

    u = finalv3.make_common_universe('BTCUSDT', m15, h1, h4, d1, sv1, pdl)
    if u.empty:
        raise RuntimeError('BTC common universe empty')
    u['split'] = np.where(pd.to_datetime(u.setup_time, utc=True) < DEV_END, 'DEV', 'OOS')
    oos = u[u.split == 'OOS'].copy()

    # Fib intentionally ignored in the overlay score. Final research showed no benefit.
    bins = [-np.inf, 54.9999, 59.9999, 64.9999, 69.9999, 74.9999, np.inf]
    labels = ['<55', '55-59', '60-64', '65-69', '70-74', '75+']
    oos['tier'] = pd.cut(oos.v3_nofib_score, bins=bins, labels=labels, right=True)

    tier_metrics = {str(t): metrics(oos[oos.tier == t]) for t in labels}
    base = metrics(oos)
    hi = metrics(oos[oos.v3_nofib_score >= 70])
    mid = metrics(oos[(oos.v3_nofib_score >= 60) & (oos.v3_nofib_score < 70)])
    low = metrics(oos[oos.v3_nofib_score < 60])

    # Overlay policy does not delete setups. It only changes execution strictness.
    policy = {
        'HIGH_70_PLUS': 'BEST priority; canonical E1 SMALL ENTER may be considered only if all existing E1 gates pass',
        'STANDARD_60_69': 'baseline treatment; completed trigger preferred',
        'LOW_BELOW_60': 'setup preserved, but require completed 15m/30m trigger + participation before execution',
        'FIB': '0 score; context-only confluence',
    }

    # Ranking-usefulness check, not replacement-engine promotion.
    useful = (
        hi.get('n', 0) >= 30 and
        hi.get('expectancy_R', -99) > base.get('expectancy_R', 99) and
        hi.get('PF', 0) > base.get('PF', 99) and
        hi.get('avg_MFE_R', 0) > base.get('avg_MFE_R', 99)
    )
    verdict = 'BASELINE+ EDGE OVERLAY SUPPORTED' if useful else 'BASELINE+ EDGE OVERLAY NOT PROVEN'

    summary = {
        'method': 'BTC baseline universe preserved; V3 no-Fib score audited only as ranking/execution-strictness overlay',
        'window': '2025-06-01..2026-08-31; OOS=2026',
        'download_failures': failures,
        'baseline_all': base,
        'high_70_plus': hi,
        'standard_60_69': mid,
        'low_below_60': low,
        'tier_metrics': tier_metrics,
        'policy': policy,
        'verdict': verdict,
        'hard_rules': [
            'CURRENT MASTER remains setup/Entry/SL/TP/Trigger authority',
            'V3 overlay cannot create/cancel baseline setup by score alone',
            'Fib contributes zero points',
            '15m/30m completed Trigger, Structural SL, >=3R, Non-Chasing, Risk Veto, Time Validity unchanged',
        ],
    }
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2, default=str), encoding='utf-8')
    oos.to_csv(OUT / 'btc_oos_tiers.csv', index=False)

    lines = [
        '# BTC BASELINE+ V3 EDGE TIER AUDIT', '', f'**{verdict}**', '',
        '| Group | n | ExpR | PF | MaxDD | Win | SL | FalseStart | MFE | MAE |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|'
    ]
    for name, m in [('BASELINE ALL', base), ('HIGH 70+', hi), ('STANDARD 60-69', mid), ('LOW <60', low)]:
        lines.append(
            f"| {name} | {m.get('n',0)} | {m.get('expectancy_R',float('nan')):.3f} | {m.get('PF',float('nan')):.2f} | "
            f"{m.get('maxDD_R',float('nan')):.2f} | {m.get('win_rate',float('nan')):.1%} | {m.get('sl_rate',float('nan')):.1%} | "
            f"{m.get('false_start_rate',float('nan')):.1%} | {m.get('avg_MFE_R',float('nan')):.2f} | {m.get('avg_MAE_R',float('nan')):.2f} |"
        )
    (OUT / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    print(verdict)
    print(pd.DataFrame([
        {'group':'BASELINE_ALL', **base},
        {'group':'HIGH_70_PLUS', **hi},
        {'group':'STANDARD_60_69', **mid},
        {'group':'LOW_BELOW_60', **low},
    ]).to_string(index=False))


if __name__ == '__main__':
    main()
