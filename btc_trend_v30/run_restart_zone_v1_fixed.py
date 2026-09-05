from __future__ import annotations

import numpy as np
import pandas as pd
import run_restart_phase1_v1 as core

_original_rsi = core.rsi


def _fixed_rsi(s, n=14):
    if isinstance(s, pd.DataFrame):
        s = s['close']
    return _original_rsi(s, n)


core.rsi = _fixed_rsi

import run_restart_zone_v1 as zone
zone.c.rsi = _fixed_rsi


def _fixed_fit(train):
    g = train.dropna(subset=['success'])
    if len(g) < 50 or g.success.nunique() < 2:
        return None
    X = g[zone.FEATURES].astype(float).to_numpy(copy=True)
    X[:, zone.FEATURES.index('repeated_weakening')] *= -1
    y = g.success.to_numpy(dtype=float, copy=True)
    mu = X.mean(0)
    sd = X.std(0) + 1e-6
    Z = (X - mu) / sd
    w = []
    for j in range(Z.shape[1]):
        a = Z[y == 1, j]
        b = Z[y == 0, j]
        w.append(float(np.clip(a.mean() - b.mean(), -1.5, 1.5)))
    return mu, sd, np.array(w)


def _fixed_raw(model, frame):
    if model is None:
        return np.full(len(frame), np.nan)
    mu, sd, w = model
    X = frame[zone.FEATURES].astype(float).to_numpy(copy=True)
    X[:, zone.FEATURES.index('repeated_weakening')] *= -1
    Z = (X - mu) / sd
    return np.nansum(Z * w, axis=1) / (np.sum(abs(w)) + core.EPS)


zone.fit = _fixed_fit
zone.raw = _fixed_raw


def main():
    zone.main()


if __name__ == '__main__':
    main()
