from __future__ import annotations

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


def main():
    zone.main()


if __name__ == '__main__':
    main()
