from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
ACTOR_OUT = ROOT / "market_vault" / "output" / "latest_actor_flows.json"
ASSETS = ("BTC", "ETH")
MAX_RETAIL_AGE_MINUTES = 90.0


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def age_minutes(value: str | None) -> float | None:
    dt = parse_dt(value)
    if dt is None:
        return None
    return max(0.0, (datetime.now(UTC) - dt).total_seconds() / 60.0)


def round_age(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def freshness_status(block: dict[str, Any] | None) -> tuple[str, float | None]:
    if not isinstance(block, dict):
        return "MISSING_UPSTREAM", None
    age = age_minutes(block.get("time_utc"))
    if age is None:
        return "MISSING_UPSTREAM", None
    if age > MAX_RETAIL_AGE_MINUTES:
        return "STALE_UPSTREAM", age
    return "OK", age


def apply_guard(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("engine") != "MASTER_MARKET_ACTOR_FLOW_ADAPTER_V1":
        raise SystemExit("wrong actor adapter engine")

    payload["retail_freshness_max_age_minutes"] = MAX_RETAIL_AGE_MINUTES
    warnings = payload.setdefault("warnings", [])
    if not isinstance(warnings, list):
        warnings = []
        payload["warnings"] = warnings

    statuses: list[str] = []
    for coin in ASSETS:
        rp = payload.setdefault("retail_proxy", {}).setdefault(coin, {})

        current_status, current_age = freshness_status(rp.get("current"))
        funding_status, funding_age = freshness_status(rp.get("funding"))

        rp["current_status"] = current_status
        rp["source_age_minutes"] = round_age(current_age)
        rp["funding_status"] = funding_status
        rp["funding_age_minutes"] = round_age(funding_age)
        rp["freshness_max_age_minutes"] = MAX_RETAIL_AGE_MINUTES

        if current_status != "OK":
            rp["current"] = None
            # Historical windows were selected relative to the stale current row.
            # Do not expose them as current 1D/3D/7D comparisons until upstream recovers.
            rp["1d"] = None
            rp["3d"] = None
            rp["7d"] = None
            msg = f"{coin} retail current {current_status.lower()}"
            if current_age is not None:
                msg += f" age={current_age:.1f}m"
            if msg not in warnings:
                warnings.append(msg)

        if funding_status != "OK":
            rp["funding"] = None
            msg = f"{coin} retail funding {funding_status.lower()}"
            if funding_age is not None:
                msg += f" age={funding_age:.1f}m"
            if msg not in warnings:
                warnings.append(msg)

        statuses.append(current_status)

    if all(x == "OK" for x in statuses):
        payload["retail_proxy_status"] = "OK"
    elif all(x == "MISSING_UPSTREAM" for x in statuses):
        payload["retail_proxy_status"] = "MISSING_UPSTREAM"
    else:
        payload["retail_proxy_status"] = "DEGRADED_UPSTREAM"

    payload["retail_freshness_checked_at_utc"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return payload


def validate(payload: dict[str, Any]) -> None:
    if payload.get("retail_freshness_max_age_minutes") != MAX_RETAIL_AGE_MINUTES:
        raise SystemExit("retail freshness threshold missing")

    for coin in ASSETS:
        rp = payload.get("retail_proxy", {}).get(coin, {})
        status = rp.get("current_status")
        if status not in ("OK", "STALE_UPSTREAM", "MISSING_UPSTREAM"):
            raise SystemExit(f"{coin} invalid retail current_status={status}")

        if status == "OK":
            current = rp.get("current")
            age = rp.get("source_age_minutes")
            if not isinstance(current, dict):
                raise SystemExit(f"{coin} retail status OK but current missing")
            if age is None or float(age) > MAX_RETAIL_AGE_MINUTES:
                raise SystemExit(f"{coin} retail status OK but freshness invalid age={age}")
        else:
            if rp.get("current") is not None:
                raise SystemExit(f"{coin} stale/missing retail current must be null")
            for key in ("1d", "3d", "7d"):
                if rp.get(key) is not None:
                    raise SystemExit(f"{coin} stale retail comparison must be null: {key}")

        funding_status = rp.get("funding_status")
        if funding_status not in ("OK", "STALE_UPSTREAM", "MISSING_UPSTREAM"):
            raise SystemExit(f"{coin} invalid funding_status={funding_status}")
        if funding_status == "OK":
            age = rp.get("funding_age_minutes")
            if rp.get("funding") is None or age is None or float(age) > MAX_RETAIL_AGE_MINUTES:
                raise SystemExit(f"{coin} funding freshness contract violated")
        elif rp.get("funding") is not None:
            raise SystemExit(f"{coin} stale/missing funding must be null")


def main() -> None:
    payload = json.loads(ACTOR_OUT.read_text(encoding="utf-8"))
    payload = apply_guard(payload)
    validate(payload)
    ACTOR_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "MARKET_ACTOR_RETAIL_FRESHNESS_GUARD=PASS",
        payload.get("retail_proxy_status"),
        payload.get("retail_freshness_max_age_minutes"),
    )


if __name__ == "__main__":
    main()
