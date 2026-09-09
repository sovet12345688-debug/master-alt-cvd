from __future__ import annotations

import csv
import json
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

from market_yen_carry import collector as yc

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "market_yen_carry"
OUT_DIR = BASE / "backtest_output"
JSON_OUT = OUT_DIR / "aug2024.json"
MD_OUT = OUT_DIR / "aug2024.md"

START_FETCH = date(2024, 7, 15)
END_FETCH = date(2024, 8, 9)
NORMAL_START = date(2024, 7, 22)
NORMAL_END = date(2024, 7, 31)
EVENT_START = date(2024, 8, 1)
EVENT_END = date(2024, 8, 9)

ECB_URL = (
    "https://data-api.ecb.europa.eu/service/data/EXR/"
    f"D.USD+JPY.EUR.SP00.A?format=csvdata&startPeriod={START_FETCH.isoformat()}&endPeriod={END_FETCH.isoformat()}"
)
FRED_URL = (
    "https://fred.stlouisfed.org/graph/fredgraph.csv"
    f"?id=DGS10&cosd={START_FETCH.isoformat()}&coed={END_FETCH.isoformat()}"
)
MOF_URL = yc.MOF_JGB_HISTORY_URL
CBOE_URL = yc.CBOE_VIX_URL


def parse_ecb_usdjpy() -> dict[date, float]:
    text = yc.decode_csv(yc.fetch_bytes(ECB_URL))
    rows = list(csv.DictReader(StringIO(text)))
    by_currency: dict[str, dict[date, float]] = {"USD": {}, "JPY": {}}
    for row in rows:
        ccy = str(row.get("CURRENCY") or "").strip().upper()
        if ccy not in by_currency:
            continue
        raw_date = row.get("TIME_PERIOD")
        value = yc.clean_float(row.get("OBS_VALUE"))
        if not raw_date or value is None:
            continue
        d = yc.parse_date(str(raw_date))
        by_currency[ccy][d] = value
    common = set(by_currency["USD"]) & set(by_currency["JPY"])
    return {d: by_currency["JPY"][d] / by_currency["USD"][d] for d in common if by_currency["USD"][d] != 0}


def parse_fred_us10y() -> dict[date, float]:
    text = yc.decode_csv(yc.fetch_bytes(FRED_URL))
    rows = list(csv.DictReader(StringIO(text)))
    out: dict[date, float] = {}
    for row in rows:
        raw_date = row.get("DATE") or row.get("observation_date")
        value = yc.clean_float(row.get("DGS10"))
        if not raw_date or value is None:
            continue
        out[yc.parse_date(str(raw_date))] = value
    return out


def parse_mof_jp10y() -> dict[date, float]:
    return yc.parse_mof_jgb_csv(yc.decode_csv(yc.fetch_bytes(MOF_URL)))


def parse_cboe_vix() -> dict[date, float]:
    text = yc.decode_csv(yc.fetch_bytes(CBOE_URL))
    rows = list(csv.DictReader(StringIO(text)))
    out: dict[date, float] = {}
    for row in rows:
        raw_date = row.get("DATE") or row.get("Date") or row.get("date")
        value = yc.clean_float(row.get("CLOSE") or row.get("Close") or row.get("close"))
        if not raw_date or value is None:
            continue
        try:
            out[yc.parse_date(str(raw_date))] = value
        except ValueError:
            continue
    return out


def trim(series: dict[date, float], target: date) -> dict[date, float]:
    return {d: v for d, v in series.items() if d <= target}


def score_day(target: date, fx: dict[date, float], us10: dict[date, float], jp10: dict[date, float], vix: dict[date, float], config: dict) -> dict:
    fx_t = trim(fx, target)
    us_t = trim(us10, target)
    jp_t = trim(jp10, target)
    vix_t = trim(vix, target)

    fx1 = yc.change_over_calendar_days(fx_t, 1)
    fx3 = yc.change_over_calendar_days(fx_t, 3)
    fx7 = yc.change_over_calendar_days(fx_t, 7)

    spread = yc.build_spread_series(us_t, jp_t)
    sp3 = yc.change_over_calendar_days(spread, 3)
    spread_change_bp = (sp3["current"] - sp3["prior"]) * 100.0

    vx1 = yc.change_over_calendar_days(vix_t, 1)

    yen_score = yc.yen_1d_score(fx1["change_pct"]) + yc.yen_3d_score(fx3["change_pct"])
    spread_component = yc.spread_score(spread_change_bp)
    vix_component = yc.vix_score(vx1["change_pct"], vx1["current"])
    raw = yen_score + spread_component + vix_component

    gate = config["gate"]
    gate_applied = yen_score < int(gate["yen_score_required_for_high"])
    final = min(raw, int(gate["max_score_without_yen_trigger"])) if gate_applied else raw
    final = max(0, min(100, int(final)))
    level, label = yc.level_for_score(final, config)

    return {
        "date": target.isoformat(),
        "usd_jpy": round(fx1["current"], 6),
        "yen_1d_pct": round(fx1["change_pct"], 4),
        "yen_3d_pct": round(fx3["change_pct"], 4),
        "yen_7d_pct": round(fx7["change_pct"], 4),
        "spread_pct_point": round(sp3["current"], 4),
        "spread_3d_change_bp": round(spread_change_bp, 2),
        "vix": round(vx1["current"], 4),
        "vix_1d_pct": round(vx1["change_pct"], 4),
        "yen_score": yen_score,
        "spread_score": spread_component,
        "vix_score": vix_component,
        "raw_score": raw,
        "gate_applied": gate_applied,
        "final_score": final,
        "level": level,
        "label_ko": label,
    }


def business_days(start: date, end: date) -> list[date]:
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def main() -> None:
    config = yc.load_config()
    fx = parse_ecb_usdjpy()
    us10 = parse_fred_us10y()
    jp10 = parse_mof_jp10y()
    vix = parse_cboe_vix()

    rows = []
    for d in business_days(NORMAL_START, EVENT_END):
        try:
            rows.append(score_day(d, fx, us10, jp10, vix, config))
        except Exception as exc:
            rows.append({"date": d.isoformat(), "error": f"{type(exc).__name__}:{exc}"})

    normal = [r for r in rows if NORMAL_START.isoformat() <= r.get("date", "") <= NORMAL_END.isoformat() and "final_score" in r]
    event = [r for r in rows if EVENT_START.isoformat() <= r.get("date", "") <= EVENT_END.isoformat() and "final_score" in r]
    peak = max(event, key=lambda r: r["final_score"]) if event else None
    first_caution = next((r for r in event if r["final_score"] >= 45), None)
    first_high = next((r for r in event if r["final_score"] >= 65), None)
    normal_high_days = [r["date"] for r in normal if r["final_score"] >= 65]

    checks = {
        "peak_at_least_65": bool(peak and peak["final_score"] >= 65),
        "high_by_aug05": bool(first_high and first_high["date"] <= "2024-08-05"),
        "no_high_in_normal_window": len(normal_high_days) == 0,
        "all_event_days_scored": len(event) == len(business_days(EVENT_START, EVENT_END)),
    }
    passed = all(checks.values())

    payload = {
        "engine": "YEN_CARRY_RISK_LITE_V1_BACKTEST_AUG2024",
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_engine": config["engine"],
        "score_weight": 0,
        "periods": {
            "normal_comparison": [NORMAL_START.isoformat(), NORMAL_END.isoformat()],
            "event": [EVENT_START.isoformat(), EVENT_END.isoformat()],
        },
        "sources": {"usd_jpy": ECB_URL, "us10y": FRED_URL, "jp10y": MOF_URL, "vix": CBOE_URL},
        "rows": rows,
        "summary": {
            "normal_avg_score": round(sum(r["final_score"] for r in normal) / len(normal), 2) if normal else None,
            "event_avg_score": round(sum(r["final_score"] for r in event) / len(event), 2) if event else None,
            "peak": peak,
            "first_caution": first_caution,
            "first_high": first_high,
            "normal_high_days": normal_high_days,
            "checks": checks,
            "verdict": "PASS" if passed else "REVIEW",
        },
        "rule": "Uses the production V1.0 scoring functions and thresholds. Each target date only sees observations available on or before that date; no future leakage, interpolation, or synthetic backfill.",
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# YEN CARRY RISK LITE V1.0 — 2024-08 BACKTEST",
        "",
        f"Verdict: **{payload['summary']['verdict']}**",
        f"Normal avg: **{payload['summary']['normal_avg_score']}**",
        f"Event avg: **{payload['summary']['event_avg_score']}**",
        f"Peak: **{peak['final_score'] if peak else 'N/A'}** on **{peak['date'] if peak else 'N/A'}**",
        f"First caution >=45: **{first_caution['date'] if first_caution else 'N/A'}**",
        f"First high >=65: **{first_high['date'] if first_high else 'N/A'}**",
        "",
        "| Date | USDJPY | 1D | 3D | 7D | Spread 3D bp | VIX | Score | Level |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        if "final_score" not in r:
            lines.append(f"| {r['date']} | N/A | N/A | N/A | N/A | N/A | N/A | N/A | ERROR |")
            continue
        lines.append(
            f"| {r['date']} | {r['usd_jpy']:.3f} | {r['yen_1d_pct']:.2f}% | {r['yen_3d_pct']:.2f}% | {r['yen_7d_pct']:.2f}% | "
            f"{r['spread_3d_change_bp']:.1f} | {r['vix']:.2f} | {r['final_score']} | {r['label_ko']} |"
        )
    lines += ["", "## Checks", ""]
    for k, v in checks.items():
        lines.append(f"- {k}: {'PASS' if v else 'FAIL'}")
    lines.append("")
    lines.append("Production score_weight remains 0. This backtest does not modify MASTER MARKET, DXY, BTC, whale, derivatives, or the production collector.")
    MD_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(payload["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
