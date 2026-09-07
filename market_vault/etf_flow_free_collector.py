from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import requests

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "market_vault"
OUT_DIR = BASE / "output"
STATE_DIR = BASE / "state"
OUT_PATH = OUT_DIR / "latest_etf_flows.json"
STATE_PATH = STATE_DIR / "etf_flow_state.json"

SOURCES = {
    "BTC": "https://farside.co.uk/bitcoin-etf-flow-all-data/",
    "ETH": "https://farside.co.uk/eth/",
}
HEADERS = {"User-Agent": "MASTER-MARKET-ETF-FLOW/1.0"}


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            text = re.sub(r"\s+", " ", " ".join(self._cell)).strip()
            self._row.append(text)
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def parse_money_m(v: str) -> float | None:
    s = v.strip().replace(",", "").replace("$", "")
    if s in {"", "-", "—", "–", "N/A"}:
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    try:
        x = float(s)
    except Exception:
        return None
    if not math.isfinite(x):
        return None
    return -x if neg else x


def parse_date(v: str) -> datetime | None:
    s = re.sub(r"\s+", " ", v.strip())
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
        except Exception:
            pass
    return None


def get_html(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def choose_flow_table(html: str) -> list[list[str]]:
    p = TableParser(); p.feed(html)
    best: list[list[str]] | None = None
    for t in p.tables:
        if not t:
            continue
        header = [x.strip().lower() for x in t[0]]
        if header and header[0] == "date" and any(x == "total" for x in header):
            if best is None or len(t) > len(best):
                best = t
    if best is None:
        raise RuntimeError("Farside ETF flow table not found")
    return best


def collect_asset(asset: str, url: str) -> dict[str, Any]:
    table = choose_flow_table(get_html(url))
    header = table[0]
    total_idx = next(i for i, x in enumerate(header) if x.strip().lower() == "total")
    rows: list[dict[str, Any]] = []
    for cells in table[1:]:
        if len(cells) <= total_idx:
            continue
        dt = parse_date(cells[0])
        if dt is None:
            continue
        total = parse_money_m(cells[total_idx])
        # Farside holiday/non-session rows may print Total=0.0 while every fund cell is '-'.
        # Exclude those from trading-day windows; retain a true zero-flow trading day when any fund cell is numeric.
        fund_cells = cells[1:total_idx]
        has_numeric_fund = any(parse_money_m(x) is not None for x in fund_cells)
        if not has_numeric_fund:
            continue
        if total is None:
            # Sum confirmed fund cells only when the published Total cell is unavailable.
            vals = [parse_money_m(x) for x in fund_cells]
            nums = [x for x in vals if x is not None]
            if not nums:
                continue
            total = sum(nums)
        rows.append({"date": dt, "total_usd_m": total})

    rows.sort(key=lambda x: x["date"])
    if len(rows) < 20:
        raise RuntimeError(f"{asset}: fewer than 20 valid trading rows ({len(rows)})")

    def window(n: int) -> float:
        return round(sum(float(x["total_usd_m"]) for x in rows[-n:]), 4)

    latest = rows[-1]
    return {
        "asset": asset,
        "source": "Farside Investors US ETF flow table",
        "source_url": url,
        "unit": "USD millions",
        "latest_trading_date": latest["date"].strftime("%Y-%m-%d"),
        "flow_1d_usd_m": round(float(latest["total_usd_m"]), 4),
        "flow_3d_usd_m": window(3),
        "flow_5d_usd_m": window(5),
        "flow_20d_usd_m": window(20),
        "trading_rows_available": len(rows),
        "window_rule": "Actual trading rows only; all-dash holiday/non-session rows excluded. Published Total preferred; no calendar-day interpolation.",
        "last_20_trading_days": [
            {"date": x["date"].strftime("%Y-%m-%d"), "flow_usd_m": round(float(x["total_usd_m"]), 4)} for x in rows[-20:]
        ],
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True); STATE_DIR.mkdir(parents=True, exist_ok=True)
    assets: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    for asset, url in SOURCES.items():
        try:
            assets.append(collect_asset(asset, url))
        except Exception as e:
            failures[asset] = f"{type(e).__name__}: {str(e)[:300]}"

    payload = {
        "engine": "MASTER_MARKET_FREE_ETF_FLOW_V1",
        "schema_version": "1.0",
        "generated_at_utc": now_iso(),
        "score_weight": 0,
        "role": "Free public ETF-flow evidence for existing MASTER MARKET institution-flow axis.",
        "windows": ["1D", "3D", "5D", "20D"],
        "assets": assets,
        "failures": failures,
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    state = {
        "engine": payload["engine"],
        "schema_version": payload["schema_version"],
        "last_run_utc": payload["generated_at_utc"],
        "ok_assets": [x["asset"] for x in assets],
        "failures": failures,
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
