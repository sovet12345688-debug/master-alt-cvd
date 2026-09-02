from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
DERIV = ROOT / "derivatives"
DATA_DIR = DERIV / "data"
OUT_DIR = DERIV / "output"
STATE_DIR = DERIV / "state"
CONFIG_PATH = DERIV / "config.json"
ALT2_CONFIG_PATH = ROOT / "final20_config.json"
HISTORY_PATH = DATA_DIR / "hourly_derivatives.csv"
SUMMARY_JSON = OUT_DIR / "latest_summary.json"
SUMMARY_CSV = OUT_DIR / "latest_summary.csv"
STATE_PATH = STATE_DIR / "collector_state.json"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "MASTER-DERIVATIVES-HISTORY/1.1"})


@dataclass
class Snapshot:
    time_utc: str
    venue: str
    symbol: str
    mark_price: float | None
    open_interest_contracts: float | None
    open_interest_usdt: float | None
    last_funding_rate: float | None
    next_funding_time_utc: str | None
    status: str
    error: str | None = None


def now_hour_utc() -> datetime:
    n = datetime.now(timezone.utc)
    return n.replace(minute=0, second=0, microsecond=0)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def safe_float(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def get_url_json(url: str, params: dict[str, Any] | None = None) -> Any:
    r = SESSION.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def load_config() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {"provider_priority": ["bitget_usdt_futures", "bybit_linear"]}


def load_universe() -> list[str]:
    symbols: list[str] = ["BTCUSDT"]
    if ALT2_CONFIG_PATH.exists():
        raw = json.loads(ALT2_CONFIG_PATH.read_text(encoding="utf-8"))
        candidates: list[str] = []
        if isinstance(raw, dict):
            for key in ("symbols", "final20", "tickers", "universe", "assets"):
                val = raw.get(key)
                if isinstance(val, list):
                    candidates.extend(str(x) for x in val)
            if not candidates:
                def walk(obj: Any) -> None:
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if k.lower() in {"symbol", "ticker", "asset"} and isinstance(v, str):
                                candidates.append(v)
                            else:
                                walk(v)
                    elif isinstance(obj, list):
                        for x in obj:
                            walk(x)
                walk(raw)
        for c in candidates:
            s = c.upper().strip().replace("/USDT", "USDT").replace("-USDT", "USDT").replace("_USDT", "USDT")
            if s and not s.endswith("USDT"):
                s += "USDT"
            if s.endswith("USDT"):
                symbols.append(s)
    out: list[str] = []
    for s in symbols:
        if s not in out:
            out.append(s)
    return out


def ts_ms_to_iso(v: Any) -> str | None:
    try:
        ms = int(v)
        if ms <= 0:
            return None
        return iso(datetime.fromtimestamp(ms / 1000, tz=timezone.utc))
    except (TypeError, ValueError, OSError):
        return None


def fetch_bitget_market() -> dict[str, dict[str, Any]]:
    data = get_url_json(
        "https://api.bitget.com/api/v3/market/tickers",
        {"category": "USDT-FUTURES"},
    )
    if str(data.get("code")) != "00000":
        raise RuntimeError(f"Bitget code={data.get('code')} msg={data.get('msg')}")
    rows = data.get("data") or []
    out: dict[str, dict[str, Any]] = {}
    for x in rows:
        symbol = str(x.get("symbol") or "").upper()
        if not symbol.endswith("USDT"):
            continue
        mark = safe_float(x.get("markPrice"))
        oi_native = safe_float(x.get("openInterest"))
        oi_usdt = oi_native * mark if oi_native is not None and mark is not None else None
        out[symbol] = {
            "mark_price": mark,
            "open_interest_contracts": oi_native,
            "open_interest_usdt": oi_usdt,
            "last_funding_rate": safe_float(x.get("fundingRate")),
            "next_funding_time_utc": None,
        }
    if "BTCUSDT" not in out:
        raise RuntimeError("Bitget returned no BTCUSDT USDT-futures ticker")
    return out


def fetch_bybit_market() -> dict[str, dict[str, Any]]:
    data = get_url_json(
        "https://api.bybit.com/v5/market/tickers",
        {"category": "linear"},
    )
    if int(data.get("retCode", -1)) != 0:
        raise RuntimeError(f"Bybit retCode={data.get('retCode')} retMsg={data.get('retMsg')}")
    rows = ((data.get("result") or {}).get("list") or [])
    out: dict[str, dict[str, Any]] = {}
    for x in rows:
        symbol = str(x.get("symbol") or "").upper()
        if not symbol.endswith("USDT"):
            continue
        fr = safe_float(x.get("fundingRate"))
        if fr is None:
            continue
        mark = safe_float(x.get("markPrice"))
        oi_native = safe_float(x.get("openInterest"))
        oi_usdt = safe_float(x.get("openInterestValue"))
        if oi_usdt is None and oi_native is not None and mark is not None:
            oi_usdt = oi_native * mark
        out[symbol] = {
            "mark_price": mark,
            "open_interest_contracts": oi_native,
            "open_interest_usdt": oi_usdt,
            "last_funding_rate": fr,
            "next_funding_time_utc": ts_ms_to_iso(x.get("nextFundingTime")),
        }
    if "BTCUSDT" not in out:
        raise RuntimeError("Bybit returned no BTCUSDT linear perpetual ticker")
    return out


def choose_market() -> tuple[str, dict[str, dict[str, Any]], list[str]]:
    cfg = load_config()
    priority = cfg.get("provider_priority") or ["bitget_usdt_futures", "bybit_linear"]
    errors: list[str] = []
    for provider in priority:
        try:
            if provider == "bitget_usdt_futures":
                return "Bitget USDT Futures", fetch_bitget_market(), errors
            if provider == "bybit_linear":
                return "Bybit Linear", fetch_bybit_market(), errors
            errors.append(f"Unknown provider: {provider}")
        except Exception as e:
            errors.append(f"{provider}: {type(e).__name__}: {str(e)[:220]}")
    raise RuntimeError("All derivatives providers failed | " + " | ".join(errors))


def build_latest(universe: list[str], venue: str, market: dict[str, dict[str, Any]], t: datetime) -> list[Snapshot]:
    latest: list[Snapshot] = []
    for symbol in universe:
        x = market.get(symbol)
        if not x:
            latest.append(Snapshot(
                time_utc=iso(t) or "",
                venue=venue,
                symbol=symbol,
                mark_price=None,
                open_interest_contracts=None,
                open_interest_usdt=None,
                last_funding_rate=None,
                next_funding_time_utc=None,
                status="N/A",
                error=f"No active {venue} USDT perpetual/ticker",
            ))
            continue
        status = "OK" if x.get("mark_price") is not None and x.get("open_interest_usdt") is not None else "N/A"
        latest.append(Snapshot(
            time_utc=iso(t) or "",
            venue=venue,
            symbol=symbol,
            mark_price=x.get("mark_price"),
            open_interest_contracts=x.get("open_interest_contracts"),
            open_interest_usdt=x.get("open_interest_usdt"),
            last_funding_rate=x.get("last_funding_rate"),
            next_funding_time_utc=x.get("next_funding_time_utc"),
            status=status,
            error=None if status == "OK" else f"Incomplete {venue} ticker fields",
        ))
    return latest


def read_history() -> list[dict[str, str]]:
    if not HISTORY_PATH.exists():
        return []
    with HISTORY_PATH.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_history(rows: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fields = [
        "time_utc", "venue", "symbol", "mark_price", "open_interest_contracts", "open_interest_usdt",
        "last_funding_rate", "next_funding_time_utc", "status", "error"
    ]
    with HISTORY_PATH.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def pct_change(cur: float | None, past: float | None) -> float | None:
    if cur is None or past is None or past == 0:
        return None
    return (cur / past - 1.0) * 100.0


def nearest_prior(rows: list[dict[str, str]], symbol: str, venue: str, target: datetime) -> dict[str, str] | None:
    best = None
    best_dt = None
    for r in rows:
        if r.get("symbol") != symbol or r.get("venue") != venue or r.get("status") != "OK":
            continue
        try:
            dt = datetime.fromisoformat(r["time_utc"].replace("Z", "+00:00"))
        except Exception:
            continue
        if dt <= target and (best_dt is None or dt > best_dt):
            best = r
            best_dt = dt
    return best


def build_summary(history: list[dict[str, str]], latest: list[Snapshot], t: datetime) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    windows = [1, 4, 24]
    for s in latest:
        row: dict[str, Any] = {
            "symbol": s.symbol,
            "venue": s.venue,
            "time_utc": s.time_utc,
            "status": s.status,
            "mark_price": s.mark_price,
            "open_interest_usdt": s.open_interest_usdt,
            "last_funding_rate": s.last_funding_rate,
        }
        for h in windows:
            p = nearest_prior(history, s.symbol, s.venue, t - timedelta(hours=h))
            p_oi = safe_float(p.get("open_interest_usdt")) if p else None
            p_price = safe_float(p.get("mark_price")) if p else None
            p_funding = safe_float(p.get("last_funding_rate")) if p else None
            row[f"oi_change_{h}h_pct"] = pct_change(s.open_interest_usdt, p_oi)
            row[f"price_change_{h}h_pct"] = pct_change(s.mark_price, p_price)
            row[f"funding_change_{h}h"] = (
                s.last_funding_rate - p_funding
                if s.last_funding_rate is not None and p_funding is not None
                else None
            )
        oi4 = row.get("oi_change_4h_pct")
        px4 = row.get("price_change_4h_pct")
        fr = s.last_funding_rate
        if s.status != "OK":
            label = "N/A"
        elif oi4 is None or px4 is None:
            label = "BUILDING_HISTORY"
        elif px4 > 0 and oi4 > 0:
            label = "PRICE_UP_OI_UP"
        elif px4 > 0 and oi4 < 0:
            label = "PRICE_UP_OI_DOWN_SQUEEZE_RISK"
        elif px4 < 0 and oi4 > 0:
            label = "PRICE_DOWN_OI_UP_BEARISH_BUILD"
        elif px4 < 0 and oi4 < 0:
            label = "DELEVERAGING"
        else:
            label = "MIXED"
        if fr is not None and abs(fr) >= 0.001:
            label += "|FUNDING_EXTREME"
        row["derivatives_state"] = label
        out.append(row)
    return out


def save_summary(summary: list[dict[str, Any]], t: datetime, venue: str, provider_errors: list[str]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "engine": "MASTER_DERIVATIVES_HISTORY_V1_1",
        "venue": venue,
        "snapshot_time_utc": iso(t),
        "windows_hours": [1, 4, 24],
        "provider_fallback_log": provider_errors,
        "note": "Venue-specific OI/funding. OI changes are compared only within the same venue. Not global derivatives data and not an ENTER signal by itself.",
        "assets": summary,
    }
    SUMMARY_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = [
        "symbol", "venue", "time_utc", "status", "mark_price", "open_interest_usdt", "last_funding_rate",
        "price_change_1h_pct", "oi_change_1h_pct", "funding_change_1h",
        "price_change_4h_pct", "oi_change_4h_pct", "funding_change_4h",
        "price_change_24h_pct", "oi_change_24h_pct", "funding_change_24h", "derivatives_state"
    ]
    with SUMMARY_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in summary:
            w.writerow({k: r.get(k, "") for k in fields})


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    t = now_hour_utc()
    universe = load_universe()
    venue, market, provider_errors = choose_market()
    latest = build_latest(universe, venue, market, t)

    old = read_history()
    key = {(r.get("time_utc"), r.get("venue", ""), r.get("symbol")): r for r in old}
    for s in latest:
        key[(s.time_utc, s.venue, s.symbol)] = {k: "" if v is None else v for k, v in asdict(s).items()}
    rows = list(key.values())
    rows.sort(key=lambda r: (r.get("time_utc", ""), r.get("venue", ""), r.get("symbol", "")))

    cutoff = t - timedelta(days=90)
    kept: list[dict[str, Any]] = []
    for r in rows:
        try:
            dt = datetime.fromisoformat(str(r.get("time_utc", "")).replace("Z", "+00:00"))
            if dt >= cutoff:
                kept.append(r)
        except Exception:
            kept.append(r)
    write_history(kept)

    summary = build_summary(kept, latest, t)
    save_summary(summary, t, venue, provider_errors)

    state = {
        "last_run_utc": iso(datetime.now(timezone.utc)),
        "snapshot_hour_utc": iso(t),
        "selected_venue": venue,
        "provider_fallback_log": provider_errors,
        "universe_count": len(universe),
        "ok_count": sum(x.status == "OK" for x in latest),
        "na_count": sum(x.status != "OK" for x in latest),
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
