from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
BTC_ROOT = HERE.parent
sys.path.insert(0, str(BTC_ROOT))

from r20 import r20_historical_diagnostic_replay as replay


def ohlcv_tf_mixed_iso(raw: pd.DataFrame) -> pd.DataFrame:
    """Parse canonical mixed-precision ISO timestamps without altering any values/rules."""
    x = raw.copy()
    x["time"] = pd.to_datetime(x["time"], utc=True, format="mixed")
    x = x.sort_values("time").drop_duplicates("time").set_index("time")
    return x[["open", "high", "low", "close", "volume"]].astype(float)


def main() -> None:
    # Compatibility-only patch for pandas>=3 mixed ISO precision.
    # No threshold, feature, state, entry, exit, risk, or performance rule changes.
    replay.ohlcv_tf = ohlcv_tf_mixed_iso
    replay.main()


if __name__ == "__main__":
    main()
