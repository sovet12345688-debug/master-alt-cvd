from __future__ import annotations

import hashlib
import io
import json
import math
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import requests

ENGINE = "MASTER_BTC_TREND_V3_R1_2_VALIDATION_PROTOCOL_V1"
STATUS = "RESEARCH_ONLY_PROMOTION_HOLD"
AUDIT_IMPL = "1.0.1"
SYMBOL = "BTCUSDT"
INTERVAL = "1d"
START = pd.Timestamp("2017-08-17T00:00:00Z")
CUTOFF = pd.Timestamp("2026-09-04T00:00:00Z")  # inclusive completed UTC day
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
session.headers.update({"User-Agent": "master-btc-trend-v30-validation-protocol-v1/1.0.1"})


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
    """
    Binance Spot archive timestamps switch to microseconds from 2025-01-01.
    Preserve interval-end semantics by integer FLOOR division, never float rounding.
    Example: ...999999 us -> ...999 ms, not next-day ...000 ms.
    """
    x = pd.to_numeric(v, errors="coerce")
    out = pd.Series(pd.array(x, dtype="Int64"), index=v.index)
    mask = out.notna() & (out > 100_000_000_000_000)
    if mask.any():
        vals = out.loc[mask].astype("int64") // 1000
        out.loc[mask] = pd.array(vals, dtype="Int64")
    return out.astype("Int64")


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
            if r.status_code in (200, 404, 451):
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
    tok = text.strip().split()[0].strip().lower()
    return tok if len(tok) == 64 and all(c in "0123456789abcdef" for c in tok) else None


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
        return df, DownloadRecord(
            kind, key, url, checksum_url, r.status_code, cr.status_code,
            expected, actual, checksum_ok, len(df),
            None if checksum_ok else "CHECKSUM_FAIL_OR_MISSING",
        )
    except Exception as e:
        return None, DownloadRecord(
            kind, key, url, checksum_url, None, None,
            None, None, False, 0, f"{type(e).__name__}:{e}",
        )


def _month_starts(start: pd.Timestamp, end: pd.Timestamp) -> Iterable[pd.Timestamp]:
    cur = pd.Timestamp(year=start.year, month=start.month, day=1, tz="UTC")
    last = pd.Timestamp(year=end.year, month=end.month, day=1, tz="UTC")
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
    """Canonical source = Binance public Spot archive with SHA256 checksums."""
    frames: list[pd.DataFrame] = []
    recs: list[DownloadRecord] = []
    for m in _month_starts(START, CUTOFF):
        ym = m.strftime("%Y-%m")
        url = f"{ARCHIVE_BASE}/monthly/klines/{SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{ym}.zip"
        df, rec = _download_checked("monthly_1d", ym, url)
        recs.append(rec)
        if df is not None and rec.checksum_ok:
            frames.append(df)
            continue
        for d in _days_in_month_for_range(m):
            ds = d.strftime("%Y-%m-%d")
            du = f"{ARCHIVE_BASE}/daily/klines/{SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{ds}.zip"
            ddf, drec = _download_checked("daily_1d", ds, du)
            recs.append(drec)
            if ddf is not None and drec.checksum_ok:
                frames.append(ddf)
    if not frames:
        raise RuntimeError("No checksum-verified Binance 1D archive data loaded")
    x = pd.concat(frames, ignore_index=True)
    lo_ms = int(START.timestamp() * 1000)
    hi_ms = int(CUTOFF.timestamp() * 1000)
    x = x[(x.open_time >= lo_ms) & (x.open_time <= hi_ms)].copy()
    return x.sort_values(["open_time", "close_time"]).reset_index(drop=True), recs


def row_audit(x: pd.DataFrame) -> pd.DataFrame:
    z = x.copy()
    z["date"] = pd.to_datetime(z.open_time.astype("int64"), unit="ms", utc=True)
    z["close_delta_ms"] = z.close_time.astype("int64") - z.open_time.astype("int64")
    z["open_midnight_utc"] = (z.open_time.astype("int64") % DAY_MS) == 0
    z["complete_1d_metadata"] = z.close_delta_ms == EXPECTED_CLOSE_DELTA_MS
    eps = 1e-10
    z["ohlc_ok"] = (
        (z.low <= z.high + eps) & (z.low <= z.open + eps) & (z.low <= z.close + eps)
        & (z.high + eps >= z.open) & (z.high + eps >= z.close)
    )
    z["volume_ok"] = (z.volume >= -eps) & (z.quote_volume >= -eps) & (z.taker_buy_quote >= -eps)
    z["taker_bound_ok"] = z.taker_buy_quote <= (
        z.quote_volume + np.maximum(1e-6, np.abs(z.quote_volume) * 1e-10)
    )
    z["numeric_ok"] = z[["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_quote"]].notna().all(axis=1)
    z["row_core_ok"] = z.open_midnight_utc & z.ohlc_ok & z.volume_ok & z.taker_bound_ok & z.numeric_ok
    return z


def _download_1m_month(day: pd.Timestamp) -> tuple[pd.DataFrame | None, DownloadRecord]:
    ym = day.strftime("%Y-%m")
    url = f"{ARCHIVE_BASE}/monthly/klines/{SYMBOL}/1m/{SYMBOL}-1m-{ym}.zip"
    return _download_checked("monthly_1m_repair", ym, url)


def repair_partial_days(audited: pd.DataFrame, recs: list[DownloadRecord]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    A truly short 1D metadata row is not silently accepted.
    Rebuild that UTC calendar day from checksum-verified Binance 1m archive.
    Repair is accepted only if the 1m source spans 00:00 through >=23:59 UTC.
    """
    z = audited.copy()
    z["provenance"] = "BINANCE_1D_ARCHIVE"
    logs = []
    cache: dict[str, pd.DataFrame | None] = {}
    for idx, r in z[~z.complete_1d_metadata].iterrows():
        day = pd.Timestamp(r.date).normalize()
        ym = day.strftime("%Y-%m")
        if ym not in cache:
            m1, rec = _download_1m_month(day)
            recs.append(rec)
            cache[ym] = m1 if m1 is not None and rec.checksum_ok else None
        m1 = cache[ym]
        log = {
            "date": day.isoformat(), "original_close_delta_ms": int(r.close_delta_ms),
            "repair_source": "checksum_verified_binance_1m_monthly", "repair_ok": False,
            "minute_bars": 0, "first_minute": None, "last_minute": None,
            "span_start_ok": False, "span_end_ok": False, "changed_required_fields": None,
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
            log["note"] = "no 1m bars for UTC day"
            logs.append(log)
            continue
        first_ms = int(q.iloc[0].open_time)
        last_ms = int(q.iloc[-1].open_time)
        span_start_ok = first_ms == lo
        span_end_ok = last_ms >= hi - 60_000
        log.update({
            "minute_bars": int(len(q)),
            "first_minute": pd.to_datetime(first_ms, unit="ms", utc=True).isoformat(),
            "last_minute": pd.to_datetime(last_ms, unit="ms", utc=True).isoformat(),
            "span_start_ok": bool(span_start_ok), "span_end_ok": bool(span_end_ok),
        })
        if not (span_start_ok and span_end_ok):
            log["note"] = "1m source does not span full UTC calendar day"
            logs.append(log)
            continue
        agg = {
            "open": float(q.iloc[0].open), "high": float(q.high.max()), "low": float(q.low.min()),
            "close": float(q.iloc[-1].close), "volume": float(q.volume.sum()),
            "quote_volume": float(q.quote_volume.sum()), "taker_buy_quote": float(q.taker_buy_quote.sum()),
        }
        changed = [c for c, v in agg.items() if not math.isclose(float(r[c]), v, rel_tol=1e-9, abs_tol=1e-8)]
        for c, v in agg.items():
            z.at[idx, c] = v
        z.at[idx, "close_time"] = lo + EXPECTED_CLOSE_DELTA_MS
        z.at[idx, "provenance"] = "RECONSTRUCTED_FULL_UTC_DAY_FROM_1M"
        log.update({"repair_ok": True, "changed_required_fields": ",".join(changed), "note": "full UTC-day 1m aggregation"})
        logs.append(log)
    repaired = row_audit(z)
    repaired["provenance"] = z["provenance"].values
    return repaired, pd.DataFrame(logs)


def fetch_api_1d() -> pd.DataFrame:
    """Diagnostic cross-check only; GitHub hosted runners may be geo-restricted (HTTP 451)."""
    rows = []
    start_ms = int(START.timestamp() * 1000)
    end_excl = int((CUTOFF + pd.Timedelta(days=1)).timestamp() * 1000)
    cur = start_ms
    while cur < end_excl:
        r = session.get(API_BASE, params={
            "symbol": SYMBOL, "interval": INTERVAL, "startTime": cur,
            "endTime": end_excl - 1, "limit": 1000,
        }, timeout=45)
        if r.status_code != 200:
            raise RuntimeError(f"Binance API HTTP {r.status_code}: {r.text[:250]}")
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
    return x[(x.open_time >= start_ms) & (x.open_time < end_excl)].sort_values("open_time").reset_index(drop=True)


def compare_api_core(matrix: pd.DataFrame, api: pd.DataFrame) -> pd.DataFrame:
    cols = [c for c in REQUIRED if c != "open_time"]
    a = matrix[["open_time", "provenance"] + cols].copy()
    b = api[["open_time"] + cols].copy()
    m = a.merge(b, on="open_time", how="outer", suffixes=("_archive", "_api"), indicator=True)
    out = []
    for _, r in m.iterrows():
        reasons = []
        if r["_merge"] != "both":
            reasons.append(str(r["_merge"]))
        else:
            for c in cols:
                av, bv = float(r[f"{c}_archive"]), float(r[f"{c}_api"])
                if not math.isclose(av, bv, rel_tol=2e-9, abs_tol=max(1e-8, abs(bv) * 2e-9)):
                    reasons.append(c)
        if reasons:
            ot = int(r.open_time)
            out.append({
                "open_time": ot, "date": pd.to_datetime(ot, unit="ms", utc=True).isoformat(),
                "provenance": r.get("provenance", None), "reasons": ",".join(reasons),
            })
    return pd.DataFrame(out)


def audit_expected_dates(x: pd.DataFrame) -> tuple[list[str], list[str], int]:
    expected = set(pd.date_range(START.normalize(), CUTOFF.normalize(), freq="D", tz="UTC"))
    actual_idx = pd.DatetimeIndex(pd.to_datetime(x.open_time.astype("int64"), unit="ms", utc=True)).normalize()
    actual = set(actual_idx)
    return (
        sorted(d.isoformat() for d in expected - actual),
        sorted(d.isoformat() for d in actual - expected),
        int(x.open_time.duplicated().sum()),
    )


def allowed_download_failures(download_df: pd.DataFrame, repairs: pd.DataFrame) -> list[dict]:
    allowed_monthly = set()
    for m in _month_starts(START, CUTOFF):
        ym = m.strftime("%Y-%m")
        mr = download_df[(download_df.kind == "monthly_1d") & (download_df.key == ym)]
        if len(mr) and not bool(mr.iloc[-1].checksum_ok):
            needed = [d.strftime("%Y-%m-%d") for d in _days_in_month_for_range(m)]
            got = download_df[(download_df.kind == "daily_1d") & (download_df.key.isin(needed)) & (download_df.checksum_ok)]
            if len(got) == len(needed):
                allowed_monthly.add(ym)
    failed = []
    for _, r in download_df[~download_df.checksum_ok].iterrows():
        if r.kind == "monthly_1d" and r.key in allowed_monthly:
            continue
        if r.kind == "monthly_1m_repair":
            # A failed repair-source download matters only if a partial day remained unrepaired.
            if len(repairs) and bool(((pd.to_datetime(repairs.date, utc=True).dt.strftime("%Y-%m") == r.key) & (~repairs.repair_ok.fillna(False))).any()):
                failed.append({"kind": r.kind, "key": r.key, "error": r.error})
            continue
        failed.append({"kind": r.kind, "key": r.key, "error": r.error})
    return failed


def main() -> None:
    raw, records = load_archive_1d()
    raw_audit = row_audit(raw)
    missing_raw, extra_raw, dup_raw = audit_expected_dates(raw_audit)

    repaired, repairs = repair_partial_days(raw_audit, records)
    missing, extra, duplicates = audit_expected_dates(repaired)
    final = repaired.sort_values("open_time").drop_duplicates("open_time", keep=False).copy()

    download_df = pd.DataFrame([asdict(r) for r in records])
    disallowed_failures = allowed_download_failures(download_df, repairs)
    raw_partial = raw_audit[~raw_audit.complete_1d_metadata].copy()
    unrepaired = repaired[~repaired.complete_1d_metadata].copy()
    core_bad = repaired[~repaired.row_core_ok].copy()
    required_nonnull = int(final[REQUIRED].isna().sum().sum())
    expected_rows = int((CUTOFF.normalize() - START.normalize()).days + 1)

    api_error = None
    api_rows = 0
    api_mismatch = pd.DataFrame()
    api_untouched_mismatch = pd.DataFrame()
    try:
        api = fetch_api_1d()
        api_rows = len(api)
        api_mismatch = compare_api_core(final, api)
        if len(api_mismatch):
            api_untouched_mismatch = api_mismatch[api_mismatch.provenance != "RECONSTRUCTED_FULL_UTC_DAY_FROM_1M"].copy()
    except Exception as e:
        api_error = f"{type(e).__name__}:{e}"

    core_checks = {
        "archive_expected_rows": len(final) == expected_rows,
        "source_date_coverage": not missing and not extra,
        "duplicates_zero": duplicates == 0,
        "required_nonnull_zero": required_nonnull == 0,
        "core_row_logic_pass": len(core_bad) == 0,
        "partial_1d_unrepaired_zero": len(unrepaired) == 0,
        "checksum_failures_zero_after_allowed_fallback": len(disallowed_failures) == 0,
        "cutoff_exact": (
            len(final) > 0
            and int(final.open_time.min()) == int(START.timestamp() * 1000)
            and int(final.open_time.max()) == int(CUTOFF.timestamp() * 1000)
        ),
    }
    # API is an added diagnostic, not a requirement in FINAL Protocol V1.0.
    # If available, any mismatch on untouched archive rows is promoted to a hard integrity failure.
    api_status = "N/A_ENV_RESTRICTED_OR_UNAVAILABLE" if api_error else ("PASS" if len(api_untouched_mismatch) == 0 else "FAIL")
    if not api_error:
        core_checks["api_untouched_rows_match"] = len(api_untouched_mismatch) == 0
    data_integrity_pass = all(core_checks.values())

    final["date"] = pd.to_datetime(final.open_time.astype("int64"), unit="ms", utc=True)
    final["taker_proxy_quote"] = 2.0 * final.taker_buy_quote - final.quote_volume
    final["taker_proxy_name"] = "SPOT_TAKER_PROXY_NOT_TRUE_CVD"
    matrix_cols = REQUIRED + ["date", "taker_proxy_quote", "taker_proxy_name", "provenance"]
    final[matrix_cols].to_csv(OUT / "btc_usdt_1d_matrix.csv", index=False)
    raw_partial[[c for c in ["date", "open_time", "close_time", "close_delta_ms"] + REQUIRED[1:] if c in raw_partial.columns]].to_csv(OUT / "raw_partial_1d_rows.csv", index=False)
    repairs.to_csv(OUT / "partial_day_repairs.csv", index=False)
    download_df.to_csv(OUT / "download_checksum_manifest.csv", index=False)
    api_mismatch.to_csv(OUT / "api_core_mismatches.csv", index=False)
    if len(core_bad):
        core_bad.to_csv(OUT / "core_row_logic_failures.csv", index=False)

    matrix_sha = hashlib.sha256(final[REQUIRED].to_csv(index=False).encode("utf-8")).hexdigest()
    summary = {
        "engine": ENGINE, "status": STATUS, "protocol": "VALIDATION_PROTOCOL_V1_0_FINAL_LOCK",
        "audit_impl": AUDIT_IMPL, "step": "1_DATA_INTEGRITY",
        "source": "Binance BTCUSDT Spot 1D public archive + SHA256 CHECKSUM",
        "start_utc": START.isoformat(), "cutoff_utc_inclusive": CUTOFF.isoformat(),
        "expected_rows": expected_rows, "final_rows": int(len(final)),
        "matrix_sha256_required_fields": matrix_sha,
        "raw": {
            "rows": int(len(raw_audit)), "missing_dates": missing_raw, "extra_dates": extra_raw,
            "duplicates": dup_raw, "partial_1d_metadata_rows": int(len(raw_partial)),
            "partial_dates": [pd.Timestamp(d).isoformat() for d in raw_partial.date.tolist()],
        },
        "repair": {
            "attempted": int(len(repairs)),
            "passed": int(repairs.repair_ok.fillna(False).sum()) if len(repairs) else 0,
            "failed": int((~repairs.repair_ok.fillna(False)).sum()) if len(repairs) else 0,
            "records": repairs.to_dict("records") if len(repairs) else [],
        },
        "final_audit": {
            "missing_dates": missing, "extra_dates": extra, "duplicates": duplicates,
            "required_nonnull_cells": required_nonnull, "core_row_logic_failures": int(len(core_bad)),
            "unrepaired_partial_rows": int(len(unrepaired)), "download_failures_disallowed": disallowed_failures,
            "core_checks": core_checks,
        },
        "api_crosscheck": {
            "policy": "diagnostic extension; FINAL Protocol V1.0 does not require API endpoint availability",
            "status": api_status, "rows": int(api_rows), "error": api_error,
            "all_mismatches": int(len(api_mismatch)), "untouched_mismatches": int(len(api_untouched_mismatch)),
            "reconstructed_exception_mismatches": int(len(api_mismatch) - len(api_untouched_mismatch)),
        },
        "data_integrity": "PASS" if data_integrity_pass else "HOLD",
        "next_step": "R1.2 LR/LC/SR/SC FULL DAILY ROLLING" if data_integrity_pass else "STOP_BEFORE_FULL_ROLLING_AND_RESOLVE_DATA_INTEGRITY",
        "probability": "확률 산출보류", "v2_6_modified": False, "promotion": "HOLD",
    }
    (OUT / "audit.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
