from __future__ import annotations

import pandas as pd
import validation_protocol_v1_step8_robustness as s8


def normalize_time_column(path):
    df = pd.read_csv(path)
    # Historical Binance 1H archives contain a few non-hour-aligned timestamps with fractional seconds.
    # Preserve the exact timestamps; only normalize the serialized string format for deterministic pandas parsing.
    ts = pd.to_datetime(df["time"], utc=True, format="mixed")
    df["time"] = ts.dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    df.to_csv(path, index=False)


if __name__ == "__main__":
    normalize_time_column(s8.H1CSV)
    normalize_time_column(s8.H4CSV)
    s8.main()
