from __future__ import annotations

import json
import numpy as np
import pandas as pd

import run_v26_v040 as v040


def fetch_monthly_funding_archive_fixed(start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    """Parse Binance Data Vision funding timestamps with explicit epoch-unit detection.

    V0.4.0 exposed that pandas accepted millisecond epochs as nanoseconds, silently creating 1970 dates.
    V0.4.1 detects numeric epochs before generic datetime parsing and rejects implausible date ranges.
    """
    months = pd.date_range(start.floor("D").replace(day=1), end.floor("D").replace(day=1), freq="MS", tz="UTC")
    frames = []
    available_months = 0
    for m in months:
        x = v040._funding_month(m)
        if x is not None and not x.empty:
            available_months += 1
            frames.append(x)
    if not frames:
        return pd.DataFrame(), {"status": "ARCHIVE_UNAVAILABLE", "requested_months": int(len(months))}

    raw = pd.concat(frames, ignore_index=True, sort=False)
    tcol = v040._find_col(raw, ["calc_time", "funding_time", "fundingTime", "time", "timestamp"], ("time",))
    rcol = v040._find_col(raw, ["last_funding_rate", "funding_rate", "fundingRate"], ("funding", "rate"))
    if tcol is None or rcol is None:
        return pd.DataFrame(), {"status": "SCHEMA_UNSUPPORTED", "columns": [str(c) for c in raw.columns]}

    numeric = pd.to_numeric(raw[tcol], errors="coerce")
    numeric_share = float(numeric.notna().mean()) if len(raw) else 0.0
    unit = None
    if numeric_share >= 0.80:
        med = float(numeric.dropna().abs().median()) if numeric.notna().any() else 0.0
        if med > 1e16:
            unit = "ns"
        elif med > 1e14:
            unit = "us"
        elif med > 1e11:
            unit = "ms"
        else:
            unit = "s"
        t = pd.to_datetime(numeric, unit=unit, utc=True, errors="coerce")
    else:
        t = pd.to_datetime(raw[tcol], utc=True, errors="coerce")

    rate = pd.to_numeric(raw[rcol], errors="coerce")
    z = pd.DataFrame({"time": t, "funding_rate": rate}).dropna()
    if z.empty:
        return pd.DataFrame(), {"status": "NO_VALID_ROWS", "time_column": tcol, "rate_column": rcol, "numeric_share": round(numeric_share, 3), "epoch_unit": unit}

    # Strong sanity guard: accepted funding timestamps must overlap the requested historical window.
    lower = start - pd.Timedelta(days=31)
    upper = end + pd.Timedelta(days=31)
    plausible = z["time"].between(lower, upper)
    plausible_share = float(plausible.mean()) if len(z) else 0.0
    z = z[plausible].copy()
    if z.empty or plausible_share < 0.95:
        return pd.DataFrame(), {
            "status": "TIMESTAMP_RANGE_INVALID",
            "time_column": tcol,
            "rate_column": rcol,
            "numeric_share": round(numeric_share, 3),
            "epoch_unit": unit,
            "plausible_share": round(plausible_share, 3),
        }

    z["day"] = z["time"].dt.floor("D")
    daily = z.groupby("day").agg(funding_rate=("funding_rate", "mean"), funding_obs=("funding_rate", "size"))
    if len(daily) < 180:
        return pd.DataFrame(), {
            "status": "ARCHIVE_COVERAGE_TOO_SHORT",
            "days": int(len(daily)),
            "available_months": int(available_months),
            "numeric_share": round(numeric_share, 3),
            "epoch_unit": unit,
        }
    return daily, {
        "status": "OK",
        "days": int(len(daily)),
        "available_months": int(available_months),
        "requested_months": int(len(months)),
        "time_column": tcol,
        "rate_column": rcol,
        "numeric_share": round(numeric_share, 3),
        "epoch_unit": unit,
        "range_start": daily.index.min().date().isoformat(),
        "range_end": daily.index.max().date().isoformat(),
        "source": "Binance Data Vision fundingRate monthly archive",
    }


def main_v041() -> None:
    # Bug-fix only: no V0.4.0 acceptance gate or scoring threshold is changed.
    v040.fetch_monthly_funding_archive = fetch_monthly_funding_archive_fixed
    v040.main_v040()

    out = v040.core.OUT
    cur = json.loads((out / "current_v040_layer_b.json").read_text(encoding="utf-8"))
    val = json.loads((out / "v040_validation_summary.json").read_text(encoding="utf-8"))
    cur["engine"] = "BTC_HISTORICAL_REGIME_OUTCOME_V0_4_1"
    cur["schema_version"] = "0.4.1"
    cur["bugfix"] = "Funding calc_time epoch unit is detected before datetime conversion; implausible 1970 dates are rejected."
    val["engine"] = "BTC_HISTORICAL_REGIME_OUTCOME_V0_4_1"
    val["schema_version"] = "0.4.1"
    val["bugfix"] = "V0.4.0 funding timestamp parsing only; scoring/acceptance gates unchanged."
    (out / "current_v041_layer_b.json").write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "v041_validation_summary.json").write_text(json.dumps(val, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(val, ensure_ascii=False))


if __name__ == "__main__":
    main_v041()
