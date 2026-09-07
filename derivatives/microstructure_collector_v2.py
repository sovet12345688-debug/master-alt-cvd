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
MAX_TRADE_PAGES = max(60, int(os.environ.get("MAX_TRADE_PAGES", "240")))
MAX_LIQ_PAGES = max(10, int(os.environ.get("MAX_LIQ_PAGES", "50")))
DEPTH_LIMIT = 1000

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "MASTER-MARKET-MICROSTRUCTURE/2.0"})


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


def get_json(url: str, params: dict[str, Any] | None = None, timeout: int = 25) -> Any:
    last: Exception | None = None
    for i in range(3):
        try:
            r = SESSION.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            if i < 2:
                time.sleep(0.8 * (i + 1))
    raise RuntimeError(str(last))


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


def fetch_ticker_basis_volume(symbol: str) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    out = {
        "last_price": None,
        "index_price": None,
        "mark_price": None,
        "basis_bps": None,
        "volume_24h_base": None,
        "volume_24h_quote": None,
        "volume_24h_usdt": None,
        "price_ts_utc": None,
    }
    try:
        data = get_json(
            "https://api.bitget.com/api/v2/mix/market/ticker",
            {"symbol": symbol, "productType": "USDT-FUTURES"},
        )
        if str(data.get("code")) != "00000":
            raise RuntimeError(f"code={data.get('code')} msg={data.get('msg')}")
        rows = data.get("data") or []
        row = rows[0] if isinstance(rows, list) and rows else {}
        last = safe_float(row.get("lastPr"))
        idx = safe_float(row.get("indexPrice"))
        mark = safe_float(row.get("markPrice"))
        ts = int(row.get("ts") or 0)
        basis = ((mark / idx) - 1.0) * 10000.0 if mark is not None and idx not in (None, 0) else None
        out.update({
            "last_price": last,
            "index_price": idx,
            "mark_price": mark,
            "basis_bps": basis,
            "volume_24h_base": safe_float(row.get("baseVolume")),
            "volume_24h_quote": safe_float(row.get("quoteVolume")),
            "volume_24h_usdt": safe_float(row.get("usdtVolume")) or safe_float(row.get("quoteVolume")),
            "price_ts_utc": iso(datetime.fromtimestamp(ts / 1000, tz=timezone.utc)) if ts else None,
        })
    except Exception as e:
        errors.append(f"ticker_basis_volume:{type(e).__name__}:{str(e)[:160]}")
    return out, errors


def fetch_long_short(symbol: str) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    out = {
        "long_position_ratio": None,
        "short_position_ratio": None,
        "long_short_position_ratio": None,
        "long_short_ts_utc": None,
    }
    endpoints = [
        ("https://api.bitget.com/api/v3/market/futures-position-long-short", {"symbol": symbol, "period": "1h"}),
        ("https://api.bitget.com/api/v2/mix/market/position-long-short", {"symbol": symbol, "period": "1h"}),
    ]
    for url, params in endpoints:
        try:
            data = get_json(url, params)
            if str(data.get("code")) != "00000":
                raise RuntimeError(f"code={data.get('code')} msg={data.get('msg')}")
            rows = data.get("data") or []
            if isinstance(rows, dict):
                rows = rows.get("list") or [rows]
            if not isinstance(rows, list) or not rows:
                raise RuntimeError("empty long-short data")
            row = sorted(rows, key=lambda x: int(x.get("ts") or 0), reverse=True)[0]
            ts = int(row.get("ts") or 0)
            out.update({
                "long_position_ratio": safe_float(row.get("longPositionRatio")),
                "short_position_ratio": safe_float(row.get("shortPositionRatio")),
                "long_short_position_ratio": safe_float(row.get("longShortPositionRatio")),
                "long_short_ts_utc": iso(datetime.fromtimestamp(ts / 1000, tz=timezone.utc)) if ts else None,
            })
            return out, errors
        except Exception as e:
            errors.append(f"long_short:{url.rsplit('/',1)[-1]}:{type(e).__name__}:{str(e)[:120]}")
    return out, errors


def fetch_depth(symbol: str, mark_or_last: float | None) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    out = {
        "depth_ts_utc": None,
        "best_bid": None,
        "best_ask": None,
        "depth_bid_notional_0_5pct": None,
        "depth_ask_notional_0_5pct": None,
        "depth_imbalance_0_5pct": None,
        "depth_bid_notional_1pct": None,
        "depth_ask_notional_1pct": None,
        "depth_imbalance_1pct": None,
    }
    try:
        data = get_json(
            "https://api.bitget.com/api/v3/market/orderbook",
            {"category": "USDT-FUTURES", "symbol": symbol, "limit": str(DEPTH_LIMIT)},
        )
        if str(data.get("code")) != "00000":
            raise RuntimeError(f"code={data.get('code')} msg={data.get('msg')}")
        body = data.get("data") or {}
        bids = body.get("b") or body.get("bids") or []
        asks = body.get("a") or body.get("asks") or []
        parsed_bids = [(safe_float(x[0]), safe_float(x[1])) for x in bids if isinstance(x, (list, tuple)) and len(x) >= 2]
        parsed_asks = [(safe_float(x[0]), safe_float(x[1])) for x in asks if isinstance(x, (list, tuple)) and len(x) >= 2]
        parsed_bids = [(p, q) for p, q in parsed_bids if p is not None and q is not None]
        parsed_asks = [(p, q) for p, q in parsed_asks if p is not None and q is not None]
        best_bid = parsed_bids[0][0] if parsed_bids else None
        best_ask = parsed_asks[0][0] if parsed_asks else None
        mid = mark_or_last
        if mid is None and best_bid is not None and best_ask is not None:
            mid = (best_bid + best_ask) / 2.0
        if mid in (None, 0):
            raise RuntimeError("no usable midpoint")

        def sums(pct: float) -> tuple[float, float, float | None]:
            lo, hi = mid * (1.0 - pct), mid * (1.0 + pct)
            bid_n = sum(p * q for p, q in parsed_bids if p >= lo)
            ask_n = sum(p * q for p, q in parsed_asks if p <= hi)
            denom = bid_n + ask_n
            imb = (bid_n - ask_n) / denom if denom > 0 else None
            return bid_n, ask_n, imb

        b05, a05, i05 = sums(0.005)
        b1, a1, i1 = sums(0.01)
        ts = int(body.get("ts") or 0)
        out.update({
            "depth_ts_utc": iso(datetime.fromtimestamp(ts / 1000, tz=timezone.utc)) if ts else None,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "depth_bid_notional_0_5pct": b05,
            "depth_ask_notional_0_5pct": a05,
            "depth_imbalance_0_5pct": i05,
            "depth_bid_notional_1pct": b1,
            "depth_ask_notional_1pct": a1,
            "depth_imbalance_1pct": i1,
        })
    except Exception as e:
        errors.append(f"depth:{type(e).__name__}:{str(e)[:160]}")
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
    ticker, ticker_errors = fetch_ticker_basis_volume(symbol)
    long_short, long_short_errors = fetch_long_short(symbol)
    depth, depth_errors = fetch_depth(symbol, ticker.get("mark_price") or ticker.get("last_price"))
    liquidations, liq_completeness, liq_errors = fetch_liquidations(symbol, start_ms, end_ms)

    buy_notional = sum(x["notional_usdt"] for x in trades if x["side"] == "buy")
    sell_notional = sum(x["notional_usdt"] for x in trades if x["side"] == "sell")
    total_notional = buy_notional + sell_notional
    cvd = buy_notional - sell_notional if trades else None
    buy_ratio = (buy_notional / total_notional) if total_notional > 0 else None
    sell_ratio = (sell_notional / total_notional) if total_notional > 0 else None

    long_liq_amount = sum((x.get("amount") or 0.0) for x in liquidations if x.get("side") == "buy")
    short_liq_amount = sum((x.get("amount") or 0.0) for x in liquidations if x.get("side") == "sell")

    errors = trade_errors + ticker_errors + long_short_errors + depth_errors + liq_errors
    core_values = {
        "cvd": cvd,
        "taker": buy_ratio,
        "long_short": long_short.get("long_short_position_ratio"),
        "liquidation": len(liquidations) if not liq_completeness.startswith("PARTIAL_UNKNOWN") else None,
        "basis": ticker.get("basis_bps"),
        "depth": depth.get("depth_imbalance_0_5pct"),
        "volume": ticker.get("volume_24h_usdt") if ticker.get("volume_24h_usdt") is not None else total_notional,
    }
    coverage = sum(v is not None for v in core_values.values())
    status = "OK" if coverage == 7 and trade_completeness == "FULL" and not liq_completeness.startswith("PARTIAL") else "PARTIAL"
    if coverage == 0:
        status = "N/A"

    return {
        "symbol": symbol,
        "venue": VENUE,
        "window_start_utc": iso(started),
        "window_end_utc": iso(ended),
        "status": status,
        "field_coverage": coverage,
        "field_total": 7,
        "trade_completeness": trade_completeness,
        "trade_count": len(trades),
        "trade_notional_1h_usdt": total_notional if trades else None,
        "taker_buy_notional_usdt": buy_notional if trades else None,
        "taker_sell_notional_usdt": sell_notional if trades else None,
        "taker_buy_ratio": buy_ratio,
        "taker_sell_ratio": sell_ratio,
        "cvd_notional_usdt": cvd,
        **ticker,
        **long_short,
        **depth,
        "liquidation_completeness": liq_completeness,
        "liquidation_count": len(liquidations),
        "long_liquidation_amount_raw": long_liq_amount if liquidations else 0.0,
        "short_liquidation_amount_raw": short_liq_amount if liquidations else 0.0,
        "liquidation_amount_unit": "Bitget API raw amount; do not convert to USD unless unit is independently confirmed",
        "errors": errors,
    }


FIELDS = [
    "window_end_utc", "window_start_utc", "venue", "symbol", "status", "field_coverage", "field_total",
    "trade_completeness", "trade_count", "trade_notional_1h_usdt", "taker_buy_notional_usdt",
    "taker_sell_notional_usdt", "taker_buy_ratio", "taker_sell_ratio", "cvd_notional_usdt",
    "last_price", "index_price", "mark_price", "basis_bps", "volume_24h_base", "volume_24h_quote",
    "volume_24h_usdt", "price_ts_utc", "long_position_ratio", "short_position_ratio",
    "long_short_position_ratio", "long_short_ts_utc", "depth_ts_utc", "best_bid", "best_ask",
    "depth_bid_notional_0_5pct", "depth_ask_notional_0_5pct", "depth_imbalance_0_5pct",
    "depth_bid_notional_1pct", "depth_ask_notional_1pct", "depth_imbalance_1pct",
    "liquidation_completeness", "liquidation_count", "long_liquidation_amount_raw",
    "short_liquidation_amount_raw", "errors",
]


def read_history() -> list[dict[str, str]]:
    if not HISTORY_PATH.exists():
        return []
    with HISTORY_PATH.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_history(rows: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            item = dict(r)
            if isinstance(item.get("errors"), list):
                item["errors"] = " | ".join(item["errors"])
            w.writerow({k: item.get(k, "") for k in FIELDS})


def save_latest(summary: list[dict[str, Any]]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "engine": "MASTER_DERIVATIVES_MICROSTRUCTURE_V2",
        "schema_version": "2.0",
        "venue": VENUE,
        "generated_at_utc": iso(now_utc()),
        "window_minutes": TRADE_WINDOW_MINUTES,
        "free_public_fields": ["CVD", "Taker Buy/Sell", "Long/Short", "Liquidation", "Basis", "Depth", "Volume"],
        "rules": {
            "cvd": "Public Bitget futures fills; taker buy notional minus taker sell notional.",
            "long_short": "Bitget public active long/short position ratio, 1H period.",
            "basis": "(markPrice/indexPrice - 1) * 10000 from same Bitget futures venue.",
            "depth": "Public Bitget futures order book; quote-notional within +/-0.5% and +/-1% of midpoint.",
            "volume": "1H public-fill quote notional plus Bitget 24H USDT/quote volume.",
            "liquidations": "Bitget public liquidation history; raw amount preserved because unit should not be guessed.",
            "partial": "PARTIAL means at least one field/API completeness check was not fully satisfied; individual confirmed fields remain usable with lower confidence.",
        },
        "assets": summary,
    }
    SUMMARY_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with SUMMARY_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in summary:
            item = dict(r)
            if isinstance(item.get("errors"), list):
                item["errors"] = " | ".join(item["errors"])
            w.writerow({k: item.get(k, "") for k in FIELDS})


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    ended = now_utc()
    started = ended - timedelta(minutes=TRADE_WINDOW_MINUTES)
    summary: list[dict[str, Any]] = []
    for i, symbol in enumerate(SYMBOLS):
        if i:
            time.sleep(1.05)  # long/short endpoint is rate-limited to 1 req/s/IP
        summary.append(summarize_symbol(symbol, started, ended))
    save_latest(summary)

    old = read_history()
    key: dict[tuple[str, str, str], dict[str, Any]] = {
        (r.get("window_end_utc", ""), r.get("venue", ""), r.get("symbol", "")): r for r in old
    }
    for r in summary:
        key[(str(r["window_end_utc"]), str(r["venue"]), str(r["symbol"]))] = r
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
        "engine": "MASTER_DERIVATIVES_MICROSTRUCTURE_V2",
        "schema_version": "2.0",
        "last_run_utc": iso(now_utc()),
        "venue": VENUE,
        "symbols": SYMBOLS,
        "free_public_fields": ["CVD", "Taker Buy/Sell", "Long/Short", "Liquidation", "Basis", "Depth", "Volume"],
        "ok_count": sum(r["status"] == "OK" for r in summary),
        "partial_count": sum(r["status"] == "PARTIAL" for r in summary),
        "na_count": sum(r["status"] == "N/A" for r in summary),
        "field_coverage": {r["symbol"]: f"{r['field_coverage']}/7" for r in summary},
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
