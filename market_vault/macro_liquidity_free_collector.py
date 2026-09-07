from __future__ import annotations

import csv
import json
import math
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "market_vault"
DATA_DIR = BASE / "data"
OUT_DIR = BASE / "output"
STATE_DIR = BASE / "state"
HISTORY_PATH = DATA_DIR / "macro_liquidity_free_history.csv"
SUMMARY_PATH = OUT_DIR / "latest_macro_liquidity.json"
STATE_PATH = STATE_DIR / "macro_liquidity_state.json"

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FISCAL_TGA = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/dts/operating_cash_balance"
TREASURY_PRESS_RELEASES = "https://home.treasury.gov/news/press-releases"
TREASURY_BUYBACK_PAGE = "https://www.treasurydirect.gov/auctions/announcements-data-results/buy-backs/"
HEADERS = {"User-Agent": "MASTER-MARKET-FREE-MACRO/1.0"}
FIELDS = ["snapshot_hour_utc", "retrieved_at_utc", "metric", "value", "unit", "source", "source_url", "source_observation_time", "source_frequency", "status", "note"]


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href = None
            self._text = []


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_hour() -> datetime:
    n = now_utc()
    return n.replace(minute=0, second=0, microsecond=0)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def parse_iso(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except Exception:
        return None


def safe_float(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def get(url: str, params: dict[str, Any] | None = None, timeout: int = 25) -> requests.Response:
    last: Exception | None = None
    for i in range(3):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            if i < 2:
                time.sleep(0.8 * (i + 1))
    raise RuntimeError(str(last))


def fred_series(series_id: str) -> list[tuple[datetime, float]]:
    text = get(FRED_CSV, {"id": series_id}, 25).text
    rows = list(csv.reader(text.splitlines()))
    if len(rows) < 2:
        raise RuntimeError(f"FRED {series_id}: no rows")
    out: list[tuple[datetime, float]] = []
    for r in rows[1:]:
        if len(r) < 2:
            continue
        try:
            dt = datetime.strptime(r[0].strip(), "%Y-%m-%d").replace(tzinfo=UTC)
        except Exception:
            continue
        val = safe_float(r[1])
        if val is not None:
            out.append((dt, val))
    if not out:
        raise RuntimeError(f"FRED {series_id}: no numeric observations")
    return out


def fed_metrics() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    walcl = fred_series("WALCL")  # USD millions, weekly
    reserves = fred_series("WRESBAL")  # USD millions, weekly
    rrp = fred_series("RRPONTSYD")  # USD billions, daily

    walcl_dt, walcl_val = walcl[-1]
    res_dt, res_val = reserves[-1]
    rrp_dt, rrp_bil = rrp[-1]
    walcl_4w = None
    target = walcl_dt - timedelta(days=28)
    prior = min(walcl, key=lambda x: abs((x[0] - target).total_seconds())) if len(walcl) >= 2 else None
    if prior and abs((prior[0] - target).days) <= 10:
        walcl_4w = walcl_val - prior[1]

    metrics = [
        {"metric": "FED_TOTAL_ASSETS", "value": walcl_val, "unit": "USD millions", "source": "Federal Reserve/FRED WALCL", "source_url": "https://fred.stlouisfed.org/series/WALCL", "source_observation_time": iso(walcl_dt), "source_frequency": "weekly", "status": "OK", "note": "Fed total assets; not itself QE/QT judgement."},
        {"metric": "FED_RESERVE_BALANCES", "value": res_val, "unit": "USD millions", "source": "Federal Reserve/FRED WRESBAL", "source_url": "https://fred.stlouisfed.org/series/WRESBAL", "source_observation_time": iso(res_dt), "source_frequency": "weekly", "status": "OK", "note": "Reserve balances with Federal Reserve Banks."},
        {"metric": "ON_RRP", "value": rrp_bil * 1000.0, "unit": "USD millions", "source": "New York Fed/FRED RRPONTSYD", "source_url": "https://fred.stlouisfed.org/series/RRPONTSYD", "source_observation_time": iso(rrp_dt), "source_frequency": "business_daily", "status": "OK", "note": "Overnight reverse repo take-up; FRED value converted from USD billions to millions."},
    ]
    if walcl_4w is not None:
        metrics.append({"metric": "FED_TOTAL_ASSETS_4W_CHANGE", "value": walcl_4w, "unit": "USD millions", "source": "Federal Reserve/FRED WALCL", "source_url": "https://fred.stlouisfed.org/series/WALCL", "source_observation_time": iso(walcl_dt), "source_frequency": "weekly", "status": "OK", "note": "Latest WALCL minus nearest observation about 4 weeks earlier."})
    return metrics, {"walcl": (walcl_dt, walcl_val), "reserves": (res_dt, res_val), "rrp": (rrp_dt, rrp_bil * 1000.0)}


def tga_metric() -> dict[str, Any]:
    params = {"sort": "-record_date", "page[size]": "100", "fields": "record_date,account_type,close_today_bal,open_today_bal", "format": "json"}
    doc = get(FISCAL_TGA, params, 25).json()
    rows = doc.get("data", []) if isinstance(doc, dict) else []
    dates = sorted({str(r.get("record_date")) for r in rows if r.get("record_date")}, reverse=True)
    for d in dates:
        same = [r for r in rows if str(r.get("record_date")) == d]
        preferred = [r for r in same if "treasury general account" in str(r.get("account_type") or "").lower()]
        for r in preferred:
            v = safe_float(r.get("close_today_bal"))
            if v is None:
                v = safe_float(r.get("open_today_bal"))
            if v is not None:
                return {"metric": "TGA_CLOSING_BALANCE", "value": v, "unit": "USD millions", "source": "US Treasury FiscalData DTS", "source_url": FISCAL_TGA, "source_observation_time": d + "T00:00:00Z", "source_frequency": "business_daily", "status": "OK", "note": "Treasury General Account closing balance."}
    raise RuntimeError("no numeric TGA balance")


def strip_html(html: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>|<style.*?>.*?</style>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    return re.sub(r"\s+", " ", text).strip()


def discover_latest_press_release(title_contains: str, max_pages: int = 5) -> str:
    needle = title_contains.lower()
    for page in range(max_pages):
        html = get(TREASURY_PRESS_RELEASES, {"page": str(page)}, 25).text
        p = LinkParser(); p.feed(html)
        matches = [(urljoin(TREASURY_PRESS_RELEASES, href), txt) for href, txt in p.links if needle in txt.lower()]
        if matches:
            return matches[0][0]
    raise RuntimeError(f"Treasury press release not found: {title_contains}")


def qra_metrics() -> list[dict[str, Any]]:
    url = discover_latest_press_release("Treasury Announces Marketable Borrowing Estimates", 6)
    html = get(url, timeout=25).text
    text = strip_html(html)
    patterns = re.findall(
        r"During the ([^\.]{1,100}?) quarter, Treasury expects to borrow \$([\d,.]+) billion[^\.]{0,260}?cash balance of \$([\d,.]+) billion",
        text,
        flags=re.I,
    )
    if not patterns:
        raise RuntimeError("QRA financing values not parsed")
    date_match = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+20\d{2}", text)
    obs = None
    if date_match:
        try:
            obs = datetime.strptime(date_match.group(0), "%B %d, %Y").replace(tzinfo=UTC)
        except Exception:
            obs = None
    out: list[dict[str, Any]] = []
    for i, (quarter, borrow, cash) in enumerate(patterns[:2], start=1):
        label = "CURRENT_Q" if i == 1 else "NEXT_Q"
        out.append({"metric": f"QRA_NET_MARKETABLE_BORROWING_{label}", "value": float(borrow.replace(",", "")), "unit": "USD billions", "source": "US Treasury Quarterly Refunding financing estimates", "source_url": url, "source_observation_time": iso(obs) if obs else None, "source_frequency": "quarterly", "status": "OK", "note": f"Quarter label: {quarter.strip()}"})
        out.append({"metric": f"QRA_END_CASH_BALANCE_{label}", "value": float(cash.replace(",", "")), "unit": "USD billions", "source": "US Treasury Quarterly Refunding financing estimates", "source_url": url, "source_observation_time": iso(obs) if obs else None, "source_frequency": "quarterly", "status": "OK", "note": f"Quarter label: {quarter.strip()}"})
    return out


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def find_xml_value(root: ET.Element, candidates: set[str]) -> str | None:
    normalized = {re.sub(r"[^a-z0-9]", "", c.lower()) for c in candidates}
    for e in root.iter():
        key = re.sub(r"[^a-z0-9]", "", local_name(e.tag).lower())
        if key in normalized and (e.text or "").strip():
            return (e.text or "").strip()
    return None


def buyback_metrics() -> list[dict[str, Any]]:
    html = get(TREASURY_BUYBACK_PAGE, timeout=30).text
    p = LinkParser(); p.feed(html)
    candidates: list[str] = []
    for href, txt in p.links:
        h = href.lower(); t = txt.lower()
        if ".xml" in h and ("result" in t or "result" in h or "buyback" in h or "buy-back" in h):
            candidates.append(urljoin(TREASURY_BUYBACK_PAGE, href))
    candidates = list(dict.fromkeys(candidates))
    parsed: list[tuple[datetime, str, float, float | None, str | None]] = []
    for url in candidates[:120]:
        try:
            content = get(url, timeout=20).content
            root = ET.fromstring(content)
            accepted_s = find_xml_value(root, {"TotalParAmountAccepted", "TotalParAccepted", "TotalAcceptedAmount"})
            if accepted_s is None:
                continue
            accepted = safe_float(accepted_s.replace(",", ""))
            if accepted is None:
                continue
            offered_s = find_xml_value(root, {"TotalParAmountOffered", "TotalParOffered", "TotalOfferedAmount"})
            offered = safe_float(offered_s.replace(",", "")) if offered_s else None
            op_s = find_xml_value(root, {"OperationDate", "BuybackOperationDate", "OperationStartDTM"})
            settle_s = find_xml_value(root, {"SettlementDate"})
            dt = None
            for s in (op_s, settle_s):
                if not s:
                    continue
                for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y"):
                    try:
                        dt = datetime.strptime(s[:19], fmt).replace(tzinfo=UTC)
                        break
                    except Exception:
                        pass
                if dt:
                    break
            if dt is None:
                m = re.search(r"(20\d{6})", url)
                if m:
                    dt = datetime.strptime(m.group(1), "%Y%m%d").replace(tzinfo=UTC)
            if dt is None:
                continue
            parsed.append((dt, url, accepted, offered, settle_s))
        except Exception:
            continue
    if not parsed:
        raise RuntimeError("TreasuryDirect buyback result XML not discoverable/parseable this run")
    parsed.sort(key=lambda x: x[0], reverse=True)
    dt, url, accepted, offered, settle = parsed[0]
    note = f"Latest discovered buyback operation; settlement={settle or 'N/A'}"
    out = [{"metric": "TREASURY_BUYBACK_ACTUAL_ACCEPTED", "value": accepted, "unit": "USD par amount (source units)", "source": "TreasuryDirect buyback results XML", "source_url": url, "source_observation_time": iso(dt), "source_frequency": "per_operation", "status": "OK", "note": note}]
    if offered is not None:
        out.append({"metric": "TREASURY_BUYBACK_ACTUAL_OFFERED", "value": offered, "unit": "USD par amount (source units)", "source": "TreasuryDirect buyback results XML", "source_url": url, "source_observation_time": iso(dt), "source_frequency": "per_operation", "status": "OK", "note": note})
    return out


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
            w.writerow({k: r.get(k, "") for k in FIELDS})


def nearest(rows: list[dict[str, Any]], metric: str, source: str, target: datetime, tolerance_hours: float, before: datetime) -> dict[str, Any] | None:
    best = None; best_gap = None
    for r in rows:
        if r.get("metric") != metric or r.get("source") != source or r.get("status") != "OK":
            continue
        dt = parse_iso(str(r.get("snapshot_hour_utc") or ""))
        if dt is None or dt >= before:
            continue
        gap = abs((dt - target).total_seconds()) / 3600.0
        if gap <= tolerance_hours and (best_gap is None or gap < best_gap):
            best, best_gap = r, gap
    return best


def delta(cur: float, prior: dict[str, Any] | None) -> dict[str, Any] | None:
    if not prior:
        return None
    pv = safe_float(prior.get("value"))
    if pv is None:
        return None
    return {"previous_value": pv, "delta": cur - pv, "delta_pct": ((cur - pv) / abs(pv) * 100.0) if pv else None, "previous_snapshot_hour_utc": prior.get("snapshot_hour_utc"), "previous_source_observation_time": prior.get("source_observation_time")}


def build_summary(history: list[dict[str, Any]], snapshot: datetime, failures: dict[str, str]) -> dict[str, Any]:
    current = [r for r in history if r.get("snapshot_hour_utc") == iso(snapshot) and r.get("status") == "OK" and safe_float(r.get("value")) is not None]
    metrics: list[dict[str, Any]] = []
    for r in current:
        cur = safe_float(r.get("value"))
        if cur is None:
            continue
        metric, source = str(r.get("metric")), str(r.get("source"))
        p1 = nearest(history, metric, source, snapshot - timedelta(days=1), 2.0, snapshot)
        p3 = nearest(history, metric, source, snapshot - timedelta(days=3), 3.0, snapshot)
        p7 = nearest(history, metric, source, snapshot - timedelta(days=7), 4.0, snapshot)
        metrics.append({**{k: r.get(k) for k in ("metric", "value", "unit", "source", "source_url", "source_observation_time", "source_frequency", "note")}, "vs_1d": delta(cur, p1), "vs_3d": delta(cur, p3), "vs_7d": delta(cur, p7)})
    metrics.sort(key=lambda x: str(x.get("metric")))
    return {"engine": "MASTER_MARKET_FREE_MACRO_LIQUIDITY_V1", "schema_version": "1.0", "generated_at_utc": iso(now_utc()), "snapshot_hour_utc": iso(snapshot), "score_weight": 0, "role": "free official-source evidence for existing MASTER MARKET macro/liquidity axes; not a standalone direction engine", "net_liquidity_formula": "FED_TOTAL_ASSETS - TGA_CLOSING_BALANCE - ON_RRP, all converted to USD millions", "metrics": metrics, "failures": failures}


def collect() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True); OUT_DIR.mkdir(parents=True, exist_ok=True); STATE_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = now_hour(); retrieved = now_utc(); rows: list[dict[str, Any]] = []; failures: dict[str, str] = {}

    fed_parts = None
    try:
        fed_rows, fed_parts = fed_metrics(); rows.extend(fed_rows)
    except Exception as e:
        failures["FED_FRED"] = str(e)[:300]
    tga = None
    try:
        tga = tga_metric(); rows.append(tga)
    except Exception as e:
        failures["TGA"] = str(e)[:300]

    if fed_parts and tga:
        try:
            walcl_dt, walcl = fed_parts["walcl"]
            _, rrp = fed_parts["rrp"]
            tga_v = safe_float(tga.get("value"))
            if tga_v is not None:
                net = walcl - tga_v - rrp
                comp_dates = [walcl_dt, fed_parts["rrp"][0], parse_iso(str(tga.get("source_observation_time") or ""))]
                comp_dates = [d for d in comp_dates if d is not None]
                obs = max(comp_dates) if comp_dates else retrieved
                rows.append({"metric": "US_NET_LIQUIDITY_PROXY", "value": net, "unit": "USD millions", "source": "Fed WALCL + Treasury TGA + NYFed ON RRP", "source_url": "https://fred.stlouisfed.org/series/WALCL", "source_observation_time": iso(obs), "source_frequency": "mixed weekly/daily", "status": "OK", "note": "Proxy only: Fed total assets minus TGA minus ON RRP; not an official Fed-published series."})
        except Exception as e:
            failures["US_NET_LIQUIDITY"] = str(e)[:300]

    try:
        rows.extend(qra_metrics())
    except Exception as e:
        failures["QRA"] = str(e)[:300]
    try:
        rows.extend(buyback_metrics())
    except Exception as e:
        failures["BUYBACK_ACTUAL"] = str(e)[:300]

    wrapped = [{"snapshot_hour_utc": iso(snapshot), "retrieved_at_utc": iso(retrieved), **r} for r in rows]
    old = read_history()
    keyed = {(r.get("snapshot_hour_utc"), r.get("metric"), r.get("source")): r for r in old}
    for r in wrapped:
        keyed[(r.get("snapshot_hour_utc"), r.get("metric"), r.get("source"))] = r
    history = list(keyed.values())
    cutoff = snapshot - timedelta(days=120)
    history = [r for r in history if (parse_iso(str(r.get("snapshot_hour_utc") or "")) or snapshot) >= cutoff]
    history.sort(key=lambda r: (str(r.get("snapshot_hour_utc", "")), str(r.get("metric", "")), str(r.get("source", ""))))
    write_history(history)

    summary = build_summary(history, snapshot, failures)
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    state = {"engine": "MASTER_MARKET_FREE_MACRO_LIQUIDITY_V1", "schema_version": "1.0", "last_run_utc": iso(retrieved), "snapshot_hour_utc": iso(snapshot), "collected_metrics": sorted({r.get("metric") for r in wrapped}), "failures": failures, "history_rows": len(history)}
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    collect()
