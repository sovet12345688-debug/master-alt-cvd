from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import pathlib
import shutil
from collections import defaultdict
from typing import Any

import requests

ROOT = pathlib.Path(__file__).resolve().parent
CFG = ROOT / "config.json"
INBOX = ROOT / "inbox"
REJECTED = ROOT / "rejected"
DATA = ROOT / "data"
OUTPUT = ROOT / "output"
STATE = ROOT / "state"
SIGNALS = DATA / "signals.jsonl"
OUTCOMES = DATA / "outcomes.jsonl"
SUMMARY = OUTPUT / "latest_summary.json"
VAULT_STATE = STATE / "vault_state.json"
UTC = dt.timezone.utc
BINANCE_KLINES = "https://data-api.binance.vision/api/v3/klines"


def now_utc() -> dt.datetime:
    return dt.datetime.now(tz=UTC)


def iso_utc(x: dt.datetime) -> str:
    return x.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_ts(v: str) -> dt.datetime:
    x = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    if x.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return x.astimezone(UTC)


def config() -> dict[str, Any]:
    return json.loads(CFG.read_text())


def ensure_dirs() -> None:
    for p in (INBOX, REJECTED, DATA, OUTPUT, STATE):
        p.mkdir(parents=True, exist_ok=True)


def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        obj = json.loads(line)
        if not isinstance(obj, dict):
            raise ValueError(f"{path}:{n} row must be object")
        rows.append(obj)
    return rows


def write_jsonl(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def write_json(path: pathlib.Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def file_sha(path: pathlib.Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_signal(raw: dict[str, Any], now: dt.datetime | None = None) -> dict[str, Any]:
    c = config()
    required = ["signal_id", "observed_at_utc", "source_master", "source_version", "symbol", "direction", "observed_price", "signal_label"]
    missing = [k for k in required if k not in raw]
    if missing:
        raise ValueError(f"missing required fields: {missing}")
    out = dict(raw)
    for k in ("signal_id", "source_master", "source_version", "symbol", "direction", "signal_label"):
        if not isinstance(out[k], str) or not out[k].strip():
            raise ValueError(f"{k} must be non-empty string")
        out[k] = out[k].strip()
    out["symbol"] = out["symbol"].upper()
    out["direction"] = out["direction"].upper()
    if out["direction"] not in {"LONG", "SHORT", "NEUTRAL"}:
        raise ValueError("direction must be LONG/SHORT/NEUTRAL")
    p = out["observed_price"]
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(float(p)) or float(p) <= 0:
        raise ValueError("observed_price must be positive finite number")
    out["observed_price"] = float(p)
    t = parse_ts(out["observed_at_utc"])
    out["observed_at_utc"] = iso_utc(t)
    now = now or now_utc()
    skew = dt.timedelta(minutes=int(c["max_clock_skew_minutes"]))
    if not c["allow_future_signal_timestamp"] and t > now + skew:
        raise ValueError("future signal timestamp not allowed")
    if not c["allow_signal_backfill"] and t < now - skew:
        raise ValueError("historical signal backfill not allowed")
    scores = out.get("scores") or {}
    if not isinstance(scores, dict):
        raise ValueError("scores must be object")
    clean_scores = {}
    for k, value in scores.items():
        if value is None:
            clean_scores[str(k)] = None
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"score {k} must be numeric or null")
        else:
            clean_scores[str(k)] = float(value)
    out["scores"] = clean_scores
    evidence = out.get("evidence") or {}
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be object")
    out["evidence"] = evidence
    tags = out.get("tags") or []
    if not isinstance(tags, list) or any(not isinstance(x, str) for x in tags):
        raise ValueError("tags must be string array")
    out["tags"] = sorted(set(x.strip() for x in tags if x.strip()))
    out["ingested_at_utc"] = iso_utc(now)
    basis = {k: v for k, v in out.items() if k not in {"ingested_at_utc", "payload_sha256"}}
    out["payload_sha256"] = hashlib.sha256(json.dumps(basis, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return out


def quarantine(path: pathlib.Path, error: Exception) -> None:
    ensure_dirs()
    stamp = now_utc().strftime("%Y%m%dT%H%M%SZ")
    target = REJECTED / f"{path.stem}__{stamp}{path.suffix}"
    shutil.move(str(path), str(target))
    target.with_suffix(target.suffix + ".error.txt").write_text(str(error) + "\n")


def ingest() -> dict[str, int]:
    ensure_dirs()
    rows = read_jsonl(SIGNALS)
    ids = {r["signal_id"] for r in rows}
    accepted = rejected = 0
    for path in sorted(INBOX.glob("*.json")):
        try:
            payload = json.loads(path.read_text())
            items = payload if isinstance(payload, list) else [payload]
            new_rows = []
            pending_ids = set()
            for item in items:
                sig = validate_signal(item)
                if sig["signal_id"] in ids or sig["signal_id"] in pending_ids:
                    raise ValueError(f"duplicate signal_id {sig['signal_id']}")
                pending_ids.add(sig["signal_id"])
                new_rows.append(sig)
            ids.update(pending_ids)
            rows.extend(new_rows)
            accepted += len(new_rows)
            path.unlink()
        except Exception as e:
            rejected += 1
            quarantine(path, e)
            print(f"REJECT {path.name}: {e}")
    if accepted:
        write_jsonl(SIGNALS, rows)
    return {"accepted": accepted, "rejected_files": rejected}


def binance_spot_symbol(symbol: str) -> str:
    """Map an internal asset symbol to the Binance Spot evaluation pair without changing stored signal identity."""
    s = symbol.strip().upper()
    quote_assets = ("USDT", "USDC", "FDUSD", "BUSD", "BTC", "ETH", "BNB")
    if any(s.endswith(q) and len(s) > len(q) for q in quote_assets):
        return s
    return f"{s}USDT"


def fetch_klines(symbol: str, start: dt.datetime, end: dt.datetime, session_factory=requests.Session) -> list[list[Any]]:
    """Fetch fully post-signal 5m candles with pagination; exclude the first partial candle."""
    interval_ms = 5 * 60 * 1000
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    cursor = ((start_ms + interval_ms - 1) // interval_ms) * interval_ms
    rows: list[list[Any]] = []
    session = session_factory()
    while cursor < end_ms:
        r = session.get(BINANCE_KLINES, params={"symbol": symbol, "interval": "5m", "startTime": cursor, "endTime": end_ms - 1, "limit": 1000}, timeout=20)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        nxt = int(batch[-1][0]) + interval_ms
        if nxt <= cursor:
            raise RuntimeError("non-advancing Binance kline pagination")
        cursor = nxt
        if len(batch) < 1000:
            break
    return rows


def outcome_metrics(direction: str, p0: float, candles: list[list[Any]]) -> dict[str, float | None]:
    if not candles:
        return {"future_close": None, "raw_return_pct": None, "direction_return_pct": None, "mfe_pct": None, "mae_pct": None}
    close = float(candles[-1][4])
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    raw = (close / p0 - 1.0) * 100.0
    if direction == "LONG":
        dreturn = raw
        mfe = (max(highs) / p0 - 1.0) * 100.0
        mae = (min(lows) / p0 - 1.0) * 100.0
    elif direction == "SHORT":
        dreturn = -raw
        mfe = (1.0 - min(lows) / p0) * 100.0
        mae = -(max(highs) / p0 - 1.0) * 100.0
    else:
        dreturn = mfe = mae = None
    rnd = lambda x: None if x is None else round(float(x), 6)
    return {"future_close": round(close, 12), "raw_return_pct": rnd(raw), "direction_return_pct": rnd(dreturn), "mfe_pct": rnd(mfe), "mae_pct": rnd(mae)}


def evaluate(now: dt.datetime | None = None, fetcher=fetch_klines) -> dict[str, int]:
    ensure_dirs()
    c = config()
    now = now or now_utc()
    signals = read_jsonl(SIGNALS)
    prior = {r["signal_id"]: r for r in read_jsonl(OUTCOMES)}
    new_horizons = errors = 0
    for s in signals:
        t0 = parse_ts(s["observed_at_utc"])
        rec = prior.get(s["signal_id"], {
            "signal_id": s["signal_id"], "symbol": s["symbol"], "source_master": s["source_master"],
            "source_version": s["source_version"], "direction": s["direction"], "signal_label": s["signal_label"],
            "observed_at_utc": s["observed_at_utc"], "observed_price": s["observed_price"],
            "scores": s.get("scores", {}), "evidence": s.get("evidence", {}), "tags": s.get("tags", []), "horizons": {}
        })
        mature = []
        for h in c["horizons_hours"]:
            h = int(h)
            end = t0 + dt.timedelta(hours=h)
            if now >= end and rec["horizons"].get(f"{h}H", {}).get("status") != "MATURED":
                mature.append((h, end))
        if mature:
            try:
                candles = fetcher(binance_spot_symbol(s["symbol"]), t0, max(end for _, end in mature))
                if not candles:
                    raise RuntimeError("no market-data candles returned")
                rec.pop("data_unavailable", None)
            except Exception as e:
                rec["data_unavailable"] = {"source": c["price_evaluation_source"], "error_type": type(e).__name__, "message": str(e)[:240]}
                errors += 1
                prior[s["signal_id"]] = rec
                continue
            for h, end in mature:
                end_ms = int(end.timestamp() * 1000)
                sub = [x for x in candles if int(x[0]) < end_ms and int(x[6]) < end_ms]
                rec["horizons"][f"{h}H"] = {
                    "status": "MATURED", "horizon_hours": h, "matured_at_utc": iso_utc(end),
                    "evaluation_interval": c.get("evaluation_interval", "5m"),
                    **outcome_metrics(s["direction"], float(s["observed_price"]), sub)
                }
                new_horizons += 1
        prior[s["signal_id"]] = rec
    write_jsonl(OUTCOMES, [prior[k] for k in sorted(prior)])
    return {"signals": len(signals), "newly_evaluated_horizons": new_horizons, "data_unavailable_signals": errors}


def score_band(v: float, c: dict[str, Any]) -> str | None:
    for b in c["score_bands"]:
        if float(b["min"]) <= v <= float(b["max"]):
            return b["label"]
    return None


def aggregate(rows: list[dict[str, Any]], horizon: str) -> dict[str, Any]:
    vals, mfes, maes = [], [], []
    for r in rows:
        h = r.get("horizons", {}).get(horizon)
        if not h or h.get("status") != "MATURED" or h.get("direction_return_pct") is None:
            continue
        vals.append(float(h["direction_return_pct"]))
        if h.get("mfe_pct") is not None:
            mfes.append(float(h["mfe_pct"]))
        if h.get("mae_pct") is not None:
            maes.append(float(h["mae_pct"]))
    if not vals:
        return {"n": 0, "positive_rate_pct": None, "avg_direction_return_pct": None, "avg_mfe_pct": None, "avg_mae_pct": None}
    return {
        "n": len(vals), "positive_rate_pct": round(sum(v > 0 for v in vals) / len(vals) * 100, 2),
        "avg_direction_return_pct": round(sum(vals) / len(vals), 4),
        "avg_mfe_pct": round(sum(mfes) / len(mfes), 4) if mfes else None,
        "avg_mae_pct": round(sum(maes) / len(maes), 4) if maes else None,
    }


def build_summary(now: dt.datetime | None = None) -> dict[str, Any]:
    ensure_dirs()
    c = config()
    now = now or now_utc()
    signals, outcomes = read_jsonl(SIGNALS), read_jsonl(OUTCOMES)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in outcomes:
        groups[f"master::{r['source_master']}"] .append(r)
        groups[f"symbol::{r['symbol']}"] .append(r)
        groups[f"direction::{r['direction']}"] .append(r)
        groups[f"label::{r['signal_label']}"] .append(r)
        for tag in r.get("tags", []):
            groups[f"tag::{tag}"] .append(r)
        for name, value in (r.get("scores") or {}).items():
            if value is not None:
                band = score_band(float(value), c)
                if band:
                    groups[f"score::{name}::{band}"] .append(r)
    horizons = [f"{int(h)}H" for h in c["horizons_hours"]]
    summary = {
        "engine": c["engine"], "schema_version": c["schema_version"], "generated_at_utc": iso_utc(now),
        "signal_count": len(signals), "outcome_record_count": len(outcomes),
        "data_unavailable_count": sum(1 for r in outcomes if r.get("data_unavailable")),
        "matured_counts": {h: sum(1 for r in outcomes if r.get("horizons", {}).get(h, {}).get("status") == "MATURED") for h in horizons},
        "grouped_calibration": {name: {h: aggregate(rows, h) for h in horizons} for name, rows in sorted(groups.items())},
        "calibration_policy": {"auto_master_tuning": False, "recommendations_enabled": False, "min_group_n_for_rate": c["min_group_n_for_rate"], "note": "V0.1 reports observed outcomes only."},
    }
    write_json(SUMMARY, summary)
    return summary


def write_state(ingest_stats: dict[str, int] | None = None, eval_stats: dict[str, int] | None = None) -> dict[str, Any]:
    c = config()
    signals, outcomes = read_jsonl(SIGNALS), read_jsonl(OUTCOMES)
    ids = [r.get("signal_id") for r in signals]
    state = {
        "engine": c["engine"], "schema_version": c["schema_version"], "last_material_run_utc": iso_utc(now_utc()),
        "signal_count": len(signals), "outcome_record_count": len(outcomes), "duplicate_signal_ids": len(ids) - len(set(ids)),
        "ingest": ingest_stats or {}, "evaluation": eval_stats or {},
        "guards": {"append_only_signal_policy": "PASS", "historical_backfill": "BLOCKED", "future_signal_timestamp": "BLOCKED", "na_zero_fill": "BLOCKED", "auto_master_tuning": "BLOCKED"},
    }
    write_json(VAULT_STATE, state)
    return state


def run() -> None:
    before = {"signals": file_sha(SIGNALS), "outcomes": file_sha(OUTCOMES)}
    s = ingest()
    e = evaluate()
    after = {"signals": file_sha(SIGNALS), "outcomes": file_sha(OUTCOMES)}
    material = before != after or s["rejected_files"] > 0
    if material:
        build_summary()
        write_state(s, e)
    print(json.dumps({"ingest": s, "evaluate": e, "material_change": material}, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["ingest", "evaluate", "summary", "run"])
    a = ap.parse_args()
    if a.command == "ingest":
        print(json.dumps(ingest(), indent=2))
    elif a.command == "evaluate":
        print(json.dumps(evaluate(), indent=2))
    elif a.command == "summary":
        print(json.dumps(build_summary(), indent=2))
    else:
        run()


if __name__ == "__main__":
    main()
