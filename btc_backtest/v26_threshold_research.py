from __future__ import annotations
import io, json, math, zipfile
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

START = pd.Timestamp("2023-09-01T00:00:00Z")
SPLIT = pd.Timestamp("2025-03-01T00:00:00Z")
END = pd.Timestamp("2026-09-01T00:00:00Z")
OUT = Path("btc_backtest/output/v26_threshold_research")
OUT.mkdir(parents=True, exist_ok=True)
URL = "https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-{ym}.zip"
COLS = ["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]

def months(start, end):
    cur = pd.Timestamp(start.year, start.month, 1, tz="UTC")
    last = pd.Timestamp(end.year, end.month, 1, tz="UTC")
    while cur < last:
        yield cur.strftime("%Y-%m")
        cur += pd.offsets.MonthBegin(1)

def load():
    frames, failures = [], []
    s = requests.Session()
    s.headers["User-Agent"] = "master-btc-trend-v26-threshold-research/1.0"
    for ym in months(START, END):
        try:
            r = s.get(URL.format(ym=ym), timeout=60)
            if r.status_code != 200:
                failures.append(f"{ym}:HTTP{r.status_code}")
                continue
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                names = [n for n in z.namelist() if n.endswith(".csv")]
                if not names:
                    failures.append(f"{ym}:no_csv")
                    continue
                q = pd.read_csv(z.open(names[0]), header=None, names=COLS)
            ts = pd.to_numeric(q.open_time, errors="coerce")
            ts = np.where(ts > 1e14, ts / 1000.0, ts)
            q["time"] = pd.to_datetime(ts, unit="ms", utc=True, errors="coerce")
            for c in ["open","high","low","close","volume","quote_volume","taker_buy_quote"]:
                q[c] = pd.to_numeric(q[c], errors="coerce")
            frames.append(q[["time","open","high","low","close","volume","quote_volume","taker_buy_quote"]])
        except Exception as exc:
            failures.append(f"{ym}:{type(exc).__name__}")
    if not frames:
        raise RuntimeError("No historical BTC data downloaded")
    h = pd.concat(frames, ignore_index=True).dropna().drop_duplicates("time").sort_values("time")
    h = h[(h.time >= START) & (h.time < END)].set_index("time")
    return h, failures

def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0)
    dn = -d.clip(upper=0)
    au = up.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    ad = dn.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    return (100 - 100/(1 + au/ad.replace(0, np.nan))).fillna(50)

def atr(df, n=14):
    pc = df.close.shift(1)
    tr = pd.concat([(df.high-df.low).abs(), (df.high-pc).abs(), (df.low-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False, min_periods=n).mean()

def enrich_hourly(h):
    x = h.copy()
    for n in [1,3,6,12,24]:
        x[f"ret{n}"] = x.close.pct_change(n)
    x["rsi14"] = rsi(x.close)
    x["ema20"] = x.close.ewm(span=20, adjust=False).mean()
    x["atr14"] = atr(x)
    vm = x.volume.rolling(20).mean()
    vs = x.volume.rolling(20).std(ddof=0)
    x["vol_z"] = (x.volume-vm)/vs.replace(0,np.nan)
    rg = (x.high-x.low).replace(0,np.nan)
    x["lower_wick_ratio"] = (np.minimum(x.open,x.close)-x.low)/rg
    x["upper_wick_ratio"] = (x.high-np.maximum(x.open,x.close))/rg
    x["net_taker_quote"] = 2*x.taker_buy_quote-x.quote_volume
    x["taker_imb6"] = x.net_taker_quote.rolling(6).sum()/x.quote_volume.rolling(6).sum().replace(0,np.nan)
    return x

def daily_frame(h):
    d = pd.DataFrame({
        "open": h.open.resample("1D", label="right", closed="right").first(),
        "high": h.high.resample("1D", label="right", closed="right").max(),
        "low": h.low.resample("1D", label="right", closed="right").min(),
        "close": h.close.resample("1D", label="right", closed="right").last(),
        "volume": h.volume.resample("1D", label="right", closed="right").sum(),
        "quote_volume": h.quote_volume.resample("1D", label="right", closed="right").sum(),
        "net_taker_quote": h.net_taker_quote.resample("1D", label="right", closed="right").sum(),
    }).dropna()
    d["rsi14"] = rsi(d.close)
    d["ema20"] = d.close.ewm(span=20, adjust=False).mean()
    d["ma50"] = d.close.rolling(50).mean()
    d["ma200"] = d.close.rolling(200).mean()
    d["atr14"] = atr(d)
    d["ema20_slope5_atr"] = (d.ema20-d.ema20.shift(5))/(d.atr14.replace(0,np.nan)*5)
    d["ext_atr"] = (d.close-d.ema20)/d.atr14.replace(0,np.nan)
    d["ret1d"] = d.close.pct_change(1)
    d["ret3d"] = d.close.pct_change(3)
    d["ret7d"] = d.close.pct_change(7)
    d["high30"] = d.high.shift(1).rolling(30).max()
    d["low30"] = d.low.shift(1).rolling(30).min()
    d["drawdown30"] = d.close/d.high30-1
    d["drawup30"] = d.close/d.low30-1
    d["range20"] = d.high.rolling(20).max()/d.low.rolling(20).min()-1
    d["low20"] = d.low.rolling(20).min()
    d["prior_low20"] = d.low.shift(20).rolling(20).min()
    d["higher_low"] = d.low20 > d.prior_low20
    d["vol_lead_5v20"] = d.volume.rolling(5).mean()/d.volume.rolling(20).mean()
    d["taker7"] = d.net_taker_quote.rolling(7).sum()/d.quote_volume.rolling(7).sum().replace(0,np.nan)
    return d

def join_intraday_features(h, d):
    roll = pd.DataFrame(index=d.index)
    roll["min_rsi24"] = h.rsi14.rolling(24).min().resample("1D", label="right", closed="right").last()
    roll["max_rsi24"] = h.rsi14.rolling(24).max().resample("1D", label="right", closed="right").last()
    roll["min_ret12_24"] = h.ret12.rolling(24).min().resample("1D", label="right", closed="right").last()
    roll["max_ret12_24"] = h.ret12.rolling(24).max().resample("1D", label="right", closed="right").last()
    roll["max_volz24"] = h.vol_z.rolling(24).max().resample("1D", label="right", closed="right").last()
    roll["min_taker6_24"] = h.taker_imb6.rolling(24).min().resample("1D", label="right", closed="right").last()
    roll["max_taker6_24"] = h.taker_imb6.rolling(24).max().resample("1D", label="right", closed="right").last()
    roll["max_lwick24"] = h.lower_wick_ratio.rolling(24).max().resample("1D", label="right", closed="right").last()
    roll["max_uwick24"] = h.upper_wick_ratio.rolling(24).max().resample("1D", label="right", closed="right").last()
    return d.join(roll, how="left")

def add_forward_outcomes(d):
    x = d.copy()
    closes = x.close.to_numpy(float)
    highs = x.high.to_numpy(float)
    lows = x.low.to_numpy(float)
    n = len(x)
    for horizon in [7,30]:
        ret = np.full(n, np.nan)
        mfe = np.full(n, np.nan)
        mae = np.full(n, np.nan)
        for i in range(n):
            j = min(n, i+horizon+1)
            if i+1 >= j or j-i-1 < min(horizon, 3):
                continue
            fut_c = closes[i+1:j]
            fut_h = highs[i+1:j]
            fut_l = lows[i+1:j]
            ret[i] = fut_c[-1]/closes[i]-1
            mfe[i] = np.nanmax(fut_h)/closes[i]-1
            mae[i] = np.nanmin(fut_l)/closes[i]-1
        x[f"fwd{horizon}d_ret"] = ret
        x[f"fwd{horizon}d_mfe"] = mfe
        x[f"fwd{horizon}d_mae"] = mae
    return x

def thin(mask, idx, gap_days=3):
    out = np.zeros(len(mask), dtype=bool)
    last = None
    for i, (m, t) in enumerate(zip(mask, idx)):
        if not bool(m):
            continue
        if last is None or (t-last).days >= gap_days:
            out[i] = True
            last = t
    return out

def stats(df, mask, direction):
    m = thin(mask.fillna(False).to_numpy(), df.index, 3)
    z = df.loc[m].dropna(subset=["fwd30d_ret","fwd30d_mfe","fwd30d_mae"])
    if z.empty:
        return {"n":0}
    r = z.fwd30d_ret
    if direction == "BOTTOM":
        favorable = r
        mfe = z.fwd30d_mfe
        adverse = z.fwd30d_mae
    elif direction == "TOP":
        favorable = -r
        mfe = -z.fwd30d_mae
        adverse = -z.fwd30d_mfe
    else:
        favorable = r
        mfe = z.fwd30d_mfe
        adverse = z.fwd30d_mae
    return {
        "n": int(len(z)),
        "median_7d_ret_pct": round(float(z.fwd7d_ret.median()*100), 3),
        "median_30d_ret_pct": round(float(r.median()*100), 3),
        "mean_30d_ret_pct": round(float(r.mean()*100), 3),
        "median_favorable_30d_pct": round(float(favorable.median()*100), 3),
        "median_mfe_30d_pct": round(float(mfe.median()*100), 3),
        "median_adverse_30d_pct": round(float(adverse.median()*100), 3),
        "positive_30d_rate_pct": round(float((r>0).mean()*100), 1),
    }

def evaluate_rules(df, rules):
    out = []
    train = df[df.index < SPLIT]
    oos = df[df.index >= SPLIT]
    for name, direction, fn, origin in rules:
        tr = stats(train, fn(train), direction)
        oo = stats(oos, fn(oos), direction)
        status = "LOW_SAMPLE"
        if tr.get("n",0) >= 20 and oo.get("n",0) >= 20:
            a = tr.get("median_favorable_30d_pct")
            b = oo.get("median_favorable_30d_pct")
            if a is not None and b is not None:
                status = "DIRECTIONALLY_STABLE" if a > 0 and b > 0 else "SIGN_OR_EDGE_FAIL"
        out.append({"rule":name,"direction":direction,"origin":origin,"train":tr,"oos":oo,"research_status":status})
    return out

def build_rules(df):
    rules = []
    def add(name, direction, fn, origin="V2.5_SEED"):
        rules.append((name,direction,fn,origin))

    for th in [30,35,40,42]:
        add(f"BOTTOM_RSI_MIN24_LE_{th}", "BOTTOM", lambda x, th=th: x.min_rsi24 <= th)
        add(f"TOP_RSI_MAX24_GE_{100-th}", "TOP", lambda x, th=th: x.max_rsi24 >= 100-th, "MIRROR_OF_V2.5_SEED")
    for th in [-0.02,-0.03,-0.04,-0.05]:
        p = int(abs(th)*100)
        add(f"BOTTOM_RET12_MIN24_LE_{p}PCT", "BOTTOM", lambda x, th=th: x.min_ret12_24 <= th)
        add(f"TOP_RET12_MAX24_GE_{p}PCT", "TOP", lambda x, th=-th: x.max_ret12_24 >= th, "MIRROR_OF_V2.5_SEED")
    for th in [0.8,1.0,1.5,2.0]:
        add(f"BOTTOM_VOLZ_MAX24_GE_{str(th).replace('.','_')}", "BOTTOM", lambda x, th=th: x.max_volz24 >= th)
        add(f"TOP_VOLZ_MAX24_GE_{str(th).replace('.','_')}", "TOP", lambda x, th=th: x.max_volz24 >= th, "SYMMETRIC_CONTEXT")
    for th in [-0.04,-0.06,-0.08,-0.10]:
        v = int(abs(th)*100)
        add(f"BOTTOM_TAKER6_MIN24_LE_{v}PCT", "BOTTOM", lambda x, th=th: x.min_taker6_24 <= th)
        add(f"TOP_TAKER6_MAX24_GE_{v}PCT", "TOP", lambda x, th=-th: x.max_taker6_24 >= th, "MIRROR_OF_V2.5_SEED")
    for th in [0.25,0.30,0.45,0.50]:
        v = int(th*100)
        add(f"BOTTOM_LWICK_MAX24_GE_{v}PCT", "BOTTOM", lambda x, th=th: x.max_lwick24 >= th)
        add(f"TOP_UWICK_MAX24_GE_{v}PCT", "TOP", lambda x, th=th: x.max_uwick24 >= th, "MIRROR_OF_V2.5_SEED")
    for th in [-1.0,-1.5,-2.0,-2.5]:
        v = str(abs(th)).replace(".","_")
        add(f"BOTTOM_DAILY_EXT_ATR_LE_MINUS_{v}", "BOTTOM", lambda x, th=th: x.ext_atr <= th, "V2.5_TIME_VALIDITY_SEED")
        add(f"TOP_DAILY_EXT_ATR_GE_PLUS_{v}", "TOP", lambda x, th=-th: x.ext_atr >= th, "MIRROR_OF_V2.5_TIME_VALIDITY")

    def bflags(x):
        return pd.concat([
            (x.min_rsi24<=35).rename("rsi"),
            (x.min_ret12_24<=-0.03).rename("ret"),
            (x.max_volz24>=1.5).rename("vol"),
            (x.min_taker6_24<=-0.08).rename("tak"),
            (x.max_lwick24>=0.45).rename("wick")
        ], axis=1).sum(axis=1)
    def tflags(x):
        return pd.concat([
            (x.max_rsi24>=65).rename("rsi"),
            (x.max_ret12_24>=0.03).rename("ret"),
            (x.max_volz24>=1.5).rename("vol"),
            (x.max_taker6_24>=0.08).rename("tak"),
            (x.max_uwick24>=0.45).rename("wick")
        ], axis=1).sum(axis=1)
    for k in [2,3,4]:
        add(f"BOTTOM_V25_FEAR_FLAGS_GE_{k}", "BOTTOM", lambda x,k=k: bflags(x)>=k, "V2.5_COMPOSITE_SEED")
        add(f"TOP_MIRROR_FLAGS_GE_{k}", "TOP", lambda x,k=k: tflags(x)>=k, "MIRROR_RESEARCH_ONLY")

    train = df[df.index < SPLIT]
    q20 = float(train.range20.quantile(.20))
    q30 = float(train.range20.quantile(.30))
    for qname, qv in [("Q20",q20),("Q30",q30)]:
        for vol_th in [1.0,1.1,1.2]:
            nm = str(vol_th).replace(".","_")
            add(
                f"ACCUM_COMPRESSION_{qname}_HL_VOLLEAD_GE_{nm}",
                "ACCUM",
                lambda x,qv=qv,vol_th=vol_th: (x.range20<=qv)&x.higher_low&(x.vol_lead_5v20>=vol_th)&(x.taker7>0)&(x.ext_atr.abs()<=1.5),
                "TRAIN_QUANTILE_PLUS_V2.6_STRUCTURE"
            )
    return rules, {"train_range20_q20":q20,"train_range20_q30":q30}

def summarize(results):
    groups={}
    for d in ["BOTTOM","TOP","ACCUM"]:
        z=[r for r in results if r["direction"]==d]
        z=sorted(z,key=lambda r:(r["oos"].get("median_favorable_30d_pct",-999),r["oos"].get("n",0)),reverse=True)
        groups[d]=z[:10]
    return groups

def main():
    h, failures = load()
    h = enrich_hourly(h)
    d = daily_frame(h)
    d = join_intraday_features(h,d)
    d = add_forward_outcomes(d).dropna(subset=["ma50","atr14","range20"])
    rules, learned = build_rules(d)
    res = evaluate_rules(d,rules)
    top = summarize(res)

    out = {
        "schema":"MASTER_BTC_TREND_V2.6_THRESHOLD_RESEARCH_V1",
        "generated_utc":datetime.now(timezone.utc).isoformat(),
        "scope":"RESEARCH_ONLY_DO_NOT_USE_FOR_PRODUCTION_SCORE",
        "data":{
            "source":"Binance Vision USD-M Futures BTCUSDT 1h monthly klines",
            "start":str(h.index.min()),
            "end":str(h.index.max()),
            "hourly_rows":int(len(h)),
            "daily_rows":int(len(d)),
            "split_oos":str(SPLIT),
            "download_failures":failures,
        },
        "methodology":{
            "point_in_time":True,
            "completed_daily_checkpoint":True,
            "event_thinning_days":3,
            "future_horizons_days":[7,30],
            "production_promotion":False,
            "note":"Candidate raw thresholds are V2.5 seeds or train-quantile research; this run does not change V2.6 production.",
        },
        "learned_train_only":learned,
        "results":res,
        "top_oos_by_family":top,
    }
    (OUT/"summary.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")

    md=[
        "# MASTER BTC TREND V2.6 Threshold Research V1",
        "",
        "**RESEARCH ONLY — no production score/threshold change.**",
        "",
        f"- Hourly rows: {len(h):,}",
        f"- Daily checkpoints: {len(d):,}",
        f"- OOS split: {SPLIT}",
        f"- Download failures: {len(failures)}",
        "",
        "## Top OOS candidates",
        "",
    ]
    for fam in ["BOTTOM","TOP","ACCUM"]:
        md += [f"### {fam}", "", "| Rule | OOS n | OOS favorable 30D median | OOS 30D return median | Train/OOS status |",
               "|---|---:|---:|---:|---|"]
        for r in top[fam]:
            oo=r["oos"]
            md.append(f"| {r['rule']} | {oo.get('n',0)} | {oo.get('median_favorable_30d_pct','N/A')}% | {oo.get('median_30d_ret_pct','N/A')}% | {r['research_status']} |")
        md.append("")
    md += [
        "## Production lock rule",
        "",
        "- This research does not auto-promote any threshold.",
        "- Final production lock requires explicit review of sample size, train/OOS sign stability, overlap, and economic meaning.",
        "- Existing V2.6 weights, caps, independence, N/A handling, and no-fabrication rules remain unchanged.",
    ]
    (OUT/"report.md").write_text("\n".join(md)+"\n",encoding="utf-8")
    print(json.dumps({"status":"OK","summary":str(OUT/"summary.json"),"report":str(OUT/"report.md"),"failures":failures},ensure_ascii=False))

if __name__ == "__main__":
    main()
