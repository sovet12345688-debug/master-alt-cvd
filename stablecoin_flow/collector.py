from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "stablecoin_flow"
CONFIG_PATH = BASE / "config.json"
DATA_DIR = BASE / "data"
OUT_DIR = BASE / "output"
STATE_DIR = BASE / "state"
HISTORY_PATH = DATA_DIR / "hourly_flow.csv"
SUMMARY_JSON = OUT_DIR / "latest_summary.json"
STATE_PATH = STATE_DIR / "collector_state.json"

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_TOPIC = "0x" + ("0" * 64)
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "MASTER-STABLECOIN-FLOW/1.0"})


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def normalize_address(a: str) -> str:
    return a.lower()


def address_topic(a: str) -> str:
    h = normalize_address(a).replace("0x", "")
    return "0x" + ("0" * 24) + h


def topic_to_address(topic: str) -> str:
    h = topic.lower().replace("0x", "")
    return "0x" + h[-40:]


def safe_int_hex(v: Any) -> int | None:
    try:
        return int(str(v), 16)
    except Exception:
        return None


def rpc_call(url: str, method: str, params: list[Any]) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    r = SESSION.post(url, json=payload, timeout=25)
    r.raise_for_status()
    body = r.json()
    if body.get("error"):
        raise RuntimeError(str(body["error"]))
    return body.get("result")


def choose_rpc(cfg: dict[str, Any]) -> tuple[str, list[str]]:
    errors: list[str] = []
    for url in cfg.get("rpc_urls") or []:
        try:
            result = rpc_call(url, "eth_blockNumber", [])
            if result:
                return url, errors
        except Exception as e:
            errors.append(f"{url}:{type(e).__name__}:{str(e)[:140]}")
    raise RuntimeError("No Ethereum RPC available | " + " | ".join(errors))


def get_block(url: str, number: int) -> dict[str, Any]:
    result = rpc_call(url, "eth_getBlockByNumber", [hex(number), False])
    if not result:
        raise RuntimeError(f"Missing block {number}")
    return result


def get_block_ts(url: str, number: int) -> int:
    b = get_block(url, number)
    ts = safe_int_hex(b.get("timestamp"))
    if ts is None:
        raise RuntimeError(f"Missing timestamp for block {number}")
    return ts


def find_block_at_or_before(url: str, target_ts: int, latest_block: int) -> int:
    lo, hi = 0, latest_block
    best = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        ts = get_block_ts(url, mid)
        if ts <= target_ts:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def get_logs(url: str, contract: str, from_block: int, to_block: int, from_topic: str) -> list[dict[str, Any]]:
    params = [{
        "address": contract,
        "fromBlock": hex(from_block),
        "toBlock": hex(to_block),
        "topics": [TRANSFER_TOPIC, from_topic],
    }]
    result = rpc_call(url, "eth_getLogs", params)
    return result or []


def decode_transfer(log: dict[str, Any], decimals: int) -> dict[str, Any] | None:
    topics = log.get("topics") or []
    if len(topics) < 3:
        return None
    raw = safe_int_hex(log.get("data"))
    block = safe_int_hex(log.get("blockNumber"))
    if raw is None or block is None:
        return None
    return {
        "from": topic_to_address(topics[1]),
        "to": topic_to_address(topics[2]),
        "amount": raw / (10 ** decimals),
        "block_number": block,
        "tx_hash": log.get("transactionHash"),
        "log_index": safe_int_hex(log.get("logIndex")),
    }


def collect_token(
    rpc_url: str,
    token: str,
    cfg: dict[str, Any],
    start_block: int,
    end_block: int,
    exchange_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    token_cfg = cfg["tokens"][token]
    contract = token_cfg["contract"]
    decimals = int(token_cfg["decimals"])
    mode = token_cfg.get("issuer_flow_mode")
    decoded: list[dict[str, Any]] = []
    errors: list[str] = []

    source_topics: list[tuple[str, str]] = []
    if mode == "treasury_outflow":
        for item in token_cfg.get("treasury_addresses") or []:
            source_topics.append((item["label"], address_topic(item["address"])))
    elif mode == "mint_destination_proxy":
        source_topics.append(("ZERO_ADDRESS_MINT", ZERO_TOPIC))
    else:
        errors.append(f"unsupported_mode:{mode}")

    for source_label, from_topic in source_topics:
        try:
            logs = get_logs(rpc_url, contract, start_block, end_block, from_topic)
            for log in logs:
                d = decode_transfer(log, decimals)
                if d:
                    d["source_label"] = source_label
                    d["exchange_match"] = exchange_map.get(normalize_address(d["to"]))
                    decoded.append(d)
        except Exception as e:
            errors.append(f"{source_label}:{type(e).__name__}:{str(e)[:160]}")

    total_issuer_out = sum(x["amount"] for x in decoded)
    exchange_rows = [x for x in decoded if x.get("exchange_match")]
    exchange_amount = sum(x["amount"] for x in exchange_rows)

    by_exchange: dict[str, float] = {}
    for x in exchange_rows:
        name = str((x.get("exchange_match") or {}).get("exchange") or "UNKNOWN")
        by_exchange[name] = by_exchange.get(name, 0.0) + float(x["amount"])

    return {
        "token": token,
        "chain": "ethereum",
        "mode": mode,
        "status": "OK" if not errors else ("PARTIAL" if decoded else "N/A"),
        "issuer_transfer_count": len(decoded),
        "issuer_transfer_amount": total_issuer_out,
        "verified_exchange_match_count": len(exchange_rows),
        "verified_exchange_amount": exchange_amount,
        "verified_exchange_breakdown": by_exchange,
        "exchange_coverage": "PARTIAL_LABEL_SET",
        "matched_transfers": [
            {
                "to": x["to"],
                "amount": x["amount"],
                "exchange": x["exchange_match"]["exchange"],
                "label": x["exchange_match"]["label"],
                "tx_hash": x["tx_hash"],
                "block_number": x["block_number"],
            }
            for x in exchange_rows
        ],
        "errors": errors,
    }


def read_history() -> list[dict[str, str]]:
    if not HISTORY_PATH.exists():
        return []
    with HISTORY_PATH.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_history(rows: list[dict[str, Any]]) -> None:
    fields = [
        "window_end_utc", "window_start_utc", "chain", "token", "mode", "status",
        "issuer_transfer_count", "issuer_transfer_amount", "verified_exchange_match_count",
        "verified_exchange_amount", "exchange_coverage", "errors",
    ]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            item = dict(r)
            if isinstance(item.get("errors"), list):
                item["errors"] = " | ".join(item["errors"])
            w.writerow({k: item.get(k, "") for k in fields})


def main() -> None:
    cfg = load_config()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    ended = now_utc()
    started = ended - timedelta(minutes=int(cfg.get("window_minutes", 60)))
    rpc_url, rpc_errors = choose_rpc(cfg)
    latest_hex = rpc_call(rpc_url, "eth_blockNumber", [])
    latest_block = int(latest_hex, 16)
    start_block = find_block_at_or_before(rpc_url, int(started.timestamp()), latest_block)

    exchange_map = {normalize_address(x["address"]): x for x in cfg.get("exchange_addresses") or []}
    assets = [collect_token(rpc_url, token, cfg, start_block, latest_block, exchange_map) for token in ("USDT", "USDC")]

    payload = {
        "engine": "MASTER_STABLECOIN_TREASURY_EXCHANGE_V1",
        "generated_at_utc": iso(now_utc()),
        "window_start_utc": iso(started),
        "window_end_utc": iso(ended),
        "chain": "ethereum",
        "rpc_url": rpc_url,
        "block_range": [start_block, latest_block],
        "rpc_fallback_log": rpc_errors,
        "coverage_rule": cfg.get("coverage_rule"),
        "rules": {
            "USDT": "Confirmed transfers from verified Tether Treasury address. Only destinations matching verified exchange registry count as Treasury->Exchange.",
            "USDC": "Circle does not use one public treasury address for all minting; zero-address mints are used as issuer mint destination proxy. Only verified exchange destinations count as Mint->Exchange.",
            "no_inference": "Unmatched addresses are never classified as exchanges.",
        },
        "assets": assets,
    }
    SUMMARY_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    old = read_history()
    key: dict[tuple[str, str], dict[str, Any]] = {(r.get("window_end_utc", ""), r.get("token", "")): r for r in old}
    for a in assets:
        row = {
            "window_end_utc": iso(ended),
            "window_start_utc": iso(started),
            **{k: a.get(k) for k in [
                "chain", "token", "mode", "status", "issuer_transfer_count", "issuer_transfer_amount",
                "verified_exchange_match_count", "verified_exchange_amount", "exchange_coverage", "errors"
            ]},
        }
        key[(row["window_end_utc"] or "", row["token"])] = row

    cutoff = ended - timedelta(days=90)
    rows: list[dict[str, Any]] = []
    for r in key.values():
        try:
            dt = datetime.fromisoformat(str(r.get("window_end_utc", "")).replace("Z", "+00:00"))
            if dt >= cutoff:
                rows.append(r)
        except Exception:
            rows.append(r)
    rows.sort(key=lambda r: (str(r.get("window_end_utc", "")), str(r.get("token", ""))))
    write_history(rows)

    state = {
        "last_run_utc": iso(now_utc()),
        "rpc_url": rpc_url,
        "ok_count": sum(a["status"] == "OK" for a in assets),
        "partial_count": sum(a["status"] == "PARTIAL" for a in assets),
        "na_count": sum(a["status"] == "N/A" for a in assets),
        "verified_exchange_labels": len(exchange_map),
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
