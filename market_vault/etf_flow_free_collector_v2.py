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
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}
DATE_RE = re.compile(r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+20\d{2})\b", re.I)
MONEY_RE = re.compile(r"(?<![A-Za-z0-9])(?:\([\d,.]+(?:\.\d+)?\)|-?[\d,.]+(?:\.\d+)?)(?![A-Za-z0-9])")


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t == "table":
            self._table = []
        elif t == "tr" and self._table is not None:
            self._row = []
        elif t in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", " ".join(self._cell)).strip())
            self._cell = None
        elif t == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif t == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None


class TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self.skip += 1
        elif not self.skip and tag.lower() in {"td", "th", "tr", "p", "div", "br", "li"}:
            self.parts.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self.skip:
            self.skip -= 1
        elif not self.skip and tag.lower() in {"td", "th", "tr", "p", "div", "br", "li"}:
            self.parts.append(" | ")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.parts))


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def parse_date(v: str) -> datetime | None:
    s = re.sub(r"\s+", " ", v.strip())
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
        except Exception:
            pass
    return None


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


def get_html(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    r.raise_for_status()
    text = r.text
    if len(text) < 1000:
        raise RuntimeError(f"unexpectedly short HTML ({len(text)} bytes)")
    return text


def table_rows(html: str) -> list[dict[str, Any]]:
    p = TableParser(); p.feed(html)
    best = None
    for t in p.tables:
        if not t:
            continue
        h = [x.strip().lower() for x in t[0]]
        if h and h[0] == "date" and "total" in h and (best is None or len(t) > len(best)):
            best = t
    if not best:
        return []
    header = best[0]
    total_idx = next(i for i, x in enumerate(header) if x.strip().lower() == "total")
    out: list[dict[str, Any]] = []
    for cells in best[1:]:
        if len(cells) <= total_idx:
            continue
        dt = parse_date(cells[0])
        if not dt:
            continue
        fund_cells = cells[1:total_idx]
        numeric_funds = [parse_money_m(x) for x in fund_cells]
        nums = [x for x in numeric_funds if x is not None]
        if not nums:
            continue
        total = parse_money_m(cells[total_idx])
        if total is None:
            total = sum(nums)
        out.append({"date": dt, "total_usd_m": total, "method": "html_table"})
    return out


def fallback_text_rows(html: str) -> list[dict[str, Any]]:
    p = TextParser(); p.feed(html); text = p.text()
    matches = list(DATE_RE.finditer(text))
    out: list[dict[str, Any]] = []
    for i, m in enumerate(matches):
        dt = parse_date(m.group(1))
        if not dt:
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else min(len(text), m.end() + 800)
        chunk = text[m.end():end]
        # Keep only a reasonable row-length area. Navigation/news prose often spans much longer.
        if len(chunk) > 1200:
            chunk = chunk[:1200]
        toks = MONEY_RE.findall(chunk)
        vals = [parse_money_m(x) for x in toks]
        nums = [x for x in vals if x is not None]
        # A holiday row usually has only published Total=0.0 and no fund-level numeric cells.
        if len(nums) < 2:
            continue
        total = nums[-1]
        out.append({"date": dt, "total_usd_m": total, "method": "text_fallback"})
    return out


def collect_asset(asset: str, url: str) -> dict[str, Any]:
    html = get_html(url)
    rows = table_rows(html)
    method = "html_table"
    if len(rows) < 20:
        rows = fallback_text_rows(html)
        method = "text_fallback"
    # de-duplicate dates and keep the last parse for that date
    dedup: dict[str, dict[str, Any]] = {}
    for r in rows:
        dedup[r["date"].strftime("%Y-%m-%d")] = r
    rows = sorted(dedup.values(), key=lambda x: x["date"])
    if len(rows) < 20:
        raise RuntimeError(f"{asset}: fewer than 20 valid trading rows ({len(rows)}), method={method}")

    def window(n: int) -> float:
        return round(sum(float(x["total_usd_m"]) for x in rows[-n:]), 4)

    latest = rows[-1]
    return {
        "asset": asset,
        "source": "Farside Investors US ETF flow table",
        "source_url": url,
        "unit": "USD millions",
        "parser_method": method,
        "latest_trading_date": latest["date"].strftime("%Y-%m-%d"),
        "flow_1d_usd_m": round(float(latest["total_usd_m"]), 4),
        "flow_3d_usd_m": window(3),
        "flow_5d_usd_m": window(5),
        "flow_20d_usd_m": window(20),
        "trading_rows_available": len(rows),
        "window_rule": "Actual trading rows only; all-dash holiday/non-session rows excluded. Published Total preferred; no calendar-day interpolation.",
        "last_20_trading_days": [{"date": x["date"].strftime("%Y-%m-%d"), "flow_usd_m": round(float(x["total_usd_m"]), 4)} for x in rows[-20:]],
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True); STATE_DIR.mkdir(parents=True, exist_ok=True)
    assets: list[dict[str, Any]] = []; failures: dict[str, str] = {}
    for asset, url in SOURCES.items():
        try:
            assets.append(collect_asset(asset, url))
        except Exception as e:
            failures[asset] = f"{type(e).__name__}: {str(e)[:300]}"
    payload = {"engine": "MASTER_MARKET_FREE_ETF_FLOW_V1", "schema_version": "1.1", "generated_at_utc": now_iso(), "score_weight": 0, "role": "Free public ETF-flow evidence for existing MASTER MARKET institution-flow axis.", "windows": ["1D", "3D", "5D", "20D"], "assets": assets, "failures": failures}
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    state = {"engine": payload["engine"], "schema_version": payload["schema_version"], "last_run_utc": payload["generated_at_utc"], "ok_assets": [x["asset"] for x in assets], "failures": failures}
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
