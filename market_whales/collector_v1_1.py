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
SESSION.headers.update({"User-Agent": "MASTER-MARKET-WHALE-RECORDER/1.1", "Content-Type": "application/json"})
UTC = timezone.utc


@dataclass
class PositionSnapshot:
    time_utc: str
    address: str
    display_name: str
    coin: str
    side: str
    position_size: float
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
    size_schema: str = "SZI_V1_1"


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
    n = datetime.now(UTC)
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
        item = {"address": addr, "display_name": str(row.get("displayName") or "").strip(), "account_value": safe_float(row.get("accountValue")) or 0.0, "perfs": perfs}
        normalized.append(item)
        meta[addr] = item

    scores: dict[str, float] = {}
    def award(items: list[dict[str, Any]], n: int, key_fn) -> None:
        ranked = sorted(items, key=key_fn, reverse=True)[:n]
        for rank, item in enumerate(ranked):
            scores[item["address"]] = scores.get(item["address"], 0.0) + max(1.0, n-rank)

    award(normalized, int(cfg["leaderboard_top_account_value"]), lambda x: x["account_value"])
    for window in cfg.get("leaderboard_windows", ["day", "week", "month", "allTime"]):
        n = int(cfg["leaderboard_top_pnl_each_window"])
        award(normalized, n, lambda x, w=window: safe_float((x["perfs"].get(w) or {}).get("pnl")) or -1e99)
    award(normalized, int(cfg.get("leaderboard_top_day_volume", 30)), lambda x: safe_float((x["perfs"].get("day") or {}).get("vlm")) or -1e99)
    new_candidates = [a for a, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:int(cfg["max_new_candidates"])]]

    tracked: list[str] = []
    now = datetime.now(UTC)
    retention = timedelta(days=int(cfg["tracked_wallet_retention_days"]))
    for addr, w in (state.get("wallets") or {}).items():
        if not ADDRESS_RE.match(addr):
            continue
        last_active = parse_iso((w or {}).get("last_active_utc"))
        if last_active is None or now-last_active <= retention:
            tracked.append(addr)
    out: list[str] = []
    for addr in tracked + new_candidates:
        if addr not in out:
            out.append(addr)
    return out, meta


def side_from_size(size: float) -> str:
    if size > 0: return "LONG"
    if size < 0: return "SHORT"
    return "FLAT"


def calc_liq_distance(side: str, mark: float | None, liq: float | None) -> float | None:
    if mark is None or liq is None or mark <= 0: return None
    if side == "LONG": return (mark-liq)/mark*100.0
    if side == "SHORT": return (liq-mark)/mark*100.0
    return None


def same_size(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-12)


def classify_size_change(prev_size: float, cur_size: float, mark: float | None, info_threshold: float, watch_threshold: float, level1_threshold: float) -> dict[str, Any]:
    prev_side, cur_side = side_from_size(prev_size), side_from_size(cur_size)
    event = "UNCHANGED"
    delta_size = cur_size - prev_size
    if same_size(prev_size, cur_size):
        delta_size = 0.0
    elif same_size(prev_size, 0.0) and not same_size(cur_size, 0.0):
        event = "NEW"
    elif not same_size(prev_size, 0.0) and same_size(cur_size, 0.0):
        event = "CLOSED"
    elif prev_side != cur_side:
        event = "FLIP"
    elif abs(cur_size) > abs(prev_size):
        event = "INCREASE"
    elif abs(cur_size) < abs(prev_size):
        event = "REDUCE"

    action_notional = abs(delta_size) * mark if mark is not None and mark > 0 else None
    if event == "UNCHANGED":
        severity = "NONE"
    elif action_notional is None:
        severity = "UNSCORED_NO_MARK"
    elif action_notional >= level1_threshold:
        severity = "LEVEL1_CANDIDATE"
    elif action_notional >= watch_threshold:
        severity = "LARGE_CHANGE_CANDIDATE"
    elif action_notional >= info_threshold:
        severity = "INFO"
    else:
        severity = "NONE"
    return {
        "event": event,
        "severity": severity,
        "prev_side": prev_side,
        "current_side": cur_side,
        "prev_position_size": prev_size,
        "current_position_size": cur_size,
        "size_change_units": delta_size,
        "action_notional_usd": action_notional,
        "reference_mark_price": mark,
    }


def read_history() -> list[dict[str, str]]:
    if not HISTORY_PATH.exists(): return []
    with HISTORY_PATH.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_history(rows: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fields = ["time_utc","address","display_name","coin","side","position_size","position_value_usd","signed_position_value_usd","entry_px","leverage_type","leverage_value","liquidation_px","mark_price","liquidation_distance_pct","unrealized_pnl","account_value","status","size_schema"]
    with HISTORY_PATH.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


def nearest_prior_size(history: list[dict[str, str]], address: str, coin: str, target: datetime, tolerance_min: int) -> tuple[dict[str,str] | None, float | None]:
    best = None; best_gap = None
    for row in history:
        if row.get("address") != address or row.get("coin") != coin: continue
        if safe_float(row.get("position_size")) is None: continue
        dt = parse_iso(row.get("time_utc"))
        if dt is None: continue
        gap = abs((dt-target).total_seconds())/60.0
        if gap <= tolerance_min and (best_gap is None or gap < best_gap):
            best, best_gap = row, gap
    return best, best_gap


def recent_series(history: list[dict[str,str]], address: str, coin: str, limit: int=6) -> list[dict[str,Any]]:
    pts=[]
    for row in history:
        if row.get("address") != address or row.get("coin") != coin: continue
        dt=parse_iso(row.get("time_utc"))
        if dt is not None:
            pts.append((dt,row))
    pts.sort(key=lambda x:x[0], reverse=True)
    out=[]
    for _,row in pts[:limit]:
        out.append({"time_utc":row.get("time_utc"),"side":row.get("side"),"position_size":safe_float(row.get("position_size")),"position_value_usd":safe_float(row.get("position_value_usd")),"size_schema":row.get("size_schema") or "LEGACY_VALUE_ONLY"})
    return out


def build_position_summary(snap: PositionSnapshot, history: list[dict[str,str]], t: datetime, cfg: dict[str,Any]) -> dict[str,Any]:
    base=asdict(snap)
    tol=int(cfg.get("window_tolerance_minutes",45))
    for h in (1,4,24):
        row,gap=nearest_prior_size(history,snap.address,snap.coin,t-timedelta(hours=h),tol)
        if row:
            prev=safe_float(row.get("position_size"))
            if prev is not None:
                ch=classify_size_change(prev,snap.position_size,snap.mark_price,float(cfg["min_store_position_usd"]),float(cfg["market_watch_threshold_usd"]),float(cfg["level1_change_usd"]))
                ch["source_gap_minutes"]=gap
                base[f"change_{h}h"]=ch
            else:
                base[f"change_{h}h"]=None
        else:
            base[f"change_{h}h"]=None
    base["recent_series"]=recent_series(history,snap.address,snap.coin,6)
    base["address_short"]=f"{snap.address[:8]}…{snap.address[-6:]}"
    return base


def self_test() -> None:
    mark=100.0
    x=classify_size_change(10.0,10.0,110.0,5,20,50)
    assert x["event"]=="UNCHANGED" and x["action_notional_usd"]==0.0
    x=classify_size_change(10.0,8.0,mark,5,20,50)
    assert x["event"]=="REDUCE" and abs(x["action_notional_usd"]-200.0)<1e-9
    x=classify_size_change(2.0,-3.0,mark,5,20,50)
    assert x["event"]=="FLIP" and abs(x["action_notional_usd"]-500.0)<1e-9
    x=classify_size_change(0.0,3.0,mark,5,20,50)
    assert x["event"]=="NEW"
    print("SIZE_ENGINE_SELF_TEST=PASS")


def main() -> None:
    cfg=load_config(); DATA_DIR.mkdir(parents=True,exist_ok=True); OUT_DIR.mkdir(parents=True,exist_ok=True); STATE_DIR.mkdir(parents=True,exist_ok=True)
    t=now_hour_utc(); timeout=int(cfg.get("request_timeout_seconds",20)); state=load_state()
    leaderboard_raw=get_json(LEADERBOARD_URL,60)
    leaderboard_rows=leaderboard_raw.get("leaderboardRows",[]) if isinstance(leaderboard_raw,dict) else []
    candidates,leaderboard_meta=rank_candidates(leaderboard_rows,state,cfg)
    mids_raw=post_info({"type":"allMids"},timeout); mids=mids_raw if isinstance(mids_raw,dict) else {}
    assets=set(cfg.get("assets",["BTC","ETH"])); min_store=float(cfg["min_store_position_usd"]); watch=float(cfg["market_watch_threshold_usd"]); level1=float(cfg["level1_change_usd"]); sleep_s=float(cfg.get("query_sleep_seconds",0.06))
    previous_wallets:dict[str,Any]=state.get("wallets") or {}
    current_positions:dict[tuple[str,str],PositionSnapshot]={}; current_sizes_by_wallet:dict[str,dict[str,float]]={}; query_failures=[]; queried=0

    for address in candidates:
        try:
            user=post_info({"type":"clearinghouseState","user":address},timeout); queried+=1
            meta=leaderboard_meta.get(address,{}); oldw=previous_wallets.get(address) or {}; display=str(meta.get("display_name") or oldw.get("display_name") or "").strip(); account_value=safe_float(((user or {}).get("marginSummary") or {}).get("accountValue"))
            found:dict[str,float]={}
            for item in (user or {}).get("assetPositions",[]) or []:
                pos=(item or {}).get("position") or {}; coin=str(pos.get("coin") or "")
                if coin not in assets: continue
                szi=safe_float(pos.get("szi")) or 0.0
                if same_size(szi,0.0): continue
                found[coin]=szi
                mark=safe_float(mids.get(coin)); pv=abs(safe_float(pos.get("positionValue")) or ((abs(szi)*mark) if mark else 0.0)); signed_value=pv if szi>0 else -pv; side=side_from_size(szi); liq=safe_float(pos.get("liquidationPx")); lev=pos.get("leverage") or {}
                status="ACTIVE" if pv>=min_store else "BELOW_5M_TRACKED"
                current_positions[(address,coin)]=PositionSnapshot(iso(t) or "",address,display,coin,side,szi,pv,signed_value,safe_float(pos.get("entryPx")),str(lev.get("type")) if lev.get("type") is not None else None,safe_float(lev.get("value")),liq,mark,calc_liq_distance(side,mark,liq),safe_float(pos.get("unrealizedPnl")),account_value,status)
            current_sizes_by_wallet[address]=found
        except Exception as e:
            query_failures.append({"address":address,"error":str(e)[:180]})
        time.sleep(sleep_s)

    # Closure rows only when this wallet query succeeded and V1.1 size baseline already exists.
    for address,oldw in previous_wallets.items():
        current=current_sizes_by_wallet.get(address)
        if current is None: continue
        prev_sizes=(oldw or {}).get("last_sizes") or {}
        if not (oldw or {}).get("size_baseline_utc"): continue
        display=str((oldw or {}).get("display_name") or leaderboard_meta.get(address,{}).get("display_name") or "")
        for coin,prev_s in prev_sizes.items():
            if coin not in assets or coin in current: continue
            ps=safe_float(prev_s) or 0.0
            if same_size(ps,0.0): continue
            mark=safe_float(mids.get(coin))
            current_positions[(address,coin)]=PositionSnapshot(iso(t) or "",address,display,coin,"FLAT",0.0,0.0,0.0,None,None,None,None,mark,None,None,None,"CLOSED", "SZI_V1_1")

    old_history=read_history(); latest_rows=[{k:("" if v is None else v) for k,v in asdict(s).items()} for s in current_positions.values()]
    keyed={(r.get("time_utc"),r.get("address"),r.get("coin")):r for r in old_history}
    for row in latest_rows: keyed[(row["time_utc"],row["address"],row["coin"])]=row
    history=list(keyed.values()); history.sort(key=lambda r:(r.get("time_utc","") ,r.get("address","") ,r.get("coin","")))
    cutoff=t-timedelta(days=int(cfg["history_retention_days"])); history=[r for r in history if (parse_iso(r.get("time_utc")) or t)>=cutoff]; write_history(history)

    events=[]; updated_wallets=dict(previous_wallets); now_real=datetime.now(UTC); size_baselines_created=0
    for address in candidates:
        if address not in current_sizes_by_wallet: continue
        oldw=previous_wallets.get(address) or {}; cur_sizes=current_sizes_by_wallet.get(address) or {}; prev_sizes=(oldw.get("last_sizes") or {}) if oldw.get("size_baseline_utc") else None
        display=str(leaderboard_meta.get(address,{}).get("display_name") or oldw.get("display_name") or "")
        if prev_sizes is None:
            size_baselines_created+=1
        else:
            for coin in set(prev_sizes)|set(cur_sizes):
                if coin not in assets: continue
                prev=safe_float(prev_sizes.get(coin)) or 0.0; cur=safe_float(cur_sizes.get(coin)) or 0.0; mark=safe_float(mids.get(coin))
                ch=classify_size_change(prev,cur,mark,min_store,watch,level1)
                if ch["severity"] in {"LARGE_CHANGE_CANDIDATE","LEVEL1_CANDIDATE"}:
                    snap=current_positions.get((address,coin))
                    prev_exposure=abs(prev)*(mark or 0.0); cur_exposure=abs(cur)*(mark or 0.0)
                    events.append({"time_utc":iso(t),"address":address,"address_short":f"{address[:8]}…{address[-6:]}","display_name":display,"coin":coin,**ch,"prev_position_usd":prev_exposure,"current_position_usd":cur_exposure,"entry_px":snap.entry_px if snap else None,"leverage_value":snap.leverage_value if snap else None,"liquidation_px":snap.liquidation_px if snap else None,"liquidation_distance_pct":snap.liquidation_distance_pct if snap else None,"change_basis":"POSITION_SIZE_SZI"})
        active_now=any((abs(sz)*(safe_float(mids.get(c)) or 0.0))>=min_store for c,sz in cur_sizes.items())
        updated_wallets[address]={"display_name":display,"first_seen_utc":oldw.get("first_seen_utc") or iso(now_real),"last_seen_utc":iso(now_real),"last_active_utc":iso(now_real) if active_now else oldw.get("last_active_utc"),"size_baseline_utc":oldw.get("size_baseline_utc") or iso(now_real),"last_sizes":{coin:size for coin,size in cur_sizes.items() if not same_size(size,0.0)},"last_positions":oldw.get("last_positions") or {},"size_engine_version":"1.1"}

    retention=timedelta(days=int(cfg["tracked_wallet_retention_days"])); pruned={}
    for address,w in updated_wallets.items():
        last_active=parse_iso((w or {}).get("last_active_utc"))
        if last_active is None or now_real-last_active<=retention: pruned[address]=w

    active_summaries=[]; scout={coin:[] for coin in assets}
    for snap in current_positions.values():
        if snap.side not in {"LONG","SHORT"}: continue
        if snap.position_value_usd>=watch: active_summaries.append(build_position_summary(snap,history,t,cfg))
        elif min_store<=snap.position_value_usd<watch: scout[snap.coin].append(build_position_summary(snap,history,t,cfg))
    active_summaries.sort(key=lambda x:x.get("position_value_usd",0.0),reverse=True)
    for coin in assets: scout[coin]=sorted(scout[coin],key=lambda x:x.get("position_value_usd",0.0),reverse=True)[:3]
    per_asset={coin:[x for x in active_summaries if x.get("coin")==coin][:int(cfg["top_output_per_asset"])] for coin in assets}
    events.sort(key=lambda x:x.get("action_notional_usd") or 0.0, reverse=True)

    payload={"engine":"MASTER_MARKET_WHALE_TIME_SERIES_V1_1","schema_version":"1.1","change_basis":"POSITION_SIZE_SZI","source":"Hyperliquid public leaderboard + official info API","snapshot_time_utc":iso(t),"leaderboard_rows":len(leaderboard_rows),"candidate_wallets":len(candidates),"queried_wallets":queried,"query_failures":len(query_failures),"min_internal_store_position_usd":min_store,"market_watch_threshold_usd":watch,"level1_change_threshold_usd":level1,"window_tolerance_minutes":int(cfg.get("window_tolerance_minutes",45)),"size_baselines_created":size_baselines_created,"top_positions":per_asset,"scout_5m_to_20m":scout,"large_change_events":events[:30],"notes":["Whale action NEW/INCREASE/REDUCE/CLOSED/FLIP is classified from Hyperliquid position size (szi), not mark-to-market USD positionValue.","action_notional_usd = absolute size change × current reference mark; current position_value_usd remains marked exposure.","Legacy V1 history has no szi and is audit-only; no size change is reconstructed from old USD values.","First successful V1.1 size observation is SIZE_BASELINE_ONLY and cannot create a whale action alert.","Missing API data never implies a closed position; closure is recorded only after a successful wallet query.","Public leaderboard displayName is a label, not verified real-world identity."]}
    SUMMARY_JSON.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    EVENTS_JSON.write_text(json.dumps({"engine":"MASTER_MARKET_WHALE_TIME_SERIES_V1_1","schema_version":"1.1","change_basis":"POSITION_SIZE_SZI","snapshot_time_utc":iso(t),"events":events[:100]},ensure_ascii=False,indent=2),encoding="utf-8")

    csv_fields=["coin","address","display_name","side","position_size","position_value_usd","entry_px","leverage_value","liquidation_px","mark_price","liquidation_distance_pct","unrealized_pnl","account_value"]
    with SUMMARY_CSV.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=csv_fields); w.writeheader()
        for row in active_summaries: w.writerow({k:row.get(k,"") for k in csv_fields})

    state_payload={"engine":"MASTER_MARKET_WHALE_TIME_SERIES_V1_1","schema_version":"1.1","change_basis":"POSITION_SIZE_SZI","size_engine":"PASS","last_run_utc":iso(now_real),"snapshot_hour_utc":iso(t),"source":"Hyperliquid","leaderboard_rows":len(leaderboard_rows),"candidate_wallets":len(candidates),"queried_wallets":queried,"query_failures":len(query_failures),"tracked_wallets":len(pruned),"active_market_whales":len(active_summaries),"large_change_events":len(events),"size_baselines_created":size_baselines_created,"wallets":pruned,"failure_samples":query_failures[:10]}
    STATE_PATH.write_text(json.dumps(state_payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:state_payload[k] for k in ["schema_version","snapshot_hour_utc","candidate_wallets","queried_wallets","query_failures","active_market_whales","large_change_events","size_baselines_created"]},ensure_ascii=False))


if __name__=="__main__":
    import sys
    if "--self-test" in sys.argv: self_test()
    else: main()
