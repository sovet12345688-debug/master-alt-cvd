from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
VAULT = ROOT / "market_vault"
WHALES = ROOT / "market_whales"
DERIV = ROOT / "derivatives"

MARKET_HISTORY = VAULT / "data" / "market_history.csv"
MARKET_SUMMARY = VAULT / "output" / "latest_summary.json"
ETF_SUMMARY = VAULT / "output" / "latest_etf_flows.json"
WHALE_HISTORY = WHALES / "data" / "positions_history.csv"
MICRO_HISTORY = DERIV / "data" / "hourly_microstructure.csv"
DERIV_HISTORY = DERIV / "data" / "hourly_derivatives.csv"

STABLE_OUT = VAULT / "output" / "latest_stablecoin_windows.json"
ACTOR_OUT = VAULT / "output" / "latest_actor_flows.json"

STABLE_METRICS = ("USDT_SUPPLY", "USDC_SUPPLY", "STABLECOIN_TOTAL_SUPPLY")
ASSETS = ("BTC", "ETH")
WHALE_THRESHOLD_USD = 20_000_000.0


def parse_dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except Exception:
        return None


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def f(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def nearest(rows: list[dict[str, str]], target: datetime, time_key: str, *, predicate=None, tolerance_min: int = 90) -> dict[str, str] | None:
    best = None
    best_gap = None
    for row in rows:
        if predicate and not predicate(row):
            continue
        dt = parse_dt(row.get(time_key))
        if dt is None:
            continue
        gap = abs((dt - target).total_seconds()) / 60.0
        if gap <= tolerance_min and (best_gap is None or gap < best_gap):
            best, best_gap = row, gap
    return best


def delta_block(current: float | None, prior: float | None, row: dict[str, str] | None = None) -> dict[str, Any] | None:
    if current is None or prior is None:
        return None
    delta = current - prior
    pct = delta / abs(prior) * 100.0 if prior else None
    out = {"previous_value": prior, "delta": delta, "delta_pct": pct}
    if row:
        out["previous_snapshot_hour_utc"] = row.get("snapshot_hour_utc") or row.get("time_utc") or row.get("window_end_utc")
    return out


def build_stablecoin_windows() -> dict[str, Any]:
    summary = read_json(MARKET_SUMMARY)
    history = read_csv(MARKET_HISTORY)
    snapshot = parse_dt(summary.get("snapshot_hour_utc"))
    metrics = {m.get("metric"): m for m in summary.get("metrics", []) if isinstance(m, dict)}
    out: dict[str, Any] = {
        "engine": "MASTER_MARKET_STABLECOIN_WINDOWS_V1",
        "schema_version": "1.0",
        "generated_at_utc": iso(datetime.now(UTC)),
        "source": "market_vault/data/market_history.csv",
        "comparison_rule": "same metric + same source only; no interpolation/backfill",
        "windows": ["prior", "1D", "3D", "5D", "7D", "20D"],
        "metrics": [],
        "failures": [],
    }
    if snapshot is None:
        out["failures"].append("missing market summary snapshot_hour_utc")
        return out

    for name in STABLE_METRICS:
        curm = metrics.get(name)
        if not curm:
            out["metrics"].append({"metric": name, "status": "N/A", "reason": "current metric unavailable"})
            continue
        current = f(curm.get("value"))
        source = str(curm.get("source") or "")
        block: dict[str, Any] = {
            "metric": name,
            "status": "OK" if current is not None else "N/A",
            "current_value": current,
            "unit": curm.get("unit"),
            "source": source,
            "source_observation_time": curm.get("source_observation_time"),
            "vs_prior": curm.get("vs_prior_snapshot"),
        }
        for label, days, tol in (("1D", 1, 90), ("3D", 3, 90), ("5D", 5, 90), ("7D", 7, 180), ("20D", 20, 180)):
            row = nearest(
                history,
                snapshot - timedelta(days=days),
                "snapshot_hour_utc",
                predicate=lambda r, n=name, s=source: r.get("metric") == n and r.get("source") == s and r.get("status") == "OK" and f(r.get("value")) is not None,
                tolerance_min=tol,
            )
            block[f"vs_{label.lower()}"] = delta_block(current, f(row.get("value")) if row else None, row)
        out["metrics"].append(block)
    return out


def snapshot_times(rows: list[dict[str, str]], coin: str) -> list[datetime]:
    vals = set()
    for row in rows:
        if row.get("coin") != coin:
            continue
        dt = parse_dt(row.get("time_utc"))
        if dt:
            vals.add(dt)
    return sorted(vals)


def nearest_snapshot_time(times: list[datetime], target: datetime, tolerance_min: int = 90) -> datetime | None:
    if not times:
        return None
    best = min(times, key=lambda x: abs((x - target).total_seconds()))
    return best if abs((best - target).total_seconds()) / 60.0 <= tolerance_min else None


def aggregate_whales(rows: list[dict[str, str]], coin: str, at: datetime | None) -> dict[str, Any] | None:
    if at is None:
        return None
    long_usd = short_usd = 0.0
    count_long = count_short = 0
    for row in rows:
        if row.get("coin") != coin or parse_dt(row.get("time_utc")) != at:
            continue
        value = f(row.get("position_value_usd"))
        signed = f(row.get("signed_position_value_usd"))
        side = str(row.get("side") or "")
        if value is None or value < WHALE_THRESHOLD_USD:
            continue
        if signed is None:
            signed = value if side == "LONG" else (-value if side == "SHORT" else 0.0)
        if signed > 0:
            long_usd += abs(signed); count_long += 1
        elif signed < 0:
            short_usd += abs(signed); count_short += 1
    return {
        "snapshot_time_utc": iso(at),
        "threshold_usd": WHALE_THRESHOLD_USD,
        "long_usd": long_usd,
        "short_usd": short_usd,
        "net_usd": long_usd - short_usd,
        "long_positions": count_long,
        "short_positions": count_short,
    }


def valid_ratio(row: dict[str, str]) -> bool:
    return f(row.get("long_position_ratio")) is not None and f(row.get("short_position_ratio")) is not None


def ratio_block(row: dict[str, str] | None) -> dict[str, Any] | None:
    if not row:
        return None
    lp = f(row.get("long_position_ratio")); sp = f(row.get("short_position_ratio"))
    if lp is None or sp is None:
        return None
    return {
        "time_utc": row.get("window_end_utc"),
        "long_pct": lp * 100.0,
        "short_pct": sp * 100.0,
        "long_short_ratio": f(row.get("long_short_position_ratio")),
        "venue": row.get("venue"),
    }


def latest_valid(rows: list[dict[str, str]], time_key: str, predicate) -> dict[str, str] | None:
    best = None; best_dt = None
    for row in rows:
        if not predicate(row):
            continue
        dt = parse_dt(row.get(time_key))
        if dt and (best_dt is None or dt > best_dt):
            best, best_dt = row, dt
    return best


def latest_funding(rows: list[dict[str, str]], symbol: str) -> dict[str, Any] | None:
    row = latest_valid(rows, "time_utc", lambda r: r.get("symbol") == symbol and r.get("status") == "OK" and f(r.get("last_funding_rate")) is not None)
    if not row:
        return None
    return {"time_utc": row.get("time_utc"), "funding_rate": f(row.get("last_funding_rate")), "venue": row.get("venue")}


def build_actor_flows() -> dict[str, Any]:
    etf = read_json(ETF_SUMMARY)
    wh = read_csv(WHALE_HISTORY)
    micro = read_csv(MICRO_HISTORY)
    deriv = read_csv(DERIV_HISTORY)
    out: dict[str, Any] = {
        "engine": "MASTER_MARKET_ACTOR_FLOW_ADAPTER_V1",
        "schema_version": "1.0",
        "generated_at_utc": iso(datetime.now(UTC)),
        "score_weight": 0,
        "rule": "actual observable values only; no institution/whale/retail synthetic score",
        "institution": {},
        "whale": {},
        "retail_proxy": {},
        "failures": [],
    }

    etf_assets = {x.get("asset"): x for x in etf.get("assets", []) if isinstance(x, dict)}
    for coin in ASSETS:
        x = etf_assets.get(coin)
        out["institution"][coin] = {
            "source": "BTC/ETH spot ETF net flow",
            "unit": "USD millions",
            "latest_trading_date": x.get("latest_trading_date") if x else None,
            "flow_1d_usd_m": f(x.get("flow_1d_usd_m")) if x else None,
            "flow_3d_usd_m": f(x.get("flow_3d_usd_m")) if x else None,
            "flow_7d_usd_m": f(x.get("flow_7d_usd_m")) if x else None,
            "flow_20d_usd_m": f(x.get("flow_20d_usd_m")) if x else None,
        }

        times = snapshot_times(wh, coin)
        latest = times[-1] if times else None
        wb: dict[str, Any] = {"current": aggregate_whales(wh, coin, latest)}
        if latest:
            for label, days in (("1d", 1), ("3d", 3), ("7d", 7)):
                t = nearest_snapshot_time(times, latest - timedelta(days=days), 90 if days < 7 else 180)
                wb[label] = aggregate_whales(wh, coin, t)
        else:
            wb.update({"1d": None, "3d": None, "7d": None})
        out["whale"][coin] = wb

        symbol = coin + "USDT"
        current_row = latest_valid(micro, "window_end_utc", lambda r, s=symbol: r.get("symbol") == s and valid_ratio(r))
        current_dt = parse_dt(current_row.get("window_end_utc")) if current_row else None
        rb: dict[str, Any] = {
            "definition": "Bitget futures active long/short position ratio proxy; wallet identity is not verified retail identity",
            "current": ratio_block(current_row),
            "funding": latest_funding(deriv, symbol),
        }
        if current_dt:
            for label, days in (("1d", 1), ("3d", 3), ("7d", 7)):
                row = nearest(micro, current_dt - timedelta(days=days), "window_end_utc", predicate=lambda r, s=symbol: r.get("symbol") == s and valid_ratio(r), tolerance_min=90 if days < 7 else 180)
                rb[label] = ratio_block(row)
        else:
            rb.update({"1d": None, "3d": None, "7d": None})
        out["retail_proxy"][coin] = rb

    return out


def validate(stable: dict[str, Any], actors: dict[str, Any]) -> None:
    if stable.get("engine") != "MASTER_MARKET_STABLECOIN_WINDOWS_V1":
        raise SystemExit("wrong stablecoin adapter engine")
    if actors.get("engine") != "MASTER_MARKET_ACTOR_FLOW_ADAPTER_V1":
        raise SystemExit("wrong actor adapter engine")
    for coin in ASSETS:
        inst = actors.get("institution", {}).get(coin, {})
        if "flow_7d_usd_m" not in inst:
            raise SystemExit(f"{coin} institution missing ETF 7D field")
        wh = actors.get("whale", {}).get(coin, {})
        if not all(k in wh for k in ("current", "1d", "3d", "7d")):
            raise SystemExit(f"{coin} whale windows incomplete")
        rp = actors.get("retail_proxy", {}).get(coin, {})
        if not all(k in rp for k in ("current", "1d", "3d", "7d")):
            raise SystemExit(f"{coin} retail proxy windows incomplete")


def main() -> None:
    (VAULT / "output").mkdir(parents=True, exist_ok=True)
    stable = build_stablecoin_windows()
    actors = build_actor_flows()
    validate(stable, actors)
    STABLE_OUT.write_text(json.dumps(stable, ensure_ascii=False, indent=2), encoding="utf-8")
    ACTOR_OUT.write_text(json.dumps(actors, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"stablecoin": stable.get("engine"), "actors": actors.get("engine"), "generated_at_utc": actors.get("generated_at_utc")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
