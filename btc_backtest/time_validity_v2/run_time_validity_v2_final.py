import json
import numpy as np
import pandas as pd
import run_time_validity_v2_clean as c

m = c.m


def run_final():
    h, bad1 = m.load('1h')
    q15, bad15 = m.load('15m')
    h = m.enrich(h)
    q15 = m.enrich(q15)
    bases = {'1H': h, '4H': m.rs(h, '4h'), '1D': m.rs(h, '1d')}
    ups = {'1H': bases['4H'], '4H': bases['1D'], '1D': m.rs(h, '1W')}
    reps = [m.replay(f, m.setups(f, bases[f], ups[f]), bases[f], q15) for f in ['1H','4H','1D']]
    allr = pd.concat(reps, ignore_index=True)
    allr.to_csv(m.OUT / 'all_proxy_replays.csv', index=False)

    b_rows, delay_rows, we_rows = [], [], []
    limits, clocks, thresholds = {}, {}, {}
    for f in ['1H','4H','1D']:
        z = allr[allr['frame'] == f]
        train, oos = z[~z['is_oos']], z[z['is_oos']]
        limit = c.learn_delay_fixed(train, f)
        limits[f] = limit
        clk = c.clock_fixed(train, limit)
        clocks[f] = clk
        p75 = clk['p025']['p75'] or 8

        b0 = oos
        b1 = oos[oos['rolling']]
        b2 = b1[b1['triggered'] & (b1['delay'] <= limit)]
        b3 = b2.copy()
        b3['w'] = np.where(pd.to_numeric(b3['p025'], errors='coerce').fillna(10**9) <= p75, 1.0, 0.4)
        b3['WR'] = b3['R'] * b3['w']

        train2 = train[train['rolling'] & train['triggered'] & (train['delay'] <= limit)]
        th = c.learn_we_fixed(train2)
        thresholds[f] = th
        b4 = b3.copy()
        b4['wp'] = b4['we'] >= th
        b4['WW'] = b4['R'] * np.where(b4['wp'], b4['w'], np.minimum(b4['w'], 0.4))

        for ver, frame_df, rcol in [('B0',b0,'R'),('B1',b1,'R'),('B2',b2,'R'),('B3',b3,'WR'),('B4-WE',b4,'WW')]:
            b_rows.append(dict(frame=f, version=ver, **m.met(frame_df, rcol)))

        q = b1.copy(); q['bucket'] = q['delay'].map(m.bucket)
        for group, y in q.groupby('bucket'):
            delay_rows.append(dict(frame=f, bucket=group, n=len(y), avg_R=float(y['R'].mean()),
                tp3_rate=float((y['label']=='TP3').mean()), sl_rate=float((y['label']=='SL').mean()),
                median_RR_trigger=float(y['rr_trigger'].median()) if y['rr_trigger'].notna().any() else None,
                avg_MFE=float(y['MFE'].mean()), avg_MAE=float(y['MAE'].mean())))

        for group, y in [('WE_PASS',b2[b2['we']>=th]),('WE_FAIL',b2[b2['we']<th])]:
            we_rows.append(dict(frame=f, group=group, threshold=th,
                retention=len(y)/len(b2) if len(b2) else None, **m.met(y)))

    pd.DataFrame(b_rows).to_csv(m.OUT / 'b0_b4_oos_2025.csv', index=False)
    pd.DataFrame(delay_rows).to_csv(m.OUT / 'trigger_delay_oos_2025.csv', index=False)
    pd.DataFrame(we_rows).to_csv(m.OUT / 'wave_energy_oos_2025.csv', index=False)
    summary = dict(method='Point-in-time PRICE proxy; 2024 train / 2025 OOS; Fibonacci Time excluded; Wave Energy upper-TF closed-candle locked',
        rows={'1h':len(h),'15m':len(q15),'replays':len(allr)}, download_failures=bad1+bad15,
        trigger_limits_15m=limits, progress_clocks_15m=clocks, wave_energy_thresholds=thresholds,
        b0_b4=b_rows, wave_energy=we_rows)
    (m.OUT / 'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == '__main__':
    run_final()
