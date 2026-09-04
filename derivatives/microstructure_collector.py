from __future__ import annotations

import csv
import json
import math
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
DERIV = ROOT / "derivatives"
DATA_DIR = DERIV / "data"
OUT_DIR = DERIV / "output"
STATE_DIR = DERIV / "state"
HISTORY_PATH = DATA_DIR / "hourly_microstructure.csv"
SUMMARY_JSON = OUT_DIR / "latest_microstructure.json"
SUMMARY_CSV = OUT_DIR / "latest_microstructure.csv"
STATE_PATH = STATE_DIR / "microstructure_state.json"

VENUE = "Bitget USDT Futures"
SYMBOLS = ["BTCUSDT", "ETHUSDT"]
TRADE_WINDOW_MINUTES = 60
# BTC can exceed the old 60k-fill ceiling in a single hour. Keep this configurable,
# but never mark a capped window FULL: fetch_taker_trades still returns PARTIAL_MAX_PAGES.
MAX_TRADE_PAGES = max(60, int(os.environ.get("MAX_TRADE_PAGES", "240")))
MAX_LIQ_PAGES = 50

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "MASTER-MARKET-MICROSTRUCTURE/1.0"})


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def safe_float(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    r = SESSION.get(url, params=params, timeout=25)
    r.raise_for_status()
    return r.json()


def fetch_taker_trades(symbol: str, start_ms: int, end_ms: int) -> tuple[list[dict[str, Any]], str, list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    cursor: str | None = None
    oldest_ts: int | None = None
    seen: set[str] = set()
    completeness = "PARTIAL_UNKNOWN"

    for _ in range(MAX_TRADE_PAGES):
        params: dict[str, Any] = {
            "symbol": symbol,
            "productType": "USDT-FUTURES",
            "limit": "1000",
            "startTime": str(start_ms),
            "endTime": str(end_ms),
        }
        if cursor:
            params["idLessThan"] = cursor
        try:
            data = get_json("https://api.bitget.com/api/v2/mix/market/fills-history", params)
        except Exception as e:
            errors.append(f"trades:{type(e).__name__}:{str(e)[:160]}")
            break
        if str(data.get("code")) != "00000":
            errors.append(f"trades:code={data.get('code')} msg={data.get('msg')}")
            break
        page = data.get("data") or []
        if not page:
            completeness = "FULL" if rows else "NO_DATA"
            break

        page_added = 0
        for x in page:
            trade_id = str(x.get("tradeId") or "")
            ts = int(x.get("ts") or 0)
            if trade_id and trade_id in seen:
                continue
            if trade_id:
                seen.add(trade_id)
            if ts < start_ms or ts > end_ms:
                continue
            price = safe_float(x.get("price"))
            size = safe_float(x.get("size"))
            side = str(x.get("side") or "").lower()
            if price is None or size is None or side not in {"buy", "sell"}:
                continue
            rows.append({
                "trade_id": trade_id,
                "ts": ts,
                "price": price,
                "size": size,
                "side": side,
                "notional_usdt": price * size,
            })
            page_added += 1
            oldest_ts = ts if oldest_ts is None else min(oldest_ts, ts)

        if oldest_ts is not None and oldest_ts <= start_ms:
            completeness = "FULL"
            break
        if len(page) < 1000:
            completeness = "FULL"
            break

        next_cursor = str(page[-1].get("tradeId") or "")
        if not next_cursor or next_cursor == cursor:
            completeness = "PARTIAL_CURSOR_STOP"
            break
        cursor = next_cursor
        if page_added == 0 and oldest_ts is not None and oldest_ts < start_ms:
            completeness = "FULL"
            break
        time.sleep(0.12)
    else:
        completeness = "PARTIAL_MAX_PAGES"

    rows.sort(key=lambda x: (x["ts"], x["trade_id"]))
    return rows, completeness, errors


def fetch_basis(symbol: str) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    out = {
        "last_price": None,
        "index_price": None,
        "mark_price": None,
        "basis_bps": None,
        "price_ts_utc": None,
    }
    try:
        data = get_json(
            "https://api.bitget.com/api/v2/mix/market/symbol-price",
            {"symbol": symbol, "productType": "USDT-FUTURES"},
        )
        if str(data.get("code")) != "00000":
            raise RuntimeError(f"code={data.get('code')} msg={data.get('msg')}")
        row = (data.get("data") or [{}])[0]
        last = safe_float(row.get("price"))
        idx = safe_float(row.get("indexPrice"))
        mark = safe_float(row.get("markPrice"))
        ts = int(row.get("ts") or 0)
        basis = ((mark / idx) - 1.0) * 10000.0 if mark is not None and idx not in (None, 0) else None
        out.update({
            "last_price": last,
            "index_price": idx,
            "mark_price": mark,
            "basis_bps": basis,
            "price_ts_utc": iso(datetime.fromtimestamp(ts / 1000, tz=timezone.utc)) if ts else None,
        })
    except Exception as e:
        errors.append(f"basis:{type(e).__name__}:{str(e)[:160]}")
    return out, errors


def fetch_liquidations(symbol: str, start_ms: int, end_ms: int) -> tuple[list[dict[str, Any]], str, list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    cursor: str | None = None
    completeness = "PARTIAL_UNKNOWN"
    seen: set[tuple[str, str, str, str]] = set()

    for _ in range(MAX_LIQ_PAGES):
        params: dict[str, Any] = {"category": "USDT-FUTURES", "symbol": symbol, "limit": "100"}
        if cursor:
            params["cursor"] = cursor
        try:
            data = get_json("https://api.bitget.com/api/v3/market/liquidations", params)
        except Exception as e:
            errors.append(f"liquidations:{type(e).__name__}:{str(e)[:160]}")
            break
        if str(data.get("code")) != "00000":
            errors.append(f"liquidations:code={data.get('code')} msg={data.get('msg')}")
            break
        body = data.get("data") or {}
        page = body.get("list") or []
        if not page:
            completeness = "FULL" if rows else "NO_DATA"
            break

        oldest = None
        for x in page:
            ts = int(x.get("ts") or 0)
            oldest = ts if oldest is None else min(oldest, ts)
            if ts < start_ms or ts > end_ms:
                continue
            side = str(x.get("side") or "").lower()
            price = safe_float(x.get("price"))
            amount = safe_float(x.get("amount"))
            key = (str(ts), side, str(price), str(amount))
            if key in seen:
                continue
            seen.add(key)
            rows.append({"ts": ts, "side": side, "price": price, "amount": amount})

        if oldest is not None and oldest <= start_ms:
            completeness = "FULL"
            break
        next_cursor = str(body.get("cursor") or "")
        if not next_cursor or next_cursor == cursor:
            completeness = "FULL" if len(page) < 100 else "PARTIAL_CURSOR_STOP"
            break
        cursor = next_cursor
        time.sleep(0.22)
    else:
        completeness = "PARTIAL_MAX_PAGES"

    rows.sort(key=lambda x: x["ts"])
    return rows, completeness, errors


def summarize_symbol(symbol: str, started: datetime, ended: datetime) -> dict[str, Any]:
    start_ms = int(started.timestamp() * 1000)
    end_ms = int(ended.timestamp() * 1000)
    trades, trade_completeness, trade_errors = fetch_taker_trades(symbol, start_ms, end_ms)
    basis, basis_errors = fetch_basis(symbol)
    liquidations, liq_completeness, liq_errors = fetch_liquidations(symbol, start_ms, end_ms)

    buy_notional = sum(x["notional_usdt"] for x in trades if x["side"] == "buy")
    sell_notional = sum(x["notional_usdt"] for x in trades if x["side"] == "sell")
    total_notional = buy_notional + sell_notional
    cvd = buy_notional - sell_notional

    long_liq_amount = sum((x.get("amount") or 0.0) for x in liquidations if x.get("side") == "buy")
    short_liq_amount = sum((x.get("amount") or 0.0) for x in liquidations if x.get("side") == "sell")

    errors = trade_errors + basis_errors + liq_errors
    status = "OK"
    if trade_completeness.startswith("PARTIAL") or liq_completeness.startswith("PARTIAL") or errors:
        status = "PARTIAL"
    if not trades and basis.get("basis_bps") is None and not liquidations:
        status = "N/A"

    return {
        "symbol": symbol,
        "venue": VENUE,
        "window_start_utc": iso(started),
        "window_end_utc": iso(ended),
        "status": status,
        "trade_completeness": trade_completeness,
        "trade_count": len(trades),
        "taker_buy_notional_usdt": buy_notional,
        "taker_sell_notional_usdt": sell_notional,
        "taker_buy_ratio": (buy_notional / total_notional) if total_notional > 0 else None,
        "cvd_notional_usdt": cvd,
        "last_price": basis.get("last_price"),
        "index_price": basis.get("index_price"),
        "mark_price": basis.get("mark_price"),
        "basis_bps": basis.get("basis_bps"),
        "price_ts_utc": basis.get("price_ts_utc"),
        "liquidation_completeness": liq_completeness,
        "liquidation_count": len(liquidations),
        "long_liquidation_amount_raw": long_liq_amount,
        "short_liquidation_amount_raw": short_liq_amount,
        "liquidation_amount_unit": "Bitget API raw amount; do not convert to USD unless unit is independently confirmed",
        "errors": errors,
    }


def read_history() -> list[dict[str, str]]:
    if not HISTORY_PATH.exists():
        return []
    with HISTORY_PATH.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_history(rows: list[dict[str, Any]]) -> None:
    fields = [
        "window_end_utc", "window_start_utc", "venue", "symbol", "status",
        "trade_completeness", "trade_count", "taker_buy_notional_usdt", "taker_sell_notional_usdt",
        "taker_buy_ratio", "cvd_notional_usdt", "last_price", "index_price", "mark_price", "basis_bps",
        "price_ts_utc", "liquidation_completeness", "liquidation_count",
        "long_liquidation_amount_raw", "short_liquidation_amount_raw", "errors",
    ]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            item = dict(r)
            if isinstance(item.get("errors"), list):
                item["errors"] = " | ".join(item["errors"])
            w.writerow({k: item.get(k, "") for k in fields})


def save_latest(summary: list[dict[str, Any]], ended: datetime) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "engine": "MASTER_DERIVATIVES_MICROSTRUCTURE_V1",
        "venue": VENUE,
        "generated_at_utc": iso(now_utc()),
        "window_minutes": TRADE_WINDOW_MINUTES,
        "max_trade_pages": MAX_TRADE_PAGES,
        "rules": {
            "cvd": "Public futures fills; buy notional minus sell notional. Side is public trade/taker direction.",
            "basis": "(markPrice/indexPrice - 1) * 10000, same Bitget futures venue.",
            "liquidations": "Bitget public liquidation history. Raw amount is preserved because REST docs do not define a quote/base unit.",
            "partial": "PARTIAL means pagination/API completeness was not fully verified; do not treat it as full-market total.",
        },
        "assets": summary,
    }
    SUMMARY_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = [
        "symbol", "venue", "window_start_utc", "window_end_utc", "status", "trade_completeness", "trade_count",
        "taker_buy_notional_usdt", "taker_sell_notional_usdt", "taker_buy_ratio", "cvd_notional_usdt",
        "last_price", "index_price", "mark_price", "basis_bps", "price_ts_utc",
        "liquidation_completeness", "liquidation_count", "long_liquidation_amount_raw", "short_liquidation_amount_raw",
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

    ended = now_utc()
    started = ended - timedelta(minutes=TRADE_WINDOW_MINUTES)
    summary = [summarize_symbol(symbol, started, ended) for symbol in SYMBOLS]
    save_latest(summary, ended)

    old = read_history()
    key: dict[tuple[str, str, str], dict[str, Any]] = {
        (r.get("window_end_utc", ""), r.get("venue", ""), r.get("symbol", "")): r for r in old
    }
    for r in summary:
        key[(r["window_end_utc"], r["venue"], r["symbol"])] = r
    cutoff = ended - timedelta(days=90)
    rows: list[dict[str, Any]] = []
    for r in key.values():
        try:
            dt = datetime.fromisoformat(str(r.get("window_end_utc", "")).replace("Z", "+00:00"))
            if dt >= cutoff:
                rows.append(r)
        except Exception:
            rows.append(r)
    rows.sort(key=lambda r: (str(r.get("window_end_utc", "")), str(r.get("symbol", ""))))
    write_history(rows)

    state = {
        "last_run_utc": iso(now_utc()),
        "venue": VENUE,
        "symbols": SYMBOLS,
        "max_trade_pages": MAX_TRADE_PAGES,
        "ok_count": sum(r["status"] == "OK" for r in summary),
        "partial_count": sum(r["status"] == "PARTIAL" for r in summary),
        "na_count": sum(r["status"] == "N/A" for r in summary),
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
