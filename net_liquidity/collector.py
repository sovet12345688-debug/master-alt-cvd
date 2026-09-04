from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timezone, timedelta
from io import StringIO
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "net_liquidity"
DATA_DIR = BASE / "data"
OUT_DIR = BASE / "output"
STATE_DIR = BASE / "state"
HISTORY_PATH = DATA_DIR / "history.csv"
SUMMARY_JSON = OUT_DIR / "latest_summary.json"
STATE_PATH = STATE_DIR / "collector_state.json"
MARKET_VAULT_PATH = ROOT / "market_vault" / "output" / "latest_summary.json"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "MASTER-US-NET-LIQUIDITY/1.1"})


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def fetch_fred_latest(series_id: str) -> dict[str, Any]:
    """Fetch only a recent FRED CSV slice with bounded retries.

    The prior implementation downloaded the full series and occasionally timed out in
    GitHub Actions. A recent date window is sufficient because this collector needs
    only the latest valid observation. No value is guessed if all attempts fail.
    """
    today = now_utc().date()
    start = today - timedelta(days=45)
    params = {"id": series_id, "cosd": start.isoformat(), "coed": today.isoformat()}
    errors: list[str] = []
    for attempt in range(1, 5):
        try:
            r = SESSION.get(
                "https://fred.stlouisfed.org/graph/fredgraph.csv",
                params=params,
                timeout=(10, 60),
            )
            r.raise_for_status()
            rows = list(csv.DictReader(StringIO(r.text)))
            for row in reversed(rows):
                raw = row.get(series_id)
                if raw not in (None, "", "."):
                    return {
                        "series_id": series_id,
                        "observation_date": row.get("DATE") or row.get("observation_date"),
                        "value": float(raw),
                        "source": "Federal Reserve Bank of St. Louis FRED",
                        "retrieval_mode": "recent_range_csv",
                    }
            raise RuntimeError(f"No valid recent observation for {series_id}")
        except Exception as e:
            errors.append(f"attempt{attempt}:{type(e).__name__}:{str(e)[:150]}")
            if attempt < 4:
                time.sleep(2 ** (attempt - 1))
    raise RuntimeError(" | ".join(errors))


def load_tga_from_market_vault() -> dict[str, Any]:
    raw = json.loads(MARKET_VAULT_PATH.read_text(encoding="utf-8"))
    for m in raw.get("metrics") or []:
        if m.get("metric") == "TGA_CLOSING_BALANCE":
            return {
                "value": float(m["value"]),
                "unit": m.get("unit"),
                "observation_time": m.get("source_observation_time"),
                "source": m.get("source"),
            }
    raise RuntimeError("TGA_CLOSING_BALANCE missing from market_vault latest_summary")


def read_history() -> list[dict[str, str]]:
    if not HISTORY_PATH.exists():
        return []
    with HISTORY_PATH.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_history(rows: list[dict[str, Any]]) -> None:
    fields = [
        "retrieved_at_utc", "net_liquidity_usd", "walcl_usd", "tga_usd", "rrp_usd",
        "walcl_observation_date", "tga_observation_time", "rrp_observation_date", "status",
    ]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def nearest_prior(rows: list[dict[str, str]], target: datetime) -> dict[str, str] | None:
    best = None
    best_dt = None
    for r in rows:
        try:
            dt = datetime.fromisoformat(str(r.get("retrieved_at_utc", "")).replace("Z", "+00:00"))
        except Exception:
            continue
        if dt <= target and (best_dt is None or dt > best_dt):
            best = r
            best_dt = dt
    return best


def pct_change(cur: float, past: float | None) -> float | None:
    if past in (None, 0):
        return None
    return (cur / past - 1.0) * 100.0


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    errors: list[str] = []
    retrieved = now_utc()

    walcl = None
    rrp = None
    tga = None
    try:
        walcl = fetch_fred_latest("WALCL")
    except Exception as e:
        errors.append(f"WALCL:{type(e).__name__}:{str(e)[:600]}")
    try:
        rrp = fetch_fred_latest("RRPONTSYD")
    except Exception as e:
        errors.append(f"RRPONTSYD:{type(e).__name__}:{str(e)[:600]}")
    try:
        tga = load_tga_from_market_vault()
    except Exception as e:
        errors.append(f"TGA:{type(e).__name__}:{str(e)[:300]}")

    status = "OK" if walcl and rrp and tga else "N/A"
    net = None
    components: dict[str, float] = {}
    if status == "OK":
        # WALCL is USD millions; RRPONTSYD is USD billions; TGA vault is USD millions.
        walcl_usd = float(walcl["value"]) * 1_000_000.0
        rrp_usd = float(rrp["value"]) * 1_000_000_000.0
        tga_usd = float(tga["value"]) * 1_000_000.0
        net = walcl_usd - tga_usd - rrp_usd
        components = {
            "walcl_usd": walcl_usd,
            "tga_usd": tga_usd,
            "rrp_usd": rrp_usd,
        }

    old = read_history()
    prior_1d = nearest_prior(old, retrieved - timedelta(days=1))
    prior_3d = nearest_prior(old, retrieved - timedelta(days=3))
    prior_7d = nearest_prior(old, retrieved - timedelta(days=7))

    def prior_value(r: dict[str, str] | None) -> float | None:
        if not r:
            return None
        try:
            return float(r.get("net_liquidity_usd") or "")
        except Exception:
            return None

    payload = {
        "engine": "MASTER_US_NET_LIQUIDITY_V1_1",
        "generated_at_utc": iso(retrieved),
        "status": status,
        "formula": "Fed Total Assets (WALCL) - Treasury General Account (TGA) - Overnight Reverse Repo (RRPONTSYD)",
        "formula_type": "market liquidity proxy; not an official Federal Reserve metric",
        "net_liquidity_usd": net,
        "components": {
            "WALCL": walcl,
            "TGA": tga,
            "RRPONTSYD": rrp,
            **components,
        },
        "change": {
            "1D_pct": pct_change(net, prior_value(prior_1d)) if net is not None else None,
            "3D_pct": pct_change(net, prior_value(prior_3d)) if net is not None else None,
            "7D_pct": pct_change(net, prior_value(prior_7d)) if net is not None else None,
            "rule": "Only stored prior collector observations are used; no interpolation/backfill.",
        },
        "errors": errors,
    }
    SUMMARY_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if status == "OK" and net is not None:
        row = {
            "retrieved_at_utc": iso(retrieved),
            "net_liquidity_usd": net,
            "walcl_usd": components["walcl_usd"],
            "tga_usd": components["tga_usd"],
            "rrp_usd": components["rrp_usd"],
            "walcl_observation_date": walcl.get("observation_date"),
            "tga_observation_time": tga.get("observation_time"),
            "rrp_observation_date": rrp.get("observation_date"),
            "status": status,
        }
        old.append(row)
        cutoff = retrieved - timedelta(days=120)
        kept: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for r in old:
            try:
                dt = datetime.fromisoformat(str(r.get("retrieved_at_utc", "")).replace("Z", "+00:00"))
            except Exception:
                continue
            if dt < cutoff:
                continue
            key = (str(r.get("retrieved_at_utc", "")), str(r.get("net_liquidity_usd", "")))
            if key in seen:
                continue
            seen.add(key)
            kept.append(r)
        kept.sort(key=lambda r: str(r.get("retrieved_at_utc", "")))
        write_history(kept)

    state = {
        "last_run_utc": iso(now_utc()),
        "status": status,
        "history_rows": len(read_history()),
        "errors": errors,
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
