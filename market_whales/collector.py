from __future__ import annotations

import csv
import json
import math
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
BASE_DIR = ROOT / "market_whales"
DATA_DIR = BASE_DIR / "data"
OUT_DIR = BASE_DIR / "output"
STATE_DIR = BASE_DIR / "state"
CONFIG_PATH = BASE_DIR / "config.json"
HISTORY_PATH = DATA_DIR / "positions_history.csv"
SUMMARY_JSON = OUT_DIR / "latest_summary.json"
SUMMARY_CSV = OUT_DIR / "latest_summary.csv"
EVENTS_JSON = OUT_DIR / "latest_events.json"
STATE_PATH = STATE_DIR / "recorder_state.json"

LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
INFO_URL = "https://api.hyperliquid.xyz/info"
ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "MASTER-MARKET-WHALE-RECORDER/1.0", "Content-Type": "application/json"})


@dataclass
class PositionSnapshot:
    time_utc: str
    address: str
    display_name: str
    coin: str
    side: str
    position_value_usd: float
    signed_position_value_usd: float
    entry_px: float | None
    leverage_type: str | None
    leverage_value: float | None
    liquidation_px: float | None
    mark_price: float | None
    liquidation_distance_pct: float | None
    unrealized_pnl: float | None
    account_value: float | None
    status: str


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def parse_iso(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except Exception:
        return None


def now_hour_utc() -> datetime:
    n = datetime.now(timezone.utc)
    return n.replace(minute=0, second=0, microsecond=0)


def safe_float(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def get_json(url: str, timeout: int) -> Any:
    r = SESSION.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def post_info(payload: dict[str, Any], timeout: int) -> Any:
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            r = SESSION.post(INFO_URL, json=payload, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(str(last_err) if last_err else "Hyperliquid info request failed")


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"wallets": {}, "last_run_utc": None}
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {"wallets": {}, "last_run_utc": None}
        raw.setdefault("wallets", {})
        return raw
    except Exception:
        return {"wallets": {}, "last_run_utc": None}


def window_map(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in row.get("windowPerformances", []) or []:
        if isinstance(item, list) and len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], dict):
            out[item[0]] = item[1]
    return out


def rank_candidates(rows: list[dict[str, Any]], state: dict[str, Any], cfg: dict[str, Any]) -> tuple[list[str], dict[str, dict[str, Any]]]:
    normalized: list[dict[str, Any]] = []
    meta: dict[str, dict[str, Any]] = {}
    for row in rows:
        addr = str(row.get("ethAddress", "")).lower()
        if not ADDRESS_RE.match(addr):
            continue
        perfs = window_map(row)
        item = {
            "address": addr,
            "display_name": str(row.get("displayName") or "").strip(),
            "account_value": safe_float(row.get("accountValue")) or 0.0,
            "perfs": perfs,
        }
        normalized.append(item)
        meta[addr] = item

    scores: dict[str, float] = {}

    def award(items: list[dict[str, Any]], n: int, key_fn) -> None:
        ranked = sorted(items, key=key_fn, reverse=True)[:n]
        for rank, item in enumerate(ranked):
            scores[item["address"]] = scores.get(item["address"], 0.0) + max(1.0, n - rank)

    award(normalized, int(cfg["leaderboard_top_account_value"]), lambda x: x["account_value"])
    for window in cfg.get("leaderboard_windows", ["day", "week", "month", "allTime"]):
        n = int(cfg["leaderboard_top_pnl_each_window"])
        award(normalized, n, lambda x, w=window: safe_float((x["perfs"].get(w) or {}).get("pnl")) or -1e99)
    award(normalized, int(cfg.get("leaderboard_top_day_volume", 30)), lambda x: safe_float((x["perfs"].get("day") or {}).get("vlm")) or -1e99)

    new_candidates = [a for a, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[: int(cfg["max_new_candidates"])]]

    tracked = []
    now = datetime.now(timezone.utc)
    retention = timedelta(days=int(cfg["tracked_wallet_retention_days"]))
    for addr, w in (state.get("wallets") or {}).items():
        if not ADDRESS_RE.match(addr):
            continue
        last_active = parse_iso((w or {}).get("last_active_utc"))
        if last_active is None or now - last_active <= retention:
            tracked.append(addr)

    out: list[str] = []
    for addr in tracked + new_candidates:
        if addr not in out:
            out.append(addr)
    return out, meta


def calc_liq_distance(side: str, mark: float | None, liq: float | None) -> float | None:
    if mark is None or liq is None or mark <= 0:
        return None
    if side == "LONG":
        return (mark - liq) / mark * 100.0
    if side == "SHORT":
        return (liq - mark) / mark * 100.0
    return None


def read_history() -> list[dict[str, str]]:
    if not HISTORY_PATH.exists():
        return []
    with HISTORY_PATH.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_history(rows: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fields = ["time_utc", "address", "display_name", "coin", "side", "position_value_usd", "signed_position_value_usd", "entry_px", "leverage_type", "leverage_value", "liquidation_px", "mark_price", "liquidation_distance_pct", "unrealized_pnl", "account_value", "status"]
    with HISTORY_PATH.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


def signed_side(v: float) -> str:
    if v > 0:
        return "LONG"
    if v < 0:
        return "SHORT"
    return "FLAT"


def classify_change(prev: float, cur: float, store_threshold: float, watch_threshold: float, level1_threshold: float) -> dict[str, Any]:
    prev_abs, cur_abs = abs(prev), abs(cur)
    prev_side, cur_side = signed_side(prev), signed_side(cur)
    event = "UNCHANGED"
    magnitude = 0.0
    if prev_abs < store_threshold <= cur_abs:
        event, magnitude = "NEW", cur_abs
    elif cur_abs < store_threshold <= prev_abs:
        event, magnitude = "CLOSED", prev_abs
    elif prev_abs >= store_threshold and cur_abs >= store_threshold and prev_side != cur_side:
        event, magnitude = "FLIP", prev_abs + cur_abs
    elif prev_side == cur_side and cur_abs >= store_threshold:
        diff = cur_abs - prev_abs
        magnitude = abs(diff)
        if diff > 0:
            event = "INCREASE"
        elif diff < 0:
            event = "REDUCE"

    if magnitude >= level1_threshold:
        severity = "LEVEL1_CANDIDATE"
    elif magnitude >= watch_threshold:
        severity = "LARGE_CHANGE_CANDIDATE"
    elif event != "UNCHANGED" and magnitude >= store_threshold:
        severity = "INFO"
    else:
        severity = "NONE"
    return {"event": event, "severity": severity, "change_magnitude_usd": magnitude, "prev_side": prev_side, "prev_position_usd": prev_abs, "current_side": cur_side, "current_position_usd": cur_abs}


def nearest_prior(history: list[dict[str, str]], address: str, coin: str, target: datetime) -> dict[str, str] | None:
    best = None
    best_dt = None
    for row in history:
        if row.get("address") != address or row.get("coin") != coin:
            continue
        dt = parse_iso(row.get("time_utc"))
        if dt is None or dt > target:
            continue
        if best_dt is None or dt > best_dt:
            best, best_dt = row, dt
    return best


def recent_series(history: list[dict[str, str]], address: str, coin: str, limit: int = 6) -> list[dict[str, Any]]:
    points = []
    for row in history:
        if row.get("address") != address or row.get("coin") != coin:
            continue
        dt = parse_iso(row.get("time_utc"))
        if dt is not None:
            points.append((dt, row))
    points.sort(key=lambda x: x[0], reverse=True)
    return [{"time_utc": row.get("time_utc"), "side": row.get("side"), "position_value_usd": safe_float(row.get("position_value_usd")) or 0.0} for _, row in points[:limit]]


def build_position_summary(snap: PositionSnapshot, history: list[dict[str, str]], t: datetime, cfg: dict[str, Any]) -> dict[str, Any]:
    base = asdict(snap)
    cur_signed = snap.signed_position_value_usd
    for h in (1, 4, 24):
        p = nearest_prior(history, snap.address, snap.coin, t - timedelta(hours=h))
        if p:
            prev_signed = safe_float(p.get("signed_position_value_usd")) or 0.0
            base[f"change_{h}h"] = classify_change(prev_signed, cur_signed, float(cfg["min_store_position_usd"]), float(cfg["market_watch_threshold_usd"]), float(cfg["level1_change_usd"]))
        else:
            base[f"change_{h}h"] = None
    base["recent_series"] = recent_series(history, snap.address, snap.coin, 6)
    base["address_short"] = f"{snap.address[:8]}…{snap.address[-6:]}"
    return base


def main() -> None:
    cfg = load_config()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    t = now_hour_utc()
    timeout = int(cfg.get("request_timeout_seconds", 20))
    state = load_state()

    leaderboard_raw = get_json(LEADERBOARD_URL, timeout=60)
    leaderboard_rows = leaderboard_raw.get("leaderboardRows", []) if isinstance(leaderboard_raw, dict) else []
    candidates, leaderboard_meta = rank_candidates(leaderboard_rows, state, cfg)
    mids_raw = post_info({"type": "allMids"}, timeout)
    mids = mids_raw if isinstance(mids_raw, dict) else {}

    assets = set(cfg.get("assets", ["BTC", "ETH"]))
    min_store = float(cfg["min_store_position_usd"])
    watch_threshold = float(cfg["market_watch_threshold_usd"])
    level1_threshold = float(cfg["level1_change_usd"])
    sleep_s = float(cfg.get("query_sleep_seconds", 0.06))
    previous_wallets: dict[str, Any] = state.get("wallets") or {}
    current_positions: dict[tuple[str, str], PositionSnapshot] = {}
    current_signed_by_wallet: dict[str, dict[str, float]] = {}
    query_failures: list[dict[str, str]] = []
    queried = 0

    for address in candidates:
        try:
            user = post_info({"type": "clearinghouseState", "user": address}, timeout)
            queried += 1
            meta = leaderboard_meta.get(address, {})
            display = str(meta.get("display_name") or (previous_wallets.get(address) or {}).get("display_name") or "").strip()
            account_value = safe_float(((user or {}).get("marginSummary") or {}).get("accountValue"))
            found: dict[str, float] = {}
            for item in (user or {}).get("assetPositions", []) or []:
                pos = (item or {}).get("position") or {}
                coin = str(pos.get("coin") or "")
                if coin not in assets:
                    continue
                szi = safe_float(pos.get("szi")) or 0.0
                pv = abs(safe_float(pos.get("positionValue")) or 0.0)
                signed_value = pv if szi > 0 else (-pv if szi < 0 else 0.0)
                if pv < min_store:
                    continue
                side = signed_side(signed_value)
                mark = safe_float(mids.get(coin))
                liq = safe_float(pos.get("liquidationPx"))
                lev = pos.get("leverage") or {}
                snap = PositionSnapshot(time_utc=iso(t) or "", address=address, display_name=display, coin=coin, side=side, position_value_usd=pv, signed_position_value_usd=signed_value, entry_px=safe_float(pos.get("entryPx")), leverage_type=str(lev.get("type")) if lev.get("type") is not None else None, leverage_value=safe_float(lev.get("value")), liquidation_px=liq, mark_price=mark, liquidation_distance_pct=calc_liq_distance(side, mark, liq), unrealized_pnl=safe_float(pos.get("unrealizedPnl")), account_value=account_value, status="ACTIVE")
                current_positions[(address, coin)] = snap
                found[coin] = signed_value
            current_signed_by_wallet[address] = found
        except Exception as e:
            query_failures.append({"address": address, "error": str(e)[:180]})
        time.sleep(sleep_s)

    for address, w in previous_wallets.items():
        prev_positions = (w or {}).get("last_positions") or {}
        current = current_signed_by_wallet.get(address)
        if current is None:
            continue
        display = str((w or {}).get("display_name") or leaderboard_meta.get(address, {}).get("display_name") or "")
        for coin, prev_v in prev_positions.items():
            if coin not in assets:
                continue
            prev_signed = safe_float(prev_v) or 0.0
            if abs(prev_signed) >= min_store and coin not in current:
                current_positions[(address, coin)] = PositionSnapshot(time_utc=iso(t) or "", address=address, display_name=display, coin=coin, side="FLAT", position_value_usd=0.0, signed_position_value_usd=0.0, entry_px=None, leverage_type=None, leverage_value=None, liquidation_px=None, mark_price=safe_float(mids.get(coin)), liquidation_distance_pct=None, unrealized_pnl=None, account_value=None, status="CLOSED_OR_BELOW_5M")

    old_history = read_history()
    latest_rows = [{k: ("" if v is None else v) for k, v in asdict(s).items()} for s in current_positions.values()]
    keyed = {(r.get("time_utc"), r.get("address"), r.get("coin")): r for r in old_history}
    for row in latest_rows:
        keyed[(row["time_utc"], row["address"], row["coin"])] = row
    history = list(keyed.values())
    history.sort(key=lambda r: (r.get("time_utc", ""), r.get("address", ""), r.get("coin", "")))
    cutoff = t - timedelta(days=int(cfg["history_retention_days"]))
    history = [r for r in history if (parse_iso(r.get("time_utc")) or t) >= cutoff]
    write_history(history)

    events: list[dict[str, Any]] = []
    updated_wallets = dict(previous_wallets)
    now_real = datetime.now(timezone.utc)
    for address in candidates:
        if address not in current_signed_by_wallet:
            continue
        oldw = previous_wallets.get(address) or {}
        prev_positions = oldw.get("last_positions") or {}
        cur_positions = current_signed_by_wallet.get(address) or {}
        all_coins = set(prev_positions) | set(cur_positions)
        display = str(leaderboard_meta.get(address, {}).get("display_name") or oldw.get("display_name") or "")
        for coin in all_coins:
            prev_signed = safe_float(prev_positions.get(coin)) or 0.0
            cur_signed = safe_float(cur_positions.get(coin)) or 0.0
            ch = classify_change(prev_signed, cur_signed, min_store, watch_threshold, level1_threshold)
            if ch["severity"] in {"LARGE_CHANGE_CANDIDATE", "LEVEL1_CANDIDATE"}:
                snap = current_positions.get((address, coin))
                events.append({"time_utc": iso(t), "address": address, "address_short": f"{address[:8]}…{address[-6:]}", "display_name": display, "coin": coin, **ch, "entry_px": snap.entry_px if snap else None, "leverage_value": snap.leverage_value if snap else None, "liquidation_px": snap.liquidation_px if snap else None, "liquidation_distance_pct": snap.liquidation_distance_pct if snap else None})
        active_now = any(abs(safe_float(v) or 0.0) >= min_store for v in cur_positions.values())
        updated_wallets[address] = {"display_name": display, "first_seen_utc": oldw.get("first_seen_utc") or iso(now_real), "last_seen_utc": iso(now_real), "last_active_utc": iso(now_real) if active_now else oldw.get("last_active_utc"), "last_positions": {coin: value for coin, value in cur_positions.items() if abs(value) >= min_store}}

    retention = timedelta(days=int(cfg["tracked_wallet_retention_days"]))
    pruned_wallets: dict[str, Any] = {}
    for address, w in updated_wallets.items():
        last_active = parse_iso((w or {}).get("last_active_utc"))
        if last_active is None or now_real - last_active <= retention:
            pruned_wallets[address] = w

    active_summaries: list[dict[str, Any]] = []
    for snap in current_positions.values():
        if snap.position_value_usd >= watch_threshold and snap.side in {"LONG", "SHORT"}:
            active_summaries.append(build_position_summary(snap, history, t, cfg))
    active_summaries.sort(key=lambda x: x.get("position_value_usd", 0.0), reverse=True)

    per_asset: dict[str, list[dict[str, Any]]] = {}
    for coin in assets:
        per_asset[coin] = [x for x in active_summaries if x.get("coin") == coin][: int(cfg["top_output_per_asset"])]

    scout: dict[str, list[dict[str, Any]]] = {}
    for coin in assets:
        rows = []
        for snap in current_positions.values():
            if snap.coin == coin and snap.side in {"LONG", "SHORT"} and min_store <= snap.position_value_usd < watch_threshold:
                rows.append(build_position_summary(snap, history, t, cfg))
        rows.sort(key=lambda x: x.get("position_value_usd", 0.0), reverse=True)
        scout[coin] = rows[:3]

    events.sort(key=lambda x: x.get("change_magnitude_usd", 0.0), reverse=True)
    payload = {"engine": "MASTER_MARKET_WHALE_TIME_SERIES_V1", "source": "Hyperliquid public leaderboard + official info API", "snapshot_time_utc": iso(t), "leaderboard_rows": len(leaderboard_rows), "candidate_wallets": len(candidates), "queried_wallets": queried, "query_failures": len(query_failures), "min_internal_store_position_usd": min_store, "market_watch_threshold_usd": watch_threshold, "level1_change_threshold_usd": level1_threshold, "top_positions": per_asset, "scout_5m_to_20m": scout, "large_change_events": events[:30], "notes": ["Public leaderboard displayName is a label, not verified real-world identity.", "Missing API data never implies a closed position; closure is recorded only after a successful wallet query.", "20M/50M labels are MARKET watch candidates, not trading signals by themselves."]}
    SUMMARY_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    EVENTS_JSON.write_text(json.dumps({"snapshot_time_utc": iso(t), "events": events[:100]}, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_fields = ["coin", "address", "display_name", "side", "position_value_usd", "entry_px", "leverage_value", "liquidation_px", "mark_price", "liquidation_distance_pct", "unrealized_pnl", "account_value"]
    with SUMMARY_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=csv_fields)
        w.writeheader()
        for row in active_summaries:
            w.writerow({k: row.get(k, "") for k in csv_fields})

    state_payload = {"last_run_utc": iso(now_real), "snapshot_hour_utc": iso(t), "source": "Hyperliquid", "leaderboard_rows": len(leaderboard_rows), "candidate_wallets": len(candidates), "queried_wallets": queried, "query_failures": len(query_failures), "tracked_wallets": len(pruned_wallets), "active_market_whales": len(active_summaries), "large_change_events": len(events), "wallets": pruned_wallets, "failure_samples": query_failures[:10]}
    STATE_PATH.write_text(json.dumps(state_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: state_payload[k] for k in ["snapshot_hour_utc", "leaderboard_rows", "candidate_wallets", "queried_wallets", "query_failures", "tracked_wallets", "active_market_whales", "large_change_events"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
