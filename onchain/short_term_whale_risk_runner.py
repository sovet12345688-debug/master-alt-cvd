#!/usr/bin/env python3
"""Robust no-key Checkonchain source adapter for short_term_whale_risk.py.

Keeps the core risk engine small/stable while handling Checkonchain's live-page
HTML handoff to charts-cdn and split Plotly traces used by some STH charts.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

import short_term_whale_risk as core


def fetch_plotly_text(url: str) -> tuple[str, str]:
    raw = core.fetch_text(url)
    if "Plotly.newPlot" in raw:
        return raw, url

    # Checkonchain's public front URL can be a tiny HTML/JS handoff to the CDN.
    # Normalize escaped URLs and find the CDN target regardless of whether it is
    # in href=, JS, meta markup, or plain text. No browser automation needed.
    normalized = raw.replace("\\/", "/").replace("&amp;", "&")
    m = re.search(
        r'https://charts-cdn[.]checkonchain[.]com/[^\s"\'<>]+[.]html',
        normalized,
        flags=re.I,
    )
    if not m:
        head = re.sub(r"\s+", " ", normalized[:500])
        raise ValueError(
            "Plotly.newPlot not found and no Checkonchain CDN target found; "
            f"page_head={head!r}"
        )

    cdn = m.group(0)
    raw2 = core.fetch_text(cdn)
    if "Plotly.newPlot" not in raw2:
        raise ValueError(f"CDN page has no Plotly.newPlot: {cdn}")
    return raw2, cdn


def trace_pairs(t: dict[str, Any]) -> list[tuple[datetime, float]]:
    x = core.decode_array(t.get("x"))
    if not x and isinstance(t.get("x"), list):
        x = t["x"]
    y = core.decode_array(t.get("y"))
    if not y and isinstance(t.get("y"), list):
        y = t["y"]

    out: list[tuple[datetime, float]] = []
    for i, v in enumerate(y):
        if not isinstance(v, (int, float)) or isinstance(v, bool) or i >= len(x):
            continue
        v = float(v)
        if not math.isfinite(v):
            continue
        d = core.parse_dt(x[i])
        if d is not None:
            out.append((d, v))
    return out


def merge_named_traces(
    ts: list[dict[str, Any]], logical_name: str, accepted_names: list[str]
) -> dict[str, Any]:
    accepted = {x.lower() for x in accepted_names}
    chosen = [
        t for t in ts
        if str(t.get("name") or "").strip().lower() in accepted
    ]
    if not chosen:
        names = [str(t.get("name")) for t in ts if t.get("name")][:30]
        raise ValueError(f"split traces not found; wanted={accepted_names}; available={names}")

    by_time: dict[datetime, float] = {}
    for t in chosen:
        for d, v in trace_pairs(t):
            by_time[d] = v
    if not by_time:
        raise ValueError(f"no dated values in split traces={accepted_names}")

    ordered = sorted(by_time.items())
    return {
        "name": logical_name,
        "x": [core.iso(d) for d, _ in ordered],
        "y": [v for _, v in ordered],
    }


def select_metric_trace(ts: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    try:
        return core.find_trace(ts, core.TRACE_NAMES[kind])
    except ValueError:
        pass

    if kind == "mvrv":
        return merge_named_traces(
            ts,
            "STH-MVRV",
            ["STH-MVRV (in Profit)", "STH-MVRV (in Loss)"],
        )
    if kind == "sopr":
        return merge_named_traces(
            ts,
            "STH-SOPR",
            ["STH-SOPR > 1", "STH-SOPR < 1"],
        )

    names = [str(t.get("name")) for t in ts if t.get("name")][:30]
    raise ValueError(f"no usable {kind} STH trace; available={names}")


def sth_metric(kind: str) -> dict[str, Any]:
    errors: list[str] = []
    for url, source_label in core.SOURCES[kind]:
        try:
            raw, effective_url = fetch_plotly_text(url)
            ts = core.plotly_traces(raw)
            t = select_metric_trace(ts, kind)
            p = core.trace_stats(t)
            p.update(
                {
                    "source": f"{source_label} public static Plotly HTML (no API key)",
                    "source_url": effective_url,
                    "entry_url": url,
                }
            )
            return p
        except Exception as e:
            errors.append(f"{source_label} {url}: {type(e).__name__}: {e}")

    return {
        "status": "N/A",
        "value": None,
        "percentile_4y": None,
        "avg_7d": None,
        "source": "Checkonchain public Plotly HTML (live/CDN first; fresh GitHub fallback)",
        "source_url": None,
        "error": " | ".join(errors)[:4000],
    }


def main() -> None:
    core.sth_metric = sth_metric
    core.main()


if __name__ == "__main__":
    main()
