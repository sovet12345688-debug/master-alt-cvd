from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from btc_trend_v30.r20 import r20_historical_diagnostic_replay as base
from btc_trend_v30.r20.r20_engine import build_feature_bundle
from btc_trend_v30.r25 import r25_historical_diagnostic_replay_runner as r25run
from btc_trend_v30.r26.r26_engine import R26Engine

HERE = Path(__file__).resolve().parent
R26 = HERE.parent
ROOT = R26.parent

POLICY_OOS_START = pd.Timestamp("2026-09-05T00:00:00Z")
FINAL_FREEZE_TIME = pd.Timestamp("2026-09-08T02:40:15Z")
# First fully completed 4H decision bar after final freeze.
STRICT_FORWARD_START = pd.Timestamp("2026-09-08T04:00:00Z")
WARMUP_START = pd.Timestamp("2025-01-01T00:00:00Z")

EXPECTED = {
    "r26_frozen_sha256": "bbdbbe173e8f16bb6d57d6ed5bdff52d616c42fead5b59b39c5b3d7188a64a61",
    "r26_frozen_blob": "09d6d6689f46c16f3a40b16c58495a999e4f9e75",
    "r26_engine_blob": "c511b99205314d351bacb441cc88d70d9a4947ee",
    "r26_contract_blob": "ae6e8bd3cbafdc8b8a2c9672db5ee16e324850b3",
    "r25_engine_blob": "b5efe7378de839b338057826e0a7cb29926ec3a6",
}

ENDPOINTS = [
    "https://data-api.binance.vision/api/v3/klines",
    "https://api.binance.com/api/v3/klines",
]
INTERVAL_MS = {"1h": 3600_000, "4h": 4 * 3600_000, "1d": 24 * 3600_000}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_blob(path: Path) -> str:
    return subprocess.check_output(["git", "hash-object", str(path)], text=True).strip()


def verify_freeze() -> dict:
    checks = {
        "r26_frozen_sha256": sha256(R26 / "r26_frozen_config.json") == EXPECTED["r26_frozen_sha256"],
        "r26_frozen_blob": git_blob(R26 / "r26_frozen_config.json") == EXPECTED["r26_frozen_blob"],
        "r26_engine_blob": git_blob(R26 / "r26_engine.py") == EXPECTED["r26_engine_blob"],
        "r26_contract_blob": git_blob(R26 / "test_r26_contract.py") == EXPECTED["r26_contract_blob"],
        "r25_engine_blob": git_blob(ROOT / "r25/r25_engine.py") == EXPECTED["r25_engine_blob"],
    }
    if not all(checks.values()):
        raise SystemExit(f"FORWARD_FREEZE_IDENTITY_FAIL:{checks}")
    return checks


def ts_ms(ts: pd.Timestamp) -> int:
    return int(pd.Timestamp(ts).timestamp() * 1000)


def fetch_page(interval: str, start_ms: int, end_ms: int) -> tuple[list, str]:
    params = {
        "symbol": "BTCUSDT",
        "interval": interval,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": 1000,
    }
    last_error = None
    for endpoint in ENDPOINTS:
        try:
            req = Request(endpoint + "?" + urlencode(params), headers={"User-Agent": "r26-forward-oos/1.0"})
            with urlopen(req, timeout=30) as r:
                payload = json.loads(r.read().decode("utf-8"))
            if not isinstance(payload, list):
                raise RuntimeError(f"BAD_KLINE_PAYLOAD:{payload}")
            return payload, endpoint
        except Exception as e:
            last_error = repr(e)
    raise RuntimeError(f"BINANCE_KLINE_FETCH_FAIL:{interval}:{last_error}")


def fetch_ohlcv(interval: str, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, list[str]]:
    cur = ts_ms(start)
    end_ms = ts_ms(end)
    step = INTERVAL_MS[interval]
    rows: list = []
    sources: list[str] = []
    while cur < end_ms:
        page, source = fetch_page(interval, cur, end_ms)
        sources.append(source)
        if not page:
            break
        rows.extend(page)
        nxt = int(page[-1][0]) + step
        if nxt <= cur:
            raise RuntimeError("KLINE_PAGINATION_STALLED")
        cur = nxt
        if len(page) < 1000:
            break
        time.sleep(0.05)
    if not rows:
        raise RuntimeError(f"NO_KLINES:{interval}")
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades", "taker_base", "taker_quote", "ignore"]
    x = pd.DataFrame(rows, columns=cols)
    x = x.drop_duplicates("open_time").sort_values("open_time")
    # Closed candles only. Binance close_time is inclusive millisecond.
    x = x[pd.to_numeric(x["close_time"]) < end_ms]
    idx = pd.to_datetime(pd.to_numeric(x["open_time"]), unit="ms", utc=True)
    out = pd.DataFrame(index=idx)
    for c in ["open", "high", "low", "close", "volume"]:
        out[c] = pd.to_numeric(x[c], errors="raise").to_numpy(dtype=float)
    out.index.name = "time"
    return out, sorted(set(sources))


def phase(ts: pd.Timestamp) -> str:
    t = pd.Timestamp(ts)
    if t < STRICT_FORWARD_START:
        return "BRIDGE_HELDOUT_PRE_FREEZE"
    return "STRICT_FORWARD"


def detection_rows_with_events(det: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    cols = ["timestamp", "direction", "state", "priority_extreme_near", "r25_parent_watch_pass", "r25_seed_present", "detector_can_execute", "execution_authority", "capital_authority", "phase", "streak_id"]
    if det.empty:
        empty = pd.DataFrame(columns=cols)
        return empty, pd.DataFrame(columns=cols + ["event_type"])
    d = det.copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True)
    d = d[d["timestamp"] >= POLICY_OOS_START].sort_values(["direction", "timestamp"]).reset_index(drop=True)
    d["phase"] = d["timestamp"].map(phase)
    streaks = []
    events = []
    for direction, q in d.groupby("direction", sort=False):
        q = q.sort_values("timestamp")
        streak_id = 0
        prev_ts = None
        prev_state = None
        for idx, r in q.iterrows():
            ts = pd.Timestamp(r["timestamp"])
            gap = None if prev_ts is None else ts - prev_ts
            new_streak = prev_ts is None or gap > pd.Timedelta(hours=4, minutes=1)
            if new_streak:
                streak_id += 1
            streaks.append((idx, streak_id))
            if new_streak or str(r["state"]) != prev_state:
                ev = r.to_dict()
                ev["streak_id"] = streak_id
                ev["event_type"] = "STREAK_START" if new_streak else "STATE_CHANGE"
                events.append(ev)
            prev_ts = ts
            prev_state = str(r["state"])
    m = dict(streaks)
    d["streak_id"] = [m[i] for i in d.index]
    e = pd.DataFrame(events)
    if not e.empty:
        e["phase"] = pd.to_datetime(e["timestamp"], utc=True).map(phase)
    return d[cols], e


def add_detection_lead(seeds: pd.DataFrame, detections: pd.DataFrame) -> pd.DataFrame:
    s = seeds.copy()
    if s.empty:
        s["phase"] = []
        s["detection_lead_hours"] = []
        return s
    s["timestamp"] = pd.to_datetime(s["timestamp"], utc=True)
    s = s[s["timestamp"] >= POLICY_OOS_START].sort_values("timestamp").reset_index(drop=True)
    s["phase"] = s["timestamp"].map(phase)
    leads = []
    for _, seed in s.iterrows():
        q = detections[(detections["direction"] == seed["direction"]) & (detections["timestamp"] <= seed["timestamp"])]
        q = q[q["state"].isin(["EARLY_DETECT", "PRIORITY_WATCH", "EXECUTION_READY"])]
        if q.empty or pd.Timestamp(q.iloc[-1]["timestamp"]) != pd.Timestamp(seed["timestamp"]):
            leads.append(np.nan)
            continue
        last_streak = int(q.iloc[-1]["streak_id"])
        z = q[q["streak_id"] == last_streak]
        first = pd.Timestamp(z["timestamp"].min())
        leads.append((pd.Timestamp(seed["timestamp"]) - first).total_seconds() / 3600.0)
    s["detection_lead_hours"] = leads
    return s


def build_positions(engine: R26Engine, seeds: pd.DataFrame, daily: pd.DataFrame, h4: pd.DataFrame, weekly: pd.DataFrame, h1: pd.DataFrame, cutoff: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    base.END_EXCL = pd.Timestamp(cutoff)
    cfg = engine.cfg
    positions: list[dict] = []
    transactions: list[dict] = []
    for direction in ["LONG", "SHORT"]:
        q = seeds[(seeds["direction"] == direction) & (seeds["timestamp"] >= POLICY_OOS_START) & (seeds["timestamp"] < cutoff)].sort_values("timestamp").reset_index(drop=True)
        last_exit = None
        last_family = None
        serial = 0
        for _, seed in q.iterrows():
            st = pd.Timestamp(seed["timestamp"])
            if last_exit is not None:
                if st <= pd.Timestamp(last_exit):
                    continue
                sessions = base.completed_sessions_since_exit(daily, pd.Timestamp(last_exit), st, cfg)
                new_family = base.family_key(seed) != last_family
                if not (sessions >= int(cfg["reset"]["minimum_completed_daily_sessions"]) and new_family):
                    continue
            serial += 1
            pos, tx = r25run.simulate_episode_r25(seed, serial, engine, daily, h4, weekly, h1, cfg)
            if pos is None:
                continue
            pos["episode_id"] = f"R26-{direction}-{serial:04d}"
            pos["phase"] = phase(pd.Timestamp(pos["seed_time"]))
            positions.append(pos)
            for r in tx:
                r["episode_id"] = pos["episode_id"]
                r["phase"] = pos["phase"]
            transactions.extend(tx)
            if pos["exit_time"] is None or pd.isna(pos["exit_time"]):
                break
            last_exit = pd.Timestamp(pos["exit_time"])
            last_family = pos["family_key"]
    positions.sort(key=lambda x: pd.Timestamp(x["seed_time"]))
    p = pd.DataFrame(positions)
    if not p.empty:
        p["legs"] = p["legs"].map(lambda x: json.dumps(x, default=str, separators=(",", ":")))
    return p, pd.DataFrame(transactions)


def strict_metrics(pos: pd.DataFrame) -> dict:
    if pos.empty:
        return {"episodes": 0, "resolved": 0, "open": 0, "mean_R_resolved": None, "sum_R_resolved": 0.0, "wins": 0, "losses": 0, "mdd_R_resolved_order": None}
    q = pos[pos["phase"] == "STRICT_FORWARD"].copy()
    if q.empty:
        return {"episodes": 0, "resolved": 0, "open": 0, "mean_R_resolved": None, "sum_R_resolved": 0.0, "wins": 0, "losses": 0, "mdd_R_resolved_order": None}
    resolved = q[q["resolved"].astype(bool)].copy()
    vals = pd.to_numeric(resolved["realized_R"], errors="coerce").dropna()
    mdd = None
    if len(vals):
        eq = vals.cumsum()
        dd = eq - eq.cummax().clip(lower=0.0)
        mdd = float(dd.min())
    return {
        "episodes": int(len(q)),
        "resolved": int(len(resolved)),
        "open": int(len(q) - len(resolved)),
        "mean_R_resolved": float(vals.mean()) if len(vals) else None,
        "sum_R_resolved": float(vals.sum()) if len(vals) else 0.0,
        "wins": int((vals > 0).sum()) if len(vals) else 0,
        "losses": int((vals < 0).sum()) if len(vals) else 0,
        "mdd_R_resolved_order": mdd,
    }


def write_latest(state: dict) -> None:
    m = state["strict_forward_metrics"]
    lines = [
        "# MASTER BTC TREND V3 R2.6 — Forward OOS",
        "",
        f"- As-of UTC: `{state['asof_utc']}`",
        f"- Policy OOS start: `{state['policy_oos_start']}`",
        f"- R2.6 final freeze: `{state['final_freeze_time']}`",
        f"- Strict forward start: `{state['strict_forward_start']}`",
        f"- Status: **{state['status']}**",
        f"- Production decision: **{state['production_decision']}**",
        "",
        "## Strict forward snapshot",
        f"- Detection rows: {state['counts']['strict_detection_rows']}",
        f"- Seeds: {state['counts']['strict_seeds']}",
        f"- Episodes: {m['episodes']} / resolved {m['resolved']} / open {m['open']}",
        f"- Resolved mean R: {m['mean_R_resolved']}",
        f"- Resolved sum R: {m['sum_R_resolved']}",
        "",
        "> No automatic production promotion. Historical diagnostics and bridge-heldout rows are excluded from strict-forward promotion evidence.",
    ]
    (HERE / "latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    HERE.mkdir(parents=True, exist_ok=True)
    freeze_checks = verify_freeze()
    now = pd.Timestamp.now(tz="UTC")
    # Do not manufacture an as-of beyond available completed candles.
    cutoff = now

    daily_raw, src_d = fetch_ohlcv("1d", WARMUP_START, cutoff)
    h4_raw, src_4 = fetch_ohlcv("4h", WARMUP_START, cutoff)
    h1_raw, src_1 = fetch_ohlcv("1h", WARMUP_START, cutoff)

    engine = R26Engine()
    bundle = build_feature_bundle(daily_raw, h4_raw, h1_raw, engine.cfg)
    daily, h4, h1, weekly = bundle["1D"], bundle["4H"], bundle["1H"], bundle["1W"]

    detections_raw = engine.detect_states(daily_raw, h4_raw, h1_raw)
    detections, events = detection_rows_with_events(detections_raw)

    seeds = engine.scan_seed_candidates(daily_raw, h4_raw, h1_raw)
    if seeds.empty:
        seeds = pd.DataFrame(columns=["direction", "route", "entry", "stop", "reference", "timestamp"])
    else:
        seeds["timestamp"] = pd.to_datetime(seeds["timestamp"], utc=True)
    seeds = add_detection_lead(seeds, detections)

    positions, transactions = build_positions(engine, seeds, daily, h4, weekly, h1, cutoff)

    detections.to_csv(HERE / "detections.csv", index=False)
    events.to_csv(HERE / "events.csv", index=False)
    seeds.to_csv(HERE / "seeds.csv", index=False)
    positions.to_csv(HERE / "positions.csv", index=False)
    transactions.to_csv(HERE / "transactions.csv", index=False)

    strict_det = detections[detections["phase"] == "STRICT_FORWARD"] if not detections.empty else detections
    strict_seed = seeds[seeds["phase"] == "STRICT_FORWARD"] if not seeds.empty else seeds
    bridge_seed = seeds[seeds["phase"] == "BRIDGE_HELDOUT_PRE_FREEZE"] if not seeds.empty else seeds

    state = {
        "model": "MASTER_BTC_TREND_V3_R2_6",
        "asof_utc": now.isoformat(),
        "policy_oos_start": POLICY_OOS_START.isoformat(),
        "final_freeze_time": FINAL_FREEZE_TIME.isoformat(),
        "strict_forward_start": STRICT_FORWARD_START.isoformat(),
        "status": "COLLECTING",
        "production_decision": "HOLD",
        "automatic_promotion": False,
        "freeze_identity": EXPECTED,
        "freeze_checks": freeze_checks,
        "data": {
            "symbol": "BTCUSDT",
            "market": "BINANCE_SPOT_PUBLIC",
            "closed_candles_only": True,
            "warmup_start": WARMUP_START.isoformat(),
            "sources_1d": src_d,
            "sources_4h": src_4,
            "sources_1h": src_1,
            "rows_1d": int(len(daily_raw)),
            "rows_4h": int(len(h4_raw)),
            "rows_1h": int(len(h1_raw)),
        },
        "counts": {
            "all_detection_rows_since_policy_start": int(len(detections)),
            "strict_detection_rows": int(len(strict_det)),
            "all_seeds_since_policy_start": int(len(seeds)),
            "bridge_seeds": int(len(bridge_seed)),
            "strict_seeds": int(len(strict_seed)),
            "positions": int(len(positions)),
        },
        "strict_forward_metrics": strict_metrics(positions),
        "governance": {
            "bridge_window_counts_for_production": False,
            "strict_forward_counts_for_production": True,
            "historical_diagnostic_counts_for_production": False,
            "no_threshold_tuning_from_forward": True,
            "no_automatic_promotion": True,
        },
    }
    (HERE / "state.json").write_text(json.dumps(state, indent=2, default=str) + "\n", encoding="utf-8")
    write_latest(state)

    run_line = json.dumps({
        "asof_utc": state["asof_utc"],
        "strict_seeds": state["counts"]["strict_seeds"],
        "strict_episodes": state["strict_forward_metrics"]["episodes"],
        "strict_resolved": state["strict_forward_metrics"]["resolved"],
        "production_decision": "HOLD",
    }, separators=(",", ":"))
    runs = HERE / "runs.jsonl"
    with runs.open("a", encoding="utf-8") as f:
        f.write(run_line + "\n")

    print(json.dumps(state, indent=2, default=str))
    print("R26_FORWARD_OOS_TRACKER_PASS")


if __name__ == "__main__":
    main()
