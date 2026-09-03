from __future__ import annotations

import argparse
import csv
import io
import json
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "market_vault"
DATA_DIR = BASE / "data"
OUT_DIR = BASE / "output"
STATE_DIR = BASE / "state"
CONFIG_PATH = BASE / "config.json"
HISTORY_PATH = DATA_DIR / "market_history.csv"
SUMMARY_PATH = OUT_DIR / "latest_summary.json"
STATE_PATH = STATE_DIR / "vault_state.json"
UTC = timezone.utc

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "MASTER-MARKET-DATA-VAULT/1.0"})
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
DEFILLAMA_STABLES = "https://stablecoins.llama.fi/stablecoins"
COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"

FIELDS = [
    "snapshot_hour_utc","retrieved_at_utc","metric","value","unit","source",
    "source_observation_time","source_frequency","timestamp_quality","status"
]


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


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


def now_hour() -> datetime:
    n = datetime.now(UTC)
    return n.replace(minute=0, second=0, microsecond=0)


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def get(url: str, params: dict[str, Any] | None = None, timeout: int = 30) -> requests.Response:
    last: Exception | None = None
    for i in range(3):
        try:
            r = SESSION.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            if i < 2:
                time.sleep(1.0 + i)
    raise RuntimeError(str(last) if last else "request failed")


def fred_latest(series_id: str, lookback_days: int = 75) -> tuple[float, str]:
    start = (datetime.now(UTC).date() - timedelta(days=lookback_days)).isoformat()
    r = get(FRED_CSV, {"id": series_id, "cosd": start}, 35)
    reader = csv.DictReader(io.StringIO(r.text))
    best_val = None
    best_date = None
    for row in reader:
        date_v = row.get("observation_date") or row.get("DATE") or next(iter(row.values()), None)
        raw = row.get(series_id)
        v = safe_float(raw)
        if date_v and v is not None:
            best_val, best_date = v, str(date_v)
    if best_val is None or best_date is None:
        raise RuntimeError(f"no numeric FRED observation for {series_id}")
    return best_val, best_date + "T00:00:00Z"


def stablecoin_metrics() -> list[dict[str, Any]]:
    data = get(DEFILLAMA_STABLES, {"includePrices": "true"}, 40).json()
    assets = data.get("peggedAssets", []) if isinstance(data, dict) else []
    wanted = {"USDT": None, "USDC": None}
    total = 0.0
    for a in assets:
        if not isinstance(a, dict):
            continue
        circ = a.get("circulating") or {}
        usd = safe_float(circ.get("peggedUSD")) if isinstance(circ, dict) else None
        if usd is None:
            continue
        total += usd
        sym = str(a.get("symbol") or "").upper()
        if sym in wanted:
            wanted[sym] = usd
    out = []
    now = iso(datetime.now(UTC))
    for sym in ("USDT", "USDC"):
        if wanted[sym] is not None:
            out.append({"metric": f"{sym}_SUPPLY", "value": wanted[sym], "unit": "USD", "source": "DefiLlama Stablecoins", "source_observation_time": now, "source_frequency": "current", "timestamp_quality": "retrieval_time_proxy", "status": "OK"})
    if total > 0:
        out.append({"metric": "STABLECOIN_TOTAL_SUPPLY", "value": total, "unit": "USD", "source": "DefiLlama Stablecoins", "source_observation_time": now, "source_frequency": "current", "timestamp_quality": "retrieval_time_proxy", "status": "OK"})
    return out


def coingecko_metrics() -> list[dict[str, Any]]:
    doc = get(COINGECKO_GLOBAL, timeout=30).json()
    d = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(d, dict):
        raise RuntimeError("CoinGecko global payload missing data")
    cap = d.get("total_market_cap") or {}
    vol = d.get("total_volume") or {}
    pct = d.get("market_cap_percentage") or {}
    updated = safe_float(d.get("updated_at"))
    obs = iso(datetime.fromtimestamp(updated, tz=UTC)) if updated else iso(datetime.now(UTC))
    vals = [
        ("CRYPTO_TOTAL_MCAP", safe_float(cap.get("usd")), "USD"),
        ("CRYPTO_24H_VOLUME", safe_float(vol.get("usd")), "USD"),
        ("BTC_DOMINANCE", safe_float(pct.get("btc")), "percent"),
        ("ETH_DOMINANCE", safe_float(pct.get("eth")), "percent"),
    ]
    return [{"metric":m,"value":v,"unit":u,"source":"CoinGecko Global","source_observation_time":obs,"source_frequency":"current","timestamp_quality":"source_updated_at" if updated else "retrieval_time_proxy","status":"OK"} for m,v,u in vals if v is not None]


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
        for row in rows:
            w.writerow({k: row.get(k, "") for k in FIELDS})


def nearest_history(rows: list[dict[str, str]], metric: str, source: str, target: datetime, tolerance_min: int, before: datetime) -> tuple[dict[str, str] | None, float | None]:
    best = None
    best_gap = None
    for r in rows:
        if r.get("metric") != metric or r.get("source") != source or r.get("status") != "OK":
            continue
        if safe_float(r.get("value")) is None:
            continue
        dt = parse_iso(r.get("snapshot_hour_utc"))
        if dt is None or dt >= before:
            continue
        gap = abs((dt-target).total_seconds())/60.0
        if gap <= tolerance_min and (best_gap is None or gap < best_gap):
            best, best_gap = r, gap
    return best, best_gap


def prior_snapshot(rows: list[dict[str, str]], metric: str, source: str, before: datetime) -> dict[str, str] | None:
    best = None
    best_dt = None
    for r in rows:
        if r.get("metric") != metric or r.get("source") != source or r.get("status") != "OK":
            continue
        if safe_float(r.get("value")) is None:
            continue
        dt = parse_iso(r.get("snapshot_hour_utc"))
        if dt is None or dt >= before:
            continue
        if best_dt is None or dt > best_dt:
            best, best_dt = r, dt
    return best


def delta_block(cur: float, prior: dict[str, str] | None, gap: float | None = None) -> dict[str, Any] | None:
    if not prior:
        return None
    pv = safe_float(prior.get("value"))
    if pv is None:
        return None
    d = cur-pv
    pct = d/abs(pv)*100.0 if pv != 0 else None
    return {"previous_value": pv, "delta": d, "delta_pct": pct, "previous_snapshot_hour_utc": prior.get("snapshot_hour_utc"), "source_observation_time": prior.get("source_observation_time"), "target_gap_minutes": gap}


def build_summary(history: list[dict[str, str]], snapshot: datetime, cfg: dict[str, Any], state_meta: dict[str, Any]) -> dict[str, Any]:
    current_rows = [r for r in history if r.get("snapshot_hour_utc") == iso(snapshot) and r.get("status") == "OK" and safe_float(r.get("value")) is not None]
    metrics = []
    t24 = int(cfg.get("compare_tolerance_minutes", {}).get("24h", 90))
    t7 = int(cfg.get("compare_tolerance_minutes", {}).get("7d", 180))
    for r in current_rows:
        cur = safe_float(r.get("value"))
        if cur is None: continue
        metric, source = r.get("metric",""), r.get("source","")
        prev = prior_snapshot(history, metric, source, snapshot)
        h24,g24 = nearest_history(history, metric, source, snapshot-timedelta(hours=24), t24, snapshot)
        d7,g7 = nearest_history(history, metric, source, snapshot-timedelta(days=7), t7, snapshot)
        obs = r.get("source_observation_time")
        prev_obs = prev.get("source_observation_time") if prev else None
        metrics.append({
            "metric": metric,
            "value": cur,
            "unit": r.get("unit"),
            "source": source,
            "source_observation_time": obs,
            "source_frequency": r.get("source_frequency"),
            "timestamp_quality": r.get("timestamp_quality"),
            "new_source_observation_since_prior_snapshot": bool(prev and obs and prev_obs and obs != prev_obs),
            "vs_prior_snapshot": delta_block(cur, prev),
            "vs_24h": delta_block(cur, h24, g24),
            "vs_7d": delta_block(cur, d7, g7),
        })
    metrics.sort(key=lambda x: x["metric"])
    return {
        "engine": "MASTER_MARKET_DATA_VAULT_V1",
        "schema_version": "1.0",
        "snapshot_hour_utc": iso(snapshot),
        "generated_at_utc": iso(datetime.now(UTC)),
        "role": "historical comparison cache only; not a score and not a current-market substitute",
        "comparison_rule": "same metric + same source only; never mix sources across deltas",
        "metrics": metrics,
        "collection": state_meta,
    }


def collect() -> None:
    cfg = load_config()
    DATA_DIR.mkdir(parents=True, exist_ok=True); OUT_DIR.mkdir(parents=True, exist_ok=True); STATE_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = now_hour(); retrieved = datetime.now(UTC)
    rows = []
    failures: dict[str,str] = {}
    mandatory_total = len(cfg.get("fred_series", {})); mandatory_ok = 0

    for metric, spec in cfg.get("fred_series", {}).items():
        sid = str(spec["id"])
        try:
            value, obs = fred_latest(sid)
            rows.append({"snapshot_hour_utc":iso(snapshot),"retrieved_at_utc":iso(retrieved),"metric":metric,"value":value,"unit":spec.get("unit"),"source":f"FRED:{sid}","source_observation_time":obs,"source_frequency":spec.get("frequency"),"timestamp_quality":"official_observation_date","status":"OK"})
            mandatory_ok += 1
        except Exception as e:
            failures[metric] = str(e)[:200]
            rows.append({"snapshot_hour_utc":iso(snapshot),"retrieved_at_utc":iso(retrieved),"metric":metric,"value":"","unit":spec.get("unit"),"source":f"FRED:{sid}","source_observation_time":"","source_frequency":spec.get("frequency"),"timestamp_quality":"official_observation_date","status":"ERROR"})

    optional_ok = 0; optional_total = 0
    if cfg.get("optional_sources", {}).get("defillama_stablecoins"):
        optional_total += 1
        try:
            ext = stablecoin_metrics(); optional_ok += 1 if ext else 0
            for x in ext: rows.append({"snapshot_hour_utc":iso(snapshot),"retrieved_at_utc":iso(retrieved),**x})
        except Exception as e:
            failures["DEFILLAMA_STABLECOINS"] = str(e)[:200]
    if cfg.get("optional_sources", {}).get("coingecko_global"):
        optional_total += 1
        try:
            ext = coingecko_metrics(); optional_ok += 1 if ext else 0
            for x in ext: rows.append({"snapshot_hour_utc":iso(snapshot),"retrieved_at_utc":iso(retrieved),**x})
        except Exception as e:
            failures["COINGECKO_GLOBAL"] = str(e)[:200]

    old = read_history()
    keyed = {(r.get("snapshot_hour_utc"),r.get("metric"),r.get("source")):r for r in old}
    for r in rows: keyed[(r.get("snapshot_hour_utc"),r.get("metric"),r.get("source"))]=r
    history = list(keyed.values()); history.sort(key=lambda r:(r.get("snapshot_hour_utc",""),r.get("metric",""),r.get("source","")))
    cutoff = snapshot - timedelta(days=int(cfg.get("history_retention_days",45)))
    history = [r for r in history if (parse_iso(r.get("snapshot_hour_utc")) or snapshot) >= cutoff]
    write_history(history)

    meta = {"mandatory_ok":mandatory_ok,"mandatory_total":mandatory_total,"mandatory_coverage_pct":mandatory_ok/mandatory_total*100.0 if mandatory_total else 0.0,"optional_sources_ok":optional_ok,"optional_sources_total":optional_total,"failures":failures,"history_rows":len(history)}
    summary = build_summary(history,snapshot,cfg,meta)
    SUMMARY_PATH.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    state = {"engine":"MASTER_MARKET_DATA_VAULT_V1","schema_version":"1.0","last_run_utc":iso(retrieved),"snapshot_hour_utc":iso(snapshot),**meta}
    STATE_PATH.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(state,ensure_ascii=False))


def self_test() -> None:
    now = datetime(2026,9,3,5,tzinfo=UTC)
    rows=[
        {"snapshot_hour_utc":iso(now-timedelta(hours=24)),"metric":"X","source":"S","status":"OK","value":"100","source_observation_time":"a"},
        {"snapshot_hour_utc":iso(now-timedelta(hours=1)),"metric":"X","source":"S","status":"OK","value":"105","source_observation_time":"b"},
    ]
    r,g=nearest_history(rows,"X","S",now-timedelta(hours=24),90,now)
    assert r and safe_float(r["value"])==100 and g==0
    p=prior_snapshot(rows,"X","S",now)
    assert p and safe_float(p["value"])==105
    d=delta_block(110,r)
    assert d and d["delta"]==10
    # Different source must never be used for comparison.
    assert nearest_history(rows,"X","OTHER",now-timedelta(hours=24),90,now)[0] is None
    print("MARKET_DATA_VAULT_SELF_TEST=PASS")


def validate() -> None:
    state=json.loads(STATE_PATH.read_text(encoding="utf-8")); summary=json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    if state.get("engine")!="MASTER_MARKET_DATA_VAULT_V1" or summary.get("engine")!="MASTER_MARKET_DATA_VAULT_V1": raise SystemExit("wrong engine")
    if state.get("mandatory_total",0)<10 or state.get("mandatory_coverage_pct",0)<80: raise SystemExit("mandatory coverage too low")
    if not summary.get("metrics"): raise SystemExit("no summary metrics")
    keys={(m.get("metric"),m.get("source")) for m in summary.get("metrics",[])}
    if len(keys)!=len(summary.get("metrics",[])): raise SystemExit("duplicate latest metrics")
    for m in summary.get("metrics",[]):
        for k in ("vs_prior_snapshot","vs_24h","vs_7d"):
            b=m.get(k)
            if b and b.get("previous_snapshot_hour_utc") is None: raise SystemExit(f"bad comparison {m.get('metric')} {k}")
    print("MARKET_DATA_VAULT_VALIDATION=PASS")


def main() -> None:
    ap=argparse.ArgumentParser(); ap.add_argument("--mode",choices=["collect","self-test","validate"],default="collect"); args=ap.parse_args()
    if args.mode=="self-test": self_test()
    elif args.mode=="validate": validate()
    else: collect()


if __name__=="__main__": main()
