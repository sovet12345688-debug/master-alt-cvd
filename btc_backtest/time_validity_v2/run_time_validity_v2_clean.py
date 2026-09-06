import math
import numpy as np
import pandas as pd
import run_time_validity_v2 as m


def before_closed(df, signal_time):
    """Return only an upper-timeframe bar that was fully closed by signal_time."""
    if df.empty:
        return None
    diffs = df.index.to_series().diff().dropna()
    step = diffs.median() if not diffs.empty else pd.Timedelta(0)
    closed = df[(df.index + step) <= signal_time]
    return None if closed.empty else closed.iloc[-1]


def learn_delay_fixed(x, frame):
    x = x[x['rolling'] & x['triggered']]
    best = None
    for cutoff in [q for q in [0,1,2,3,4,6,8,12,16,24,32,48,64,96] if q <= m.SEARCH[frame]]:
        y = x[x['delay'] <= cutoff]
        if len(y) < max(20, int(len(x) * 0.6)):
            continue
        candidate = (y['R'].mean(), -cutoff, cutoff)
        if best is None or candidate > best:
            best = candidate
    return 4 if best is None else best[2]


def clock_fixed(x, limit):
    x = x[x['rolling'] & x['triggered'] & (x['delay'] <= limit) & (x['R'] > 0)]
    out = {}
    for col in ['p025', 'p05']:
        vals = pd.to_numeric(x[col], errors='coerce').dropna()
        out[col] = {
            key: (None if vals.empty else int(math.ceil(vals.quantile(q))))
            for key, q in [('p50',0.50),('p75',0.75),('p90',0.90)]
        }
    return out


def learn_we_fixed(x):
    if x.empty:
        return 50.0
    best = None
    for q in [0.3, 0.4, 0.5]:
        threshold = float(x['we'].quantile(q))
        y = x[x['we'] >= threshold]
        if len(y) < max(20, int(len(x) * 0.5)):
            continue
        candidate = (y['R'].mean(), len(y), threshold)
        if best is None or candidate > best:
            best = candidate
    return float(x['we'].quantile(0.4)) if best is None else best[2]


# Research-integrity patches only. Price setup, stop, target, trigger and outcome
# logic remain exactly as in the isolated V2 research script.
m.before = before_closed
m.learn_delay = learn_delay_fixed
m.clock = clock_fixed
m.learn_we = learn_we_fixed

if __name__ == '__main__':
    m.run()
