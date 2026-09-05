from __future__ import annotations

import pandas as pd
import run_restart_phase1_v1 as core

_original_rsi = core.rsi


def _fixed_rsi(s, n=14):
    # The original restart prototype passed the whole OHLCV DataFrame into RSI.
    # Normalize to the close Series here without changing any production code.
    if isinstance(s, pd.DataFrame):
        s = s['close']
    return _original_rsi(s, n)


core.rsi = _fixed_rsi


def main():
    core.main()


if __name__ == '__main__':
    main()
