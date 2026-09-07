from __future__ import annotations

import hashlib
import io
import json
import math
import time
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import requests

ENGINE = "MASTER_BTC_TREND_V3_R1_2_VALIDATION_PROTOCOL_V1"
STATUS = "RESEARCH_ONLY_PROMOTION_HOLD"
SYMBOL = "BTCUSDT"
INTERVAL = "1d"
START = pd.Timestamp("2017-08-17T00:00:00Z")
CUTOFF = pd.Timestamp("2026-09-04T00:00:00Z")  # inclusive completed 1D cutoff
DAY_MS = 86_400_000
EXPECTED_CLOSE_DELTA_MS = DAY_MS - 1
OUT = Path("btc_trend_v30/output/validation_v1/data_integrity")
OUT.mkdir(parents=True, exist_ok=True)

COLS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
]
REQUIRED = [
    "open_time", "open", "high", "low", "close", "volume",
    "quote_volume", "taker_buy_quote",
]
ARCHIVE_BASE = "https://data.binance.vision/data/spot"
API_BASE = "https://api.binance.com/api/v3/klines"

session = requests.Session()
session.headers.update({"User-Agent": "master-btc-trend-v30-validation-protocol-v1/1.0"})


@dataclass
class DownloadRecord:
    kind: str
    key: str
    url: str
    checksum_url: str
    http_status: int | None
    checksum_http_status: int | None
    checksum_expected: str | None
    checksum_actual: str | None
    checksum_ok: bool
    rows: int
    error: str | None = None


def _norm_epoch_ms(v: pd.Series) -> pd.Series:
    """Normalize Binance archive timestamps: >=2025 spot archive can be microseconds."""
    x = pd.to_numeric(v, errors="coerce").astype("float64")
    x = np.where(x > 1e14, x / 1000.0, x)
    return pd.Series(x, index=v.index).round().astype("Int64")


def _numeric_frame(raw: pd.DataFrame) -> pd.DataFrame:
    x = raw.copy()
    x["open_time"] = _norm_epoch_ms(x["open_time"])
    x["close_time"] = _norm_epoch_ms(x["close_time"])
    for c in ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_quote"]:
        x[c] = pd.to_numeric(x[c], errors="coerce")
    x["trades"] = pd.to_numeric(x["trades"], errors="coerce")
    return x


def _get(url: str, timeout: int = 60, attempts: int = 4) -> requests.Response:
    last = None
    for k in range(attempts):
        try:
            r = session.get(url, timeout=timeout)
            last = r
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return r
        except Exception as e:
            last = e
        time.sleep(1.5 * (k + 1))
    if isinstance(last, requests.Response):
        return last
    raise RuntimeError(f"GET failed {url}: {type(last).__name__}: {last}")


def _parse_checksum(text: str) -> str | None:
    if not text:
        return None
    tok = text.strip().split()[0].strip()
    if len(tok) == 64 and all(c in "0123456789abcdefABCDEF" for c in tok):
        return tok.lower()
    return None


def _read_zip_csv(blob: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise RuntimeError("zip contains no CSV")
        raw = pd.read_csv(z.open(names[0]), header=None, names=COLS)
    return _numeric_frame(raw)


def _download_checked(kind: str, key: str, url: str) -> tuple[pd.DataFrame | None, DownloadRecord]:
    checksum_url = url + ".CHECKSUM"
    try:
        r = _get(url)
        cr = _get(checksum_url)
        if r.status_code != 200:
            return None, DownloadRecord(
                kind, key, url, checksum_url, r.status_code, cr.status_code,
                None, None, False, 0, f"HTTP_{r.status_code}"
            )
        expected = _parse_checksum(cr.text) if cr.status_code == 200 else None
        actual = hashlib.sha256(r.content).hexdigest()
        checksum_ok = expected is not None and actual == expected
        df = _read_zip_csv(r.content)
        rec = DownloadRecord(
            kind, key, url, checksum_url, r.status_code, cr.status_code,
            expected, actual, checksum_ok, len(df),
            None if checksum_ok else "CHECKSUM_FAIL_OR_MISSING"
        )
        return df, rec
    except Exception as e:
        return None, DownloadRecord(
            kind, key, url, checksum_url, None, None,
            None, None, False, 0, f"{type(e).__name__}:{e}"
        )


def _month_starts(start: pd.Timestamp, end: pd.Timestamp) -> Iterable[pd.Timestamp]:
    cur = pd.Timestamp(start.year, start.month, 1, tz="UTC")
    last = pd.Timestamp(end.year, end.month, 1, tz="UTC")
    while cur <= last:
        yield cur
        cur = cur + pd.offsets.MonthBegin(1)


def _days_in_month_for_range(m: pd.Timestamp) -> list[pd.Timestamp]:
    m2 = m + pd.offsets.MonthBegin(1)
    lo = max(START, m)
    hi = min(CUTOFF, m2 - pd.Timedelta(days=1))
    if hi < lo:
        return []
    return list(pd.date_range(lo.normalize(), hi.normalize(), freq="D", tz="UTC"))


def load_archive_1d() -> tuple[pd.DataFrame, list[DownloadRecord]]:
    """
    Canonical source: checksum-verified Binance public archive.
    Prefer monthly archive; if current/incomplete month is unavailable, fallback to daily zips.
    """
    frames: list[pd.DataFrame] = []
    recs: list[DownloadRecord] = []

    for m in _month_starts(START, CUTOFF):
        ym = m.strftime("%Y-%m")
        monthly = (
            f"{ARCHIVE_BASE}/monthly/klines/{SYMBOL}/{INTERVAL}/"
            f"{SYMBOL}-{INTERVAL}-{ym}.zip"
        )
        df, rec = _download_checked("monthly_1d", ym, monthly)
        recs.append(rec)

        if df is not None and rec.checksum_ok:
            frames.append(df)
            continue

        for d in _days_in_month_for_range(m):
            ds = d.strftime("%Y-%m-%d")
            daily = (
                f"{ARCHIVE_BASE}/daily/klines/{SYMBOL}/{INTERVAL}/"
                f"{SYMBOL}-{INTERVAL}-{ds}.zip"
            )
            ddf, drec = _download_checked("daily_1d", ds, daily)
            recs.append(drec)
            if ddf is not None and drec.checksum_ok:
                frames.append(ddf)

    if not frames:
        raise RuntimeError("No checksum-verified Binance 1D archive data loaded")

    x = pd.concat(frames, ignore_index=True)
    lo_ms = int(START.timestamp() * 1000)
    hi_ms = int(CUTOFF.timestamp() * 1000)
    x = x[(x.open_time >= lo_ms) & (x.open_time <= hi_ms)].copy()
    x = x.sort_values(["open_time", "close_time"]).reset_index(drop=True)
    return x, recs


def fetch_api_1d() -> pd.DataFrame:
    """Independent endpoint cross-check. API is not used as the canonical archive source."""
    rows = []
    start_ms = int(START.timestamp() * 1000)
    end_exclusive = int((CUTOFF + pd.Timedelta(days=1)).timestamp() * 1000)
    cur = start_ms

    while cur < end_exclusive:
        params = {
            "symbol": SYMBOL,
            "interval": INTERVAL,
            "startTime": cur,
            "endTime": end_exclusive - 1,
            "limit": 1000,
        }
        r = session.get(API_BASE, params=params, timeout=45)
        if r.status_code != 200:
            raise RuntimeError(f"Binance API HTTP {r.status_code}: {r.text[:200]}")
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        nxt = int(batch[-1][0]) + DAY_MS
        if nxt <= cur:
            raise RuntimeError("API pagination did not advance")
        cur = nxt
        time.sleep(0.05)

    raw = pd.DataFrame(rows, columns=COLS)
    x = _numeric_frame(raw)
    x = x[(x.open_time >= start_ms) & (x.open_time < end_exclusive)].copy()
    x = x.sort_values("open_time").reset_index(drop=True)
    return x


def row_audit(x: pd.DataFrame) -> pd.DataFrame:
    z = x.copy()
    z["date"] = pd.to_datetime(z.open_time.astype("int64"), unit="ms", utc=True)
    z["close_delta_ms"] = z.close_time.astype("int64") - z.open_time.astype("int64")
    z["open_midnight_utc"] = (z.open_time.astype("int64") % DAY_MS) == 0
    z["complete_1d_metadata"] = z.close_delta_ms == EXPECTED_CLOSE_DELTA_MS

    eps = 1e-10
    z["ohlc_ok"] = (
        (z.low <= z.high + eps)
        & (z.low <= z.open + eps)
        & (z.low <= z.close + eps)
        & (z.high + eps >= z.open)
        & (z.high + eps >= z.close)
    )
    z["volume_ok"] = (
        (z.volume >= -eps)
        & (z.quote_volume >= -eps)
        & (z.taker_buy_quote >= -eps)
    )
    z["taker_bound_ok"] = z.taker_buy_quote <= (
        z.quote_volume + np.maximum(1e-6, np.abs(z.quote_volume) * 1e-10)
    )
    z["numeric_ok"] = z[[
        "open", "high", "low", "close", "volume", "quote_volume", "taker_buy_quote"
    ]].notna().all(axis=1)
    z["row_core_ok"] = (
        z.open_midnight_utc & z.ohlc_ok & z.volume_ok & z.taker_bound_ok & z.numeric_ok
    )
    return z


def _download_1m_month_for_date(day: pd.Timestamp) -> tuple[pd.DataFrame | None, DownloadRecord]:
    ym = day.strftime("%Y-%m")
    url = (
        f"{ARCHIVE_BASE}/monthly/klines/{SYMBOL}/1m/"
        f"{SYMBOL}-1m-{ym}.zip"
    )
    return _download_checked("monthly_1m_repair", ym, url)


def repair_partial_days(
    audited: pd.DataFrame,
    recs: list[DownloadRecord],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Historical exchange outages can leave a 1D row whose close_time is earlier than 23:59:59.999.
    Under the FINAL protocol we do not silently accept it.

    Repair policy:
    - Download checksum-verified 1m archive for the affected month.
    - Aggregate ALL 1m bars whose open_time belongs to that UTC calendar day.
    - Replace OHLCV/quote/taker with that full-day aggregate.
    - Set close_time to calendar day end only in the reconstructed research matrix.
    - Preserve provenance and minute coverage in repair_log.
    """
    z = audited.copy()
    partial = z[~z.complete_1d_metadata].copy()
    logs = []
    month_cache: dict[str, pd.DataFrame | None] = {}

    for idx, r in partial.iterrows():
        day = pd.Timestamp(r.date).normalize()
        ym = day.strftime("%Y-%m")
        if ym not in month_cache:
            m1, rec = _download_1m_month_for_date(day)
            recs.append(rec)
            month_cache[ym] = m1 if (m1 is not None and rec.checksum_ok) else None
        m1 = month_cache[ym]

        log = {
            "date": day.isoformat(),
            "original_close_delta_ms": int(r.close_delta_ms),
            "repair_source": "checksum_verified_binance_1m_monthly",
            "repair_ok": False,
            "minute_bars": 0,
            "first_minute": None,
            "last_minute": None,
            "changed_ohlcv": None,
            "note": None,
        }
        if m1 is None or m1.empty:
            log["note"] = "1m repair source unavailable"
            logs.append(log)
            continue

        lo = int(day.timestamp() * 1000)
        hi = lo + DAY_MS
        q = m1[(m1.open_time >= lo) & (m1.open_time < hi)].sort_values("open_time").copy()
        if q.empty:
            log["note"] = "no 1m bars for partial day"
            logs.append(log)
            continue

        agg = {
            "open": float(q.iloc[0].open),
            "high": float(q.high.max()),
            "low": float(q.low.min()),
            "close": float(q.iloc[-1].close),
            "volume": float(q.volume.sum()),
            "quote_volume": float(q.quote_volume.sum()),
            "taker_buy_quote": float(q.taker_buy_quote.sum()),
        }
        changed = any(
            not math.isclose(float(r[c]), float(agg[c]), rel_tol=1e-9, abs_tol=1e-8)
            for c in agg
        )
        for c, v in agg.items():
            z.at[idx, c] = v
        z.at[idx, "close_time"] = lo + EXPECTED_CLOSE_DELTA_MS
        z.at[idx, "close_delta_ms"] = EXPECTED_CLOSE_DELTA_MS
        z.at[idx, "complete_1d_metadata"] = True
        z.at[idx, "provenance"] = "RECONSTRUCTED_FULL_UTC_DAY_FROM_1M"

        log.update({
            "repair_ok": True,
            "minute_bars": int(len(q)),
            "first_minute": pd.to_datetime(int(q.iloc[0].open_time), unit="ms", utc=True).isoformat(),
            "last_minute": pd.to_datetime(int(q.iloc[-1].open_time), unit="ms", utc=True).isoformat(),
            "changed_ohlcv": bool(changed),
            "note": "full UTC calendar day aggregated from all available Binance 1m bars",
        })
        logs.append(log)

    if "provenance" not in z.columns:
        z["provenance"] = "BINANCE_1D_ARCHIVE"
    z["provenance"] = z["provenance"].fillna("BINANCE_1D_ARCHIVE")
    repaired = row_audit(z)
    repaired["provenance"] = z["provenance"].values
    return repaired, pd.DataFrame(logs)


def compare_api_core(matrix: pd.DataFrame, api: pd.DataFrame) -> pd.DataFrame:
    a = matrix[["open_time"] + [c for c in REQUIRED if c != "open_time"]].copy()
    b = api[["open_time"] + [c for c in REQUIRED if c != "open_time"]].copy()
    m = a.merge(b, on="open_time", how="outer", suffixes=("_archive", "_api"), indicator=True)
    out = []
    value_cols = [c for c in REQUIRED if c != "open_time"]

    for _, r in m.iterrows():
        reasons = []
        if r["_merge"] != "both":
            reasons.append(str(r["_merge"]))
        else:
            for c in value_cols:
                x = float(r[f"{c}_archive"])
                y = float(r[f"{c}_api"])
                tol = max(1e-8, abs(y) * 2e-9)
                if not math.isclose(x, y, rel_tol=2e-9, abs_tol=tol):
                    reasons.append(c)
        if reasons:
            out.append({
                "open_time": int(r.open_time),
                "date": pd.to_datetime(int(r.open_time), unit="ms", utc=True).isoformat(),
                "reasons": ",".join(reasons),
            })
    return pd.DataFrame(out)


def audit_expected_dates(x: pd.DataFrame) -> tuple[list[str], list[str], int]:
    expected = pd.date_range(START.normalize(), CUTOFF.normalize(), freq="D", tz="UTC")
    actual = pd.DatetimeIndex(
        pd.to_datetime(x.open_time.astype("int64"), unit="ms", utc=True)
    ).normalize()
    exp = set(expected)
    act = set(actual)
    missing = sorted(d.isoformat() for d in exp - act)
    extra = sorted(d.isoformat() for d in act - exp)
    dup = int(x.open_time.duplicated().sum())
    return missing, extra, dup


def main() -> None:
    raw, download_records = load_archive_1d()
    raw_audit = row_audit(raw)

    missing_raw, extra_raw, dup_raw = audit_expected_dates(raw_audit)

    repaired, repairs = repair_partial_days(raw_audit, download_records)
    missing, extra, duplicates = audit_expected_dates(repaired)

    final = repaired.sort_values("open_time").drop_duplicates("open_time", keep=False).copy()

    api_error = None
    api_mismatch = pd.DataFrame()
    api_rows = 0
    try:
        api = fetch_api_1d()
        api_rows = len(api)
        api_mismatch = compare_api_core(final, api)
    except Exception as e:
        api_error = f"{type(e).__name__}:{e}"

    download_df = pd.DataFrame([asdict(r) for r in download_records])
    bad_downloads = download_df[~download_df.checksum_ok].copy()
    allowed_monthly_fallbacks = set()
    for m in _month_starts(START, CUTOFF):
        ym = m.strftime("%Y-%m")
        mrow = download_df[(download_df.kind == "monthly_1d") & (download_df.key == ym)]
        if len(mrow) and not bool(mrow.iloc[-1].checksum_ok):
            needed = [d.strftime("%Y-%m-%d") for d in _days_in_month_for_range(m)]
            got = download_df[
                (download_df.kind == "daily_1d")
                & (download_df.key.isin(needed))
                & (download_df.checksum_ok)
            ]
            if len(got) == len(needed):
                allowed_monthly_fallbacks.add(ym)

    disallowed_download_failures = []
    for _, r in bad_downloads.iterrows():
        if r.kind == "monthly_1d" and r.key in allowed_monthly_fallbacks:
            continue
        if r.kind == "monthly_1m_repair":
            day_months = set(pd.to_datetime(repairs.date, utc=True).dt.strftime("%Y-%m")) if len(repairs) else set()
            if r.key not in day_months:
                continue
        disallowed_download_failures.append({
            "kind": r.kind, "key": r.key, "error": r.error
        })

    raw_partial = raw_audit[~raw_audit.complete_1d_metadata][[
        "date", "open_time", "close_time", "close_delta_ms",
        "open", "high", "low", "close", "volume", "quote_volume", "taker_buy_quote"
    ]].copy()
    unrepaired_partial = repaired[~repaired.complete_1d_metadata].copy()
    core_bad = repaired[~repaired.row_core_ok].copy()

    required_nonnull = int(final[REQUIRED].isna().sum().sum())
    expected_rows = int((CUTOFF.normalize() - START.normalize()).days + 1)

    checks = {
        "archive_expected_rows": len(final) == expected_rows,
        "source_date_coverage": not missing and not extra,
        "duplicates_zero": duplicates == 0,
        "required_nonnull_zero": required_nonnull == 0,
        "core_row_logic_pass": len(core_bad) == 0,
        "partial_1d_unrepaired_zero": len(unrepaired_partial) == 0,
        "checksum_failures_zero_after_allowed_fallback": len(disallowed_download_failures) == 0,
        "api_crosscheck_available": api_error is None,
        "api_core_mismatch_zero": api_error is None and len(api_mismatch) == 0,
        "cutoff_exact": (
            len(final) > 0
            and int(final.open_time.min()) == int(START.timestamp() * 1000)
            and int(final.open_time.max()) == int(CUTOFF.timestamp() * 1000)
        ),
    }
    data_integrity_pass = all(checks.values())

    final["date"] = pd.to_datetime(final.open_time.astype("int64"), unit="ms", utc=True)
    final["taker_proxy_quote"] = 2.0 * final.taker_buy_quote - final.quote_volume
    final["taker_proxy_name"] = "SPOT_TAKER_PROXY_NOT_TRUE_CVD"

    matrix_cols = REQUIRED + [
        "date", "taker_proxy_quote", "taker_proxy_name", "provenance"
    ]
    final[matrix_cols].to_csv(OUT / "btc_usdt_1d_matrix.csv", index=False)
    raw_partial.to_csv(OUT / "raw_partial_1d_rows.csv", index=False)
    repairs.to_csv(OUT / "partial_day_repairs.csv", index=False)
    download_df.to_csv(OUT / "download_checksum_manifest.csv", index=False)
    api_mismatch.to_csv(OUT / "api_core_mismatches.csv", index=False)
    if len(core_bad):
        core_bad.to_csv(OUT / "core_row_logic_failures.csv", index=False)

    payload = final[REQUIRED].to_csv(index=False).encode("utf-8")
    matrix_sha256 = hashlib.sha256(payload).hexdigest()

    summary = {
        "engine": ENGINE,
        "status": STATUS,
        "protocol": "VALIDATION_PROTOCOL_V1_0_FINAL_LOCK",
        "step": "1_DATA_INTEGRITY",
        "source": "Binance BTCUSDT Spot 1D public archive + SHA256 CHECKSUM; API core cross-check",
        "start_utc": START.isoformat(),
        "cutoff_utc_inclusive": CUTOFF.isoformat(),
        "expected_rows": expected_rows,
        "final_rows": int(len(final)),
        "matrix_sha256_required_fields": matrix_sha256,
        "raw": {
            "rows": int(len(raw_audit)),
            "missing_dates": missing_raw,
            "extra_dates": extra_raw,
            "duplicates": dup_raw,
            "partial_1d_metadata_rows": int(len(raw_partial)),
            "partial_dates": [pd.Timestamp(d).isoformat() for d in raw_partial.date.tolist()],
        },
        "repair": {
            "attempted": int(len(repairs)),
            "passed": int(repairs.repair_ok.fillna(False).sum()) if len(repairs) else 0,
            "failed": int((~repairs.repair_ok.fillna(False)).sum()) if len(repairs) else 0,
            "policy": "checksum-verified 1m full UTC-day aggregation; no silent acceptance of short historical candle",
        },
        "final_audit": {
            "missing_dates": missing,
            "extra_dates": extra,
            "duplicates": duplicates,
            "required_nonnull_cells": required_nonnull,
            "core_row_logic_failures": int(len(core_bad)),
            "unrepaired_partial_rows": int(len(unrepaired_partial)),
            "download_failures_disallowed": disallowed_download_failures,
            "api_rows": int(api_rows),
            "api_error": api_error,
            "api_core_mismatches": int(len(api_mismatch)),
            "checks": checks,
        },
        "data_integrity": "PASS" if data_integrity_pass else "HOLD",
        "next_step": (
            "R1.2 LR/LC/SR/SC FULL DAILY ROLLING"
            if data_integrity_pass
            else "STOP_BEFORE_FULL_ROLLING_AND_RESOLVE_DATA_INTEGRITY"
        ),
        "probability": "확률 산출보류",
        "v2_6_modified": False,
        "promotion": "HOLD",
    }
    (OUT / "audit.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
