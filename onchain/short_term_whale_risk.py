#!/usr/bin/env python3
"""MASTER MARKET free/no-key short-term whale profit-taking risk proxy.

NOT the exact CryptoQuant STH Whale Unrealized P&L series.
Uses public Checkonchain STH cohort metrics + existing Hyperliquid whale NET
+ existing Bitget BTC CVD. No backfill/interpolation/OCR/guessing.
"""
from __future__ import annotations

import base64
import csv
import json
import math
import statistics
import struct
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "onchain/output/latest_short_term_whale_risk.json"
HISTORY = ROOT / "onchain/data/short_term_whale_risk_history.csv"
ACTOR = ROOT / "market_vault/output/latest_actor_flows.json"
MICRO = ROOT / "derivatives/output/latest_microstructure.json"
ENGINE = "MASTER_ST_WHALE_PROFIT_TAKING_RISK_PROXY_V1"
SCHEMA = "1.0"
BASE = "https://raw.githubusercontent.com/checkmatey/checkonchain.com/main"
URLS = {
    "nupl": f"{BASE}/btconchain/unrealised/nupl_bycohort/nupl_bycohort_light.html",
    "mvrv": f"{BASE}/btconchain/unrealised/mvrv_sth/mvrv_sth_light.html",
    "sopr": f"{BASE}/btconchain/realised/sthsopr_indicator/sthsopr_indicator_light.html",
}
POINTS = {"🔴": 1.0, "🟡": 0.5, "🟢": 0.0}


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def parse_dt(v: Any) -> datetime | None:
    if not isinstance(v, str): return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


def age_hours(v: str | None) -> float | None:
    d = parse_dt(v)
    return None if d is None else (now() - d).total_seconds() / 3600


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "MASTER-MARKET-free-proxy/1.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def extract_json_array(text: str, start: int) -> str:
    depth = 0; quoted = False; esc = False
    for i in range(start, len(text)):
        c = text[i]
        if quoted:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == '"': quoted = False
            continue
        if c == '"': quoted = True
        elif c == "[": depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0: return text[start:i+1]
    raise ValueError("unterminated Plotly trace array")


def traces(url: str) -> list[dict[str, Any]]:
    text = fetch(url)
    p = text.find("Plotly.newPlot")
    if p < 0: raise ValueError("Plotly.newPlot not found")
    s = text.find("[", p)
    if s < 0: raise ValueError("Plotly data array not found")
    data = json.loads(extract_json_array(text, s))
    return [x for x in data if isinstance(x, dict)]


def decode_array(v: Any) -> list[Any]:
    if isinstance(v, list): return v
    if not isinstance(v, dict) or "bdata" not in v: return []
    raw = base64.b64decode(v["bdata"])
    dtype = str(v.get("dtype") or "f8")
    endian = "<"
    if dtype and dtype[0] in "<>":
        endian, dtype = dtype[0], dtype[1:]
    elif dtype and dtype[0] in "=|":
        dtype = dtype[1:]
    fmts = {"f8":"d","f4":"f","i8":"q","i4":"i","i2":"h","i1":"b","u8":"Q","u4":"I","u2":"H","u1":"B"}
    fmt = fmts.get(dtype)
    if not fmt: raise ValueError(f"unsupported Plotly dtype={dtype}")
    size = struct.calcsize(fmt)
    if len(raw) % size: raise ValueError("typed-array byte length mismatch")
    return [x[0] for x in struct.iter_unpack(endian + fmt, raw)]


def find_trace(ts: list[dict[str, Any]], exact: str) -> dict[str, Any]:
    exact = exact.lower()
    for t in ts:
        if str(t.get("name") or "").strip().lower() == exact:
            return t
    raise ValueError(f"trace {exact!r} not found; names={[t.get('name') for t in ts[:20]]}")


def merge_sopr(ts: list[dict[str, Any]]) -> dict[str, Any]:
    parts = [t for t in ts if str(t.get("name") or "").strip().lower().startswith("sth-sopr")]
    if not parts: raise ValueError("STH-SOPR traces not found")
    x = decode_array(parts[0].get("x")) or parts[0].get("x") or []
    ys = [decode_array(t.get("y")) for t in parts]
    n = max([len(x)] + [len(y) for y in ys])
    merged: list[float | None] = []
    for i in range(n):
        val = None
        for y in ys:
            if i >= len(y): continue
            v = y[i]
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
                val = float(v); break
        merged.append(val)
    return {"name": "STH-SOPR", "x": x, "y": merged}


def stats(t: dict[str, Any]) -> dict[str, Any]:
    x = decode_array(t.get("x")) or (t.get("x") if isinstance(t.get("x"), list) else [])
    y = decode_array(t.get("y")) or (t.get("y") if isinstance(t.get("y"), list) else [])
    pairs: list[tuple[datetime | None, float]] = []
    for i, v in enumerate(y):
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(float(v)): continue
        pairs.append((parse_dt(x[i]) if i < len(x) else None, float(v)))
    if not pairs: raise ValueError(f"no numeric values in {t.get('name')}")
    latest_dt, value = pairs[-1]
    if latest_dt:
        hist = [v for d,v in pairs if d and d >= latest_dt - timedelta(days=1461)]
        last7 = [v for d,v in pairs if d and d >= latest_dt - timedelta(days=7)]
    else:
        hist = [v for _,v in pairs[-1460:]]; last7 = [v for _,v in pairs[-7:]]
    if len(hist) < 30: hist = [v for _,v in pairs]
    pct = 100 * sum(v <= value for v in hist) / len(hist)
    return {"status":"OK","trace_name":t.get("name"),"source_time_utc":iso(latest_dt),"value":value,"percentile_4y":pct,"avg_7d":statistics.fmean(last7) if last7 else None,"history_points_4y":len(hist)}


def sth_metric(kind: str) -> dict[str, Any]:
    url = URLS[kind]
    try:
        ts = traces(url)
        t = merge_sopr(ts) if kind == "sopr" else find_trace(ts, "STH-NUPL" if kind == "nupl" else "STH-MVRV")
        p = stats(t); p.update({"source":"Checkonchain public static Plotly HTML","source_url":url}); return p
    except Exception as e:
        return {"status":"N/A","value":None,"percentile_4y":None,"avg_7d":None,"source":"Checkonchain public static Plotly HTML","source_url":url,"error":f"{type(e).__name__}: {e}"}


def read_json(path: Path) -> dict[str, Any]:
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e: return {"_error":f"{type(e).__name__}: {e}"}


def whale() -> dict[str, Any]:
    try:
        c = read_json(ACTOR)["whale"]["BTC"]["current"]
        net, longv, shortv = float(c["net_usd"]), float(c["long_usd"]), float(c["short_usd"])
        ts = c.get("snapshot_time_utc"); age = age_hours(ts)
        if age is None or age > 6: raise ValueError(f"stale actor flow age={age}")
        gross = longv + shortv
        return {"status":"OK","value":net,"net_ratio":net/gross if gross else None,"long_usd":longv,"short_usd":shortv,"source_time_utc":ts,"source":"market_vault/output/latest_actor_flows.json"}
    except Exception as e:
        return {"status":"N/A","value":None,"error":f"{type(e).__name__}: {e}"}


def cvd() -> dict[str, Any]:
    try:
        p = read_json(MICRO)
        if p.get("engine") != "MASTER_DERIVATIVES_MICROSTRUCTURE_V2": raise ValueError("wrong derivatives engine")
        a = next(x for x in p["assets"] if x.get("symbol") == "BTCUSDT")
        value, total = float(a["cvd_notional_usdt"]), float(a["trade_notional_1h_usdt"])
        ts = a.get("window_end_utc") or p.get("generated_at_utc"); age = age_hours(ts)
        if age is None or age > 4: raise ValueError(f"stale CVD age={age}")
        return {"status":"OK","value":value,"cvd_to_volume_ratio":value/total if total else None,"trade_notional_1h_usdt":total,"source_time_utc":ts,"source":"derivatives/output/latest_microstructure.json"}
    except Exception as e:
        return {"status":"N/A","value":None,"error":f"{type(e).__name__}: {e}"}


def sig_unreal(m: dict[str, Any]) -> tuple[str,str]:
    if m.get("status") != "OK": return "⚪","N/A"
    v,p = float(m["value"]),float(m["percentile_4y"])
    if v > 0 and p >= 90: return "🔴","미실현 수익 과열권"
    if v > 0 and p >= 75: return "🟡","미실현 수익 상단"
    return "🟢","차익실현 잠재압력 낮음"


def sig_mvrv(m: dict[str, Any]) -> tuple[str,str]:
    if m.get("status") != "OK": return "⚪","N/A"
    v,p = float(m["value"]),float(m["percentile_4y"])
    if v > 1 and p >= 90: return "🔴","단기보유자 수익 과열"
    if v > 1 and p >= 75: return "🟡","단기보유자 수익권 상단"
    if v > 1: return "🟡","단기보유자 수익권"
    return "🟢","손익분기 이하/과열 아님"


def sig_sopr(m: dict[str, Any]) -> tuple[str,str]:
    if m.get("status") != "OK": return "⚪","N/A"
    v,p,a = float(m["value"]),float(m["percentile_4y"]),m.get("avg_7d")
    if v > 1 and a is not None and float(a) > 1 and p >= 75: return "🔴","실제 이익실현 압력 확인"
    if v > 1: return "🟡","이익실현 중"
    return "🟢","이익실현 압력 낮음"


def sig_whale(m: dict[str, Any]) -> tuple[str,str]:
    if m.get("status") != "OK" or m.get("net_ratio") is None: return "⚪","N/A"
    r=float(m["net_ratio"])
    if r <= -0.10: return "🔴","고래 순포지션 숏 우세"
    if r >= 0.10: return "🟢","고래 순포지션 롱 우세"
    return "🟡","고래 롱·숏 혼조"


def sig_cvd(m: dict[str, Any]) -> tuple[str,str]:
    if m.get("status") != "OK" or m.get("cvd_to_volume_ratio") is None: return "⚪","N/A"
    r=float(m["cvd_to_volume_ratio"])
    if r <= -0.05: return "🔴","적극매도 우세"
    if r >= 0.05: return "🟢","적극매수 우세"
    return "🟡","매수·매도 혼조"


def num(v: Any, n: int=3) -> str: return "N/A" if v is None else f"{float(v):.{n}f}"

def money(v: Any) -> str:
    if v is None: return "N/A"
    x=float(v); sign="-" if x<0 else "+"; x=abs(x)
    if x>=1e9: return f"{sign}${x/1e9:.2f}B"
    if x>=1e6: return f"{sign}${x/1e6:.1f}M"
    return f"{sign}${x:,.0f}"


def row(name: str, m: dict[str, Any], fn, display: str) -> dict[str, Any]:
    s,state=fn(m); return {"signal":s,"indicator":name,"display_value":display if s!="⚪" else "N/A","state":state,"raw":m}


def build() -> dict[str, Any]:
    u,m,s,w,c = sth_metric("nupl"),sth_metric("mvrv"),sth_metric("sopr"),whale(),cvd()
    rows = [
        row("STH 미실현 수익상태 (Whale 대체)",u,sig_unreal,f"{num(u.get('value'))} / 4Y {num(u.get('percentile_4y'),1)}%ile"),
        row("STH-MVRV",m,sig_mvrv,f"{num(m.get('value'))} / 4Y {num(m.get('percentile_4y'),1)}%ile"),
        row("STH-SOPR",s,sig_sopr,f"{num(s.get('value'))} / 7D평균 {num(s.get('avg_7d'))}"),
        row("Hyperliquid 고래 NET",w,sig_whale,money(w.get("value"))),
        row("BTC CVD",c,sig_cvd,money(c.get("value"))),
    ]
    avail=[r for r in rows if r["signal"] in POINTS]; pts=sum(POINTS[r["signal"]] for r in avail); intensity=pts/len(avail) if avail else None
    if len(avail)<3 or intensity is None: osig,level="⚪","확인 제한"
    elif intensity>=0.80: osig,level="🔴","매우 높음"
    elif intensity>=0.60: osig,level="🔴","높음"
    elif intensity>=0.35: osig,level="🟡","중간"
    else: osig,level="🟢","낮음"
    return {
        "engine":ENGINE,"schema_version":SCHEMA,"generated_at_utc":iso(now()),"name_ko":"단기 고래 차익실현 위험","role":"AUXILIARY_RISK_ONLY","score_weight":0,"exact_cryptoquant_sth_whale":False,
        "proxy_note":"Exact CryptoQuant STH Whale P&L is not reproduced. STH cohort profit-state proxies + current whale futures NET + BTC CVD are combined.",
        "rows":rows,"overall":{"signal":osig,"level":level,"risk_points":round(pts,2),"available_rows":len(avail),"total_rows":5,"coverage":round(len(avail)/5,3),"method":"red=1, yellow=0.5, green=0; equal-weight auxiliary. <3 available rows => 확인 제한."},
        "guards":{"no_api_key":True,"no_paid_source":True,"no_backfill":True,"no_interpolation":True,"no_guessing":True,"cannot_alone_change":["Market Positive Score","BTC Liquidity Lead","Crypto Money Inflow","ALT Money Inflow","final direction","Risk Veto","WATCH"]},
        "failures":[{"indicator":r["indicator"],"error":r["raw"].get("error")} for r in rows if r["signal"]=="⚪"]
    }


def append_history(p: dict[str, Any]) -> None:
    HISTORY.parent.mkdir(parents=True,exist_ok=True)
    fields=["generated_at_utc","risk_level","risk_signal","risk_points","coverage","sth_unrealized_value","sth_mvrv","sth_sopr","whale_net_usd","btc_cvd_usdt"]
    r={x["indicator"]:x for x in p["rows"]}
    rec={"generated_at_utc":p["generated_at_utc"],"risk_level":p["overall"]["level"],"risk_signal":p["overall"]["signal"],"risk_points":p["overall"]["risk_points"],"coverage":p["overall"]["coverage"],"sth_unrealized_value":r["STH 미실현 수익상태 (Whale 대체)"]["raw"].get("value"),"sth_mvrv":r["STH-MVRV"]["raw"].get("value"),"sth_sopr":r["STH-SOPR"]["raw"].get("value"),"whale_net_usd":r["Hyperliquid 고래 NET"]["raw"].get("value"),"btc_cvd_usdt":r["BTC CVD"]["raw"].get("value")}
    exists=HISTORY.exists() and HISTORY.stat().st_size>0
    with HISTORY.open("a",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        if not exists: w.writeheader()
        w.writerow(rec)


def main() -> None:
    p=build(); OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(p,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); append_history(p); print(json.dumps(p,ensure_ascii=False,indent=2))


if __name__ == "__main__": main()
