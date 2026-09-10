from __future__ import annotations

import csv
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "net_liquidity"
DATA_DIR = BASE / "data"
OUT_DIR = BASE / "output"
STATE_DIR = BASE / "state"
HISTORY_PATH = DATA_DIR / "history.csv"
SUMMARY_JSON = OUT_DIR / "latest_summary.json"
STATE_PATH = STATE_DIR / "collector_state.json"
MACRO_PATH = ROOT / "market_vault" / "output" / "latest_macro_liquidity.json"

MAX_UPSTREAM_AGE_MINUTES = 180


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_macro_proxy(now: datetime) -> dict[str, Any]:
    if not MACRO_PATH.exists():
        raise RuntimeError("market_vault/output/latest_macro_liquidity.json missing")
    raw = json.loads(MACRO_PATH.read_text(encoding="utf-8"))
    if raw.get("engine") != "MASTER_MARKET_FREE_MACRO_LIQUIDITY_V2":
        raise RuntimeError(f"unexpected macro engine: {raw.get('engine')}")

    generated = parse_dt(raw.get("generated_at_utc"))
    if generated is None:
        raise RuntimeError("macro generated_at_utc missing/invalid")
    age_minutes = max(0.0, (now - generated).total_seconds() / 60.0)
    if age_minutes > MAX_UPSTREAM_AGE_MINUTES:
        raise RuntimeError(f"macro upstream stale: {age_minutes:.1f}m > {MAX_UPSTREAM_AGE_MINUTES}m")

    metrics = {
        str(m.get("metric")): m
        for m in (raw.get("metrics") or [])
        if isinstance(m, dict) and m.get("metric")
    }
    required = ["US_NET_LIQUIDITY_PROXY", "FED_TOTAL_ASSETS", "FED_TGA_H41", "FED_RRP_TOTAL"]
    missing = [name for name in required if name not in metrics or metrics[name].get("value") is None]
    if missing:
        raise RuntimeError(f"macro upstream missing required metrics: {missing}")

    proxy = metrics["US_NET_LIQUIDITY_PROXY"]
    assets = metrics["FED_TOTAL_ASSETS"]
    tga = metrics["FED_TGA_H41"]
    rrp = metrics["FED_RRP_TOTAL"]

    # Canonical macro-vault values are USD millions; preserve that exact same-source formula.
    net_usd = float(proxy["value"]) * 1_000_000.0
    walcl_usd = float(assets["value"]) * 1_000_000.0
    tga_usd = float(tga["value"]) * 1_000_000.0
    rrp_usd = float(rrp["value"]) * 1_000_000.0

    return {
        "generated_at_utc": iso(generated),
        "age_minutes": age_minutes,
        "net_liquidity_usd": net_usd,
        "walcl_usd": walcl_usd,
        "tga_usd": tga_usd,
        "rrp_usd": rrp_usd,
        "walcl_observation_date": assets.get("source_observation_time"),
        "tga_observation_time": tga.get("source_observation_time"),
        "rrp_observation_date": rrp.get("source_observation_time"),
        "source": "market_vault/output/latest_macro_liquidity.json",
        "source_engine": raw.get("engine"),
        "source_formula": raw.get("net_liquidity_formula"),
    }


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


def prior_value(row: dict[str, str] | None) -> float | None:
    if not row:
        return None
    try:
        return float(row.get("net_liquidity_usd") or "")
    except Exception:
        return None


def pct_change(cur: float | None, past: float | None) -> float | None:
    if cur is None or past in (None, 0):
        return None
    return (cur / past - 1.0) * 100.0


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    retrieved = now_utc()
    errors: list[str] = []
    upstream = None
    try:
        upstream = load_macro_proxy(retrieved)
    except Exception as e:
        errors.append(f"MACRO_UPSTREAM:{type(e).__name__}:{str(e)[:600]}")

    status = "OK" if upstream else "N/A"
    net = float(upstream["net_liquidity_usd"]) if upstream else None

    old = read_history()
    prior_1d = nearest_prior(old, retrieved - timedelta(days=1))
    prior_3d = nearest_prior(old, retrieved - timedelta(days=3))
    prior_7d = nearest_prior(old, retrieved - timedelta(days=7))

    payload = {
        "engine": "MASTER_US_NET_LIQUIDITY_V1_2",
        "generated_at_utc": iso(retrieved),
        "status": status,
        "formula": "Fed H.4.1 Total Assets - H.4.1 Treasury General Account - H.4.1 Reverse Repurchase Agreements",
        "formula_type": "market liquidity proxy; not an official Federal Reserve metric",
        "source_mode": "canonical_macro_vault_adapter",
        "net_liquidity_usd": net,
        "upstream": upstream,
        "change": {
            "1D_pct": pct_change(net, prior_value(prior_1d)),
            "3D_pct": pct_change(net, prior_value(prior_3d)),
            "7D_pct": pct_change(net, prior_value(prior_7d)),
            "rule": "Only stored same-source collector observations are used; no interpolation/backfill/cross-source fill.",
        },
        "errors": errors,
    }
    SUMMARY_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if upstream and net is not None:
        old.append({
            "retrieved_at_utc": iso(retrieved),
            "net_liquidity_usd": net,
            "walcl_usd": upstream["walcl_usd"],
            "tga_usd": upstream["tga_usd"],
            "rrp_usd": upstream["rrp_usd"],
            "walcl_observation_date": upstream.get("walcl_observation_date"),
            "tga_observation_time": upstream.get("tga_observation_time"),
            "rrp_observation_date": upstream.get("rrp_observation_date"),
            "status": "OK",
        })
        cutoff = retrieved - timedelta(days=120)
        kept: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in old:
            try:
                dt = datetime.fromisoformat(str(row.get("retrieved_at_utc", "")).replace("Z", "+00:00"))
            except Exception:
                continue
            if dt < cutoff:
                continue
            key = (str(row.get("retrieved_at_utc", "")), str(row.get("net_liquidity_usd", "")))
            if key in seen:
                continue
            seen.add(key)
            kept.append(row)
        kept.sort(key=lambda row: str(row.get("retrieved_at_utc", "")))
        write_history(kept)

    state = {
        "last_run_utc": iso(now_utc()),
        "status": status,
        "source_mode": "canonical_macro_vault_adapter",
        "upstream_generated_at_utc": upstream.get("generated_at_utc") if upstream else None,
        "upstream_age_minutes": round(float(upstream["age_minutes"]), 1) if upstream else None,
        "history_rows": len(read_history()),
        "errors": errors,
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
