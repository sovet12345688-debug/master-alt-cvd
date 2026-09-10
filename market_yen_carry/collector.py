from __future__ import annotations

import csv
import json
import time
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "market_yen_carry"
CONFIG_PATH = BASE / "config.json"
OUTPUT_PATH = BASE / "output" / "latest_yen_carry.json"

ECB_FX_URL = (
    "https://data-api.ecb.europa.eu/service/data/EXR/"
    "D.USD+JPY.EUR.SP00.A?format=csvdata&lastNObservations=20"
)
FRED_DGS10_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
MOF_JGB_CURRENT_URL = (
    "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"
)
MOF_JGB_HISTORY_URL = (
    "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/"
    "historical/jgbcme_all.csv"
)
CBOE_VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"

USER_AGENT = "MASTER-MARKET-YEN-CARRY-RISK-LITE/1.0"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def fetch_bytes(url: str, *, attempts: int = 4, timeout: int = 45) -> bytes:
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        try:
            req = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/csv,application/csv,text/plain,*/*",
                },
            )
            with urlopen(req, timeout=timeout) as response:
                return response.read()
        except Exception as exc:
            errors.append(f"attempt{attempt}:{type(exc).__name__}:{str(exc)[:160]}")
            if attempt < attempts:
                time.sleep(2 ** (attempt - 1))
    raise RuntimeError(" | ".join(errors))


def decode_csv(data: bytes) -> str:
    errors: list[str] = []
    for encoding in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError as exc:
            errors.append(f"{encoding}:{exc}")
    raise RuntimeError("CSV decode failed: " + " | ".join(errors))


def parse_date(raw: str) -> date:
    text = raw.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unsupported date: {raw!r}")


def clean_float(raw: Any) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if text in ("", ".", "-", "NA", "N/A", "nan"):
        return None
    return float(text)


def nearest_at_or_before(series: dict[date, float], target: date) -> tuple[date, float] | None:
    candidates = [d for d in series if d <= target]
    if not candidates:
        return None
    chosen = max(candidates)
    return chosen, series[chosen]


def latest_point(series: dict[date, float]) -> tuple[date, float]:
    if not series:
        raise RuntimeError("empty series")
    d = max(series)
    return d, series[d]


def pct_change(current: float, prior: float) -> float:
    if prior == 0:
        raise ZeroDivisionError("prior value is zero")
    return (current / prior - 1.0) * 100.0


def change_over_calendar_days(series: dict[date, float], days: int) -> dict[str, Any]:
    latest_date, latest_value = latest_point(series)
    prior = nearest_at_or_before(series, latest_date - timedelta(days=days))
    if prior is None:
        raise RuntimeError(f"no {days}D baseline")
    prior_date, prior_value = prior
    return {
        "current_date": latest_date.isoformat(),
        "current": latest_value,
        "prior_date": prior_date.isoformat(),
        "prior": prior_value,
        "change_pct": pct_change(latest_value, prior_value),
    }


def source_age_days(asof: date) -> int:
    return (now_utc().date() - asof).days


def ensure_fresh(name: str, asof: date, max_age_days: int) -> None:
    age_days = source_age_days(asof)
    if age_days > max_age_days:
        raise RuntimeError(f"{name} stale: {age_days} days old; max={max_age_days}")


def freshness_meta(asof: date, max_age_days: int) -> dict[str, int]:
    return {
        "source_age_days": source_age_days(asof),
        "freshness_max_age_days": max_age_days,
    }


def fetch_usdjpy_series(max_age_days: int) -> tuple[dict[date, float], dict[str, Any]]:
    text = decode_csv(fetch_bytes(ECB_FX_URL))
    rows = list(csv.DictReader(StringIO(text)))
    by_currency: dict[str, dict[date, float]] = {"USD": {}, "JPY": {}}
    for row in rows:
        currency = str(row.get("CURRENCY") or "").strip().upper()
        if currency not in by_currency:
            continue
        try:
            d = parse_date(str(row.get("TIME_PERIOD") or ""))
            value = clean_float(row.get("OBS_VALUE"))
        except Exception:
            continue
        if value is not None:
            by_currency[currency][d] = value

    common_dates = sorted(set(by_currency["USD"]) & set(by_currency["JPY"]))
    if len(common_dates) < 3:
        raise RuntimeError("ECB USD/JPY common observations insufficient")
    usdjpy = {
        d: by_currency["JPY"][d] / by_currency["USD"][d]
        for d in common_dates
        if by_currency["USD"][d] != 0
    }
    asof, value = latest_point(usdjpy)
    ensure_fresh("ECB USDJPY", asof, max_age_days)
    return usdjpy, {
        "provider": "ECB",
        "asof_date": asof.isoformat(),
        "value": value,
        "source_url": ECB_FX_URL,
        "method": "JPY per EUR divided by USD per EUR",
        **freshness_meta(asof, max_age_days),
    }


def fetch_us10y_series(max_age_days: int) -> tuple[dict[date, float], dict[str, Any]]:
    today = now_utc().date()
    params = urlencode(
        {
            "id": "DGS10",
            "cosd": (today - timedelta(days=45)).isoformat(),
            "coed": today.isoformat(),
        }
    )
    url = f"{FRED_DGS10_URL}?{params}"
    text = decode_csv(fetch_bytes(url))
    rows = list(csv.DictReader(StringIO(text)))
    series: dict[date, float] = {}
    for row in rows:
        raw_date = row.get("DATE") or row.get("observation_date")
        value = clean_float(row.get("DGS10"))
        if not raw_date or value is None:
            continue
        try:
            series[parse_date(str(raw_date))] = value
        except ValueError:
            continue
    asof, value = latest_point(series)
    ensure_fresh("US10Y", asof, max_age_days)
    return series, {
        "provider": "FRED_H15",
        "series_id": "DGS10",
        "asof_date": asof.isoformat(),
        "value_pct": value,
        "source_url": url,
        **freshness_meta(asof, max_age_days),
    }


def parse_mof_jgb_csv(text: str) -> dict[date, float]:
    raw_rows = list(csv.reader(StringIO(text)))
    header_index = None
    for i, row in enumerate(raw_rows[:10]):
        if row and str(row[0]).strip().lower() == "date":
            header_index = i
            break
    if header_index is None:
        raise RuntimeError("MOF JGB header row not found")

    header = [str(x).strip() for x in raw_rows[header_index]]
    normalized = [h.lower().replace("-", "").replace(" ", "") for h in header]
    ten_year_index = None
    for i, h in enumerate(normalized):
        if h in ("10y", "10yr", "10year", "10years"):
            ten_year_index = i
            break
    if ten_year_index is None:
        raise RuntimeError(f"MOF 10Y column not found: {header}")

    series: dict[date, float] = {}
    for row in raw_rows[header_index + 1 :]:
        if not row or len(row) <= ten_year_index:
            continue
        try:
            d = parse_date(row[0])
            value = clean_float(row[ten_year_index])
        except Exception:
            continue
        if value is not None:
            series[d] = value
    if not series:
        raise RuntimeError("MOF JGB 10Y series empty")
    return series


def fetch_jp10y_series(max_age_days: int) -> tuple[dict[date, float], dict[str, Any]]:
    errors: list[str] = []
    for url, mode in (
        (MOF_JGB_CURRENT_URL, "current"),
        (MOF_JGB_HISTORY_URL, "historical_fallback"),
    ):
        try:
            series = parse_mof_jgb_csv(decode_csv(fetch_bytes(url)))
            asof, value = latest_point(series)
            ensure_fresh("JP10Y", asof, max_age_days)
            if nearest_at_or_before(series, asof - timedelta(days=3)) is None and mode == "current":
                raise RuntimeError("current MOF CSV lacks 3D baseline")
            return series, {
                "provider": "MOF_JAPAN",
                "asof_date": asof.isoformat(),
                "value_pct": value,
                "source_url": url,
                "retrieval_mode": mode,
                **freshness_meta(asof, max_age_days),
            }
        except Exception as exc:
            errors.append(f"{mode}:{type(exc).__name__}:{str(exc)[:220]}")
    raise RuntimeError(" | ".join(errors))


def fetch_vix_series(max_age_days: int) -> tuple[dict[date, float], dict[str, Any]]:
    text = decode_csv(fetch_bytes(CBOE_VIX_URL))
    rows = list(csv.DictReader(StringIO(text)))
    series: dict[date, float] = {}
    for row in rows:
        raw_date = row.get("DATE") or row.get("Date") or row.get("date")
        raw_close = row.get("CLOSE") or row.get("Close") or row.get("close")
        value = clean_float(raw_close)
        if not raw_date or value is None:
            continue
        try:
            series[parse_date(str(raw_date))] = value
        except ValueError:
            continue
    asof, value = latest_point(series)
    ensure_fresh("VIX", asof, max_age_days)
    return series, {
        "provider": "CBOE",
        "asof_date": asof.isoformat(),
        "value": value,
        "source_url": CBOE_VIX_URL,
        **freshness_meta(asof, max_age_days),
    }


def yen_1d_score(change_pct_value: float) -> int:
    if change_pct_value <= -1.5:
        return 15
    if change_pct_value <= -1.0:
        return 10
    if change_pct_value <= -0.5:
        return 5
    return 0


def yen_3d_score(change_pct_value: float) -> int:
    if change_pct_value <= -3.5:
        return 35
    if change_pct_value <= -2.5:
        return 28
    if change_pct_value <= -1.5:
        return 20
    if change_pct_value <= -0.5:
        return 10
    return 0


def spread_score(change_bp: float) -> int:
    if change_bp <= -50:
        return 30
    if change_bp <= -30:
        return 23
    if change_bp <= -15:
        return 15
    if change_bp <= -5:
        return 8
    return 0


def vix_score(change_pct_value: float, current: float) -> int:
    if change_pct_value >= 20:
        score = 15
    elif change_pct_value >= 10:
        score = 10
    elif change_pct_value >= 5:
        score = 5
    else:
        score = 0
    if current >= 25:
        score += 5
    return min(score, 20)


def level_for_score(score: int, config: dict[str, Any]) -> tuple[str, str]:
    levels = config["levels"]
    if score <= int(levels["low_max"]):
        return "low", "낮음"
    if score <= int(levels["watch_max"]):
        return "watch", "주의"
    if score <= int(levels["caution_max"]):
        return "caution", "경계"
    return "high", "높음"


def easy_read(level: str) -> str:
    return {
        "low": "엔화·금리차·시장 공포를 함께 보면 현재 엔 캐리 청산 위험은 낮습니다.",
        "watch": "엔화 또는 금리차에 주의 신호가 있지만 아직 엔 캐리 청산 위험이 높은 단계는 아닙니다.",
        "caution": "엔화 강세와 금리차 축소가 겹치고 있어 엔 캐리 축소 가능성을 경계할 구간입니다.",
        "high": "엔화가 빠르게 강해지고 위험회피 신호도 겹쳐 엔 캐리 청산 위험이 높은 구간입니다.",
    }[level]


def build_spread_series(us10y: dict[date, float], jp10y: dict[date, float]) -> dict[date, float]:
    common = set(us10y) & set(jp10y)
    return {d: us10y[d] - jp10y[d] for d in common}


def main() -> None:
    config = load_config()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    generated = now_utc()
    fallback_max_age_days = int(config.get("max_source_age_days", 7))
    freshness = config.get("source_freshness_days", {})
    if not isinstance(freshness, dict):
        raise SystemExit("source_freshness_days must be an object")

    errors: list[str] = []
    sources: dict[str, Any] = {}
    datasets: dict[str, dict[date, float]] = {}

    fetchers = {
        "usd_jpy": fetch_usdjpy_series,
        "us10y": fetch_us10y_series,
        "jp10y": fetch_jp10y_series,
        "vix": fetch_vix_series,
    }
    effective_freshness_days: dict[str, int] = {}
    for name, fn in fetchers.items():
        max_age_days = int(freshness.get(name, fallback_max_age_days))
        if max_age_days < 0:
            raise SystemExit(f"invalid freshness threshold for {name}: {max_age_days}")
        effective_freshness_days[name] = max_age_days
        try:
            series, source = fn(max_age_days)
            datasets[name] = series
            sources[name] = source
        except Exception as exc:
            errors.append(f"{name}:{type(exc).__name__}:{str(exc)[:500]}")
            sources[name] = {
                "status": "error",
                "freshness_max_age_days": max_age_days,
                "error": str(exc)[:500],
            }

    coverage_pct = int(round(100 * len(datasets) / len(fetchers)))
    payload: dict[str, Any] = {
        "engine": config["engine"],
        "schema_version": config["schema_version"],
        "generated_at_utc": iso(generated),
        "score_weight": int(config["score_weight"]),
        "status": "partial",
        "coverage_pct": coverage_pct,
        "freshness_policy": "source-specific calendar-day guards with weekend/holiday buffer; stale required source makes this auxiliary block partial",
        "source_freshness_days": effective_freshness_days,
        "sources": sources,
        "metrics": {},
        "risk": {
            "final_score": None,
            "level": "na",
            "label_ko": "산출 불가",
        },
        "calculation_rule": (
            "1D/3D/7D use the latest available observation on or before the target calendar day; "
            "no interpolation or synthetic backfill. score_weight remains 0."
        ),
        "errors": errors,
    }

    if len(datasets) == len(fetchers):
        try:
            fx_1d = change_over_calendar_days(datasets["usd_jpy"], 1)
            fx_3d = change_over_calendar_days(datasets["usd_jpy"], 3)
            fx_7d = change_over_calendar_days(datasets["usd_jpy"], 7)

            spread = build_spread_series(datasets["us10y"], datasets["jp10y"])
            spread_3d = change_over_calendar_days(spread, 3)
            spread_change_bp = (spread_3d["current"] - spread_3d["prior"]) * 100.0

            vix_1d = change_over_calendar_days(datasets["vix"], 1)

            yen_score = yen_1d_score(fx_1d["change_pct"]) + yen_3d_score(fx_3d["change_pct"])
            rate_score = spread_score(spread_change_bp)
            fear_score = vix_score(vix_1d["change_pct"], vix_1d["current"])
            raw_score = yen_score + rate_score + fear_score

            gate = config["gate"]
            gate_applied = yen_score < int(gate["yen_score_required_for_high"])
            final_score = min(raw_score, int(gate["max_score_without_yen_trigger"])) if gate_applied else raw_score
            final_score = max(0, min(100, int(final_score)))
            level, label_ko = level_for_score(final_score, config)

            payload["status"] = "ok"
            payload["coverage_pct"] = 100
            payload["metrics"] = {
                "usd_jpy": {
                    "current": round(fx_1d["current"], 6),
                    "asof_date": fx_1d["current_date"],
                    "change_1d_pct": round(fx_1d["change_pct"], 4),
                    "change_3d_pct": round(fx_3d["change_pct"], 4),
                    "change_7d_pct": round(fx_7d["change_pct"], 4),
                    "score": yen_score,
                },
                "us_jp_10y_spread": {
                    "current_pct_point": round(spread_3d["current"], 6),
                    "asof_date": spread_3d["current_date"],
                    "prior_3d_date": spread_3d["prior_date"],
                    "change_3d_bp": round(spread_change_bp, 3),
                    "score": rate_score,
                },
                "vix": {
                    "current": round(vix_1d["current"], 6),
                    "asof_date": vix_1d["current_date"],
                    "change_1d_pct": round(vix_1d["change_pct"], 4),
                    "score": fear_score,
                },
            }
            payload["risk"] = {
                "yen_score": yen_score,
                "spread_score": rate_score,
                "vix_score": fear_score,
                "raw_score": raw_score,
                "gate_applied": gate_applied,
                "final_score": final_score,
                "level": level,
                "label_ko": label_ko,
            }
            payload["easy_read"] = easy_read(level)
        except Exception as exc:
            payload["errors"].append(f"calculation:{type(exc).__name__}:{str(exc)[:500]}")
            payload["easy_read"] = "필수 계산 기준값이 부족해 이번 회차 엔 캐리 청산 위험을 산출하지 못했습니다."
    else:
        payload["easy_read"] = "필수 공개데이터 일부가 원천별 freshness 기준을 통과하지 못했거나 수집되지 않아 이번 회차 엔 캐리 청산 위험을 산출하지 못했습니다."

    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "engine": payload["engine"],
        "status": payload["status"],
        "coverage_pct": payload["coverage_pct"],
        "source_freshness_days": payload["source_freshness_days"],
        "final_score": payload["risk"].get("final_score"),
        "errors": payload["errors"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
