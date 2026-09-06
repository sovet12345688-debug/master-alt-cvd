#!/usr/bin/env python3
import argparse, csv, json, math, re, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import requests

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config.json"
DATA_DIR = BASE / "data"
OUT_DIR = BASE / "output"
STATE_DIR = BASE / "state"
HISTORY_CSV = DATA_DIR / "hourly_top10.csv"
LATEST_JSON = OUT_DIR / "latest_summary.json"
STATE_JSON = STATE_DIR / "collector_state.json"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
UA = "MASTER-MARKET-Polymarket-Collector/1.0"

DEFAULT_CONFIG = {
    "top_n": 10, "preselect_n": 35, "gamma_scan_limit": 500,
    "timeout_seconds": 15, "min_total_volume": 50000, "min_liquidity": 10000,
    "theme_caps": {"FED_INFLATION":3,"GEO_OIL":2,"CRYPTO_PRICE":3,"US_MACRO":2,"CRYPTO_POLICY":2},
    "family_cap_default":2, "family_cap_crypto_price":3,
    "watch_move_pp_4h":10.0, "watch_move_pp_1d":15.0,
    "history_hours":[1,4,24,168]
}
THEMES = {
    "FED_INFLATION":["federal reserve","fed ","fomc","interest rate","rate hike","rate cut","rates","cpi","inflation","pce","payroll","nonfarm","nfp","unemployment","jobs report"],
    "GEO_OIL":["iran","israel","hormuz","oil","crude","brent","wti","middle east","ceasefire","war","missile","sanction","opec"],
    "CRYPTO_PRICE":["bitcoin"," btc","btc ","ethereum"," eth","eth ","solana","crypto market cap","bitcoin price","ethereum price"],
    "US_MACRO":["recession","gdp","treasury","bond yield","yield curve","government shutdown","debt ceiling","default","s&p 500","nasdaq","stock market"],
    "CRYPTO_POLICY":["bitcoin etf","ethereum etf","crypto etf","sec ","stablecoin","crypto regulation","crypto bill","digital asset","cftc","coinbase"]
}
EXCLUDE = ["nba","nfl","mlb","nhl","soccer","champions league","premier league","ufc","oscars","grammy","box office","movie","album","reality tv","celebrity","election winner","presidential election","governor election","senate election"]
HIGH_IMPACT = {"rate hike":18,"rate cut":16,"fomc":14,"cpi":16,"inflation":14,"recession":17,"hormuz":20,"iran":16,"oil":14,"brent":14,"wti":14,"bitcoin":13,"ethereum":10,"etf":12,"treasury":10,"default":18,"government shutdown":10,"stablecoin":10}

def now_utc(): return datetime.now(timezone.utc)
def iso(dt): return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")

def load_config():
    cfg=dict(DEFAULT_CONFIG)
    if CONFIG.exists(): cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
    return cfg

def parse_jsonish(v):
    if isinstance(v,list): return v
    if v in (None,""): return []
    if isinstance(v,str):
        try:
            x=json.loads(v); return x if isinstance(x,list) else []
        except Exception: return []
    return []

def num(v):
    try: return None if v in (None,"") else float(v)
    except Exception: return None

def text_blob(m):
    bits=[m.get("question") or "",m.get("description") or "",m.get("category") or "",m.get("slug") or ""]
    evs=m.get("events") or []
    if isinstance(evs,list):
        for e in evs[:2]:
            if isinstance(e,dict): bits += [e.get("title") or "",e.get("subtitle") or "",e.get("slug") or "",e.get("category") or "",e.get("subcategory") or ""]
    tags=m.get("tags") or []
    if isinstance(tags,list):
        for t in tags[:10]:
            if isinstance(t,dict): bits += [t.get("label") or "",t.get("slug") or ""]
    return " ".join(bits).lower()

def classify_theme(m):
    blob=" "+text_blob(m)+" "
    if any(x in blob for x in EXCLUDE):
        strong=any(k in blob for k in ["fomc","rate hike","rate cut","cpi","inflation","recession","bitcoin","ethereum","hormuz","oil"])
        if not strong: return None,0
    best_theme,best=None,0
    for theme,kws in THEMES.items():
        score=sum(6 for kw in kws if kw in blob)
        if score>best: best_theme,best=theme,score
    return best_theme,best

def event_family(m):
    evs=m.get("events") or []
    if isinstance(evs,list) and evs and isinstance(evs[0],dict) and evs[0].get("slug"): return str(evs[0]["slug"])
    slug=str(m.get("slug") or m.get("question") or m.get("id") or "")
    return re.sub(r"\d+(?:pt\d+|\.\d+|k)?","#",slug.lower())

def yes_token_and_prob(m):
    outcomes=[str(x) for x in parse_jsonish(m.get("outcomes"))]
    prices=parse_jsonish(m.get("outcomePrices")); tokens=[str(x) for x in parse_jsonish(m.get("clobTokenIds"))]
    if not outcomes or not prices: return None,None
    idx=next((i for i,x in enumerate(outcomes) if x.strip().lower()=="yes"),None)
    if idx is None or idx>=len(prices): return None,None
    p=num(prices[idx]); token=tokens[idx] if idx<len(tokens) else None
    return token,p

def end_urgency_score(m,now):
    raw=m.get("endDate") or m.get("endDateIso")
    if not raw: return 0.0
    try:
        dt=datetime.fromisoformat(str(raw).replace("Z","+00:00")); days=(dt-now).total_seconds()/86400
        if days < -0.5: return -30
        if days<=7: return 10
        if days<=30: return 8
        if days<=90: return 4
        return 1
    except Exception: return 0.0

def initial_score(m,theme_score,now):
    blob=text_blob(m); s=20+min(theme_score,30)
    for kw,w in HIGH_IMPACT.items():
        if kw in blob: s+=w
    v24=num(m.get("volume24hr")) or 0; vt=num(m.get("volumeNum") or m.get("volume")) or 0; liq=num(m.get("liquidityNum") or m.get("liquidity")) or 0
    s += min(12,max(0,math.log10(v24+1)-3)*4); s += min(8,max(0,math.log10(liq+1)-3)*3); s += min(6,max(0,math.log10(vt+1)-4)*2)
    _,p=yes_token_and_prob(m)
    if p is not None:
        if 0.10<=p<=0.90: s+=4
        if 0.20<=p<=0.80: s+=3
    s += end_urgency_score(m,now)
    return round(s,2)

class HTTP:
    def __init__(self,timeout):
        self.timeout=timeout; self.s=requests.Session(); self.s.headers.update({"User-Agent":UA,"Accept":"application/json"})
    def get(self,url,params=None):
        r=self.s.get(url,params=params,timeout=self.timeout); r.raise_for_status(); return r.json()

def fetch_markets(h,cfg):
    limit=int(cfg["gamma_scan_limit"]); union={}; errs=[]
    queries=[
        {"limit":limit,"closed":"false","order":"volume24hr","ascending":"false","include_tag":"true"},
        {"limit":limit,"closed":"false","order":"liquidityNum","ascending":"false","include_tag":"true"}
    ]
    for q in queries:
        try:
            data=h.get(f"{GAMMA}/markets",params=q)
            if isinstance(data,list):
                for m in data:
                    if isinstance(m,dict) and m.get("id"): union[str(m["id"])]=m
        except Exception as e: errs.append(str(e))
    if not union: raise RuntimeError("Gamma market discovery failed: "+" | ".join(errs))
    return list(union.values())

def closest_price(history,target_ts,tolerance_sec):
    best=gap=None
    for x in history:
        try: t=int(float(x.get("t"))); p=float(x.get("p"))
        except Exception: continue
        g=abs(t-target_ts)
        if g<=tolerance_sec and (gap is None or g<gap): best,gap=p,g
    return best

def history_deltas(h,token,current,now):
    start=int((now-timedelta(days=8)).timestamp()); end=int(now.timestamp())
    try:
        data=h.get(f"{CLOB}/prices-history",params={"market":token,"startTs":start,"endTs":end,"interval":"all","fidelity":60}); hist=data.get("history") if isinstance(data,dict) else []
    except Exception: hist=[]
    out={}
    for label,hours in [("1h",1),("4h",4),("1d",24),("7d",168)]:
        target=int((now-timedelta(hours=hours)).timestamp()); tol=5400 if hours<=4 else 14400; old=closest_price(hist or [],target,tol)
        out[f"delta_pp_{label}"]=None if old is None else round((current-old)*100,2)
    return out

def spread_for(h,token):
    try:
        x=h.get(f"{CLOB}/spread",params={"token_id":token}); return num(x.get("spread")) if isinstance(x,dict) else None
    except Exception: return None

def oi_for(h,condition_id):
    try:
        x=h.get(f"{DATA_API}/oi",params={"market":condition_id})
        if isinstance(x,list) and x:
            vals=[num(i.get("value")) for i in x if isinstance(i,dict)]; vals=[v for v in vals if v is not None]
            return sum(vals) if vals else None
    except Exception: pass
    return None

def quality_grade(v24,total,liq,spread):
    if spread is not None and spread<=0.03 and v24>=100000 and liq>=50000: return "A"
    if spread is not None and spread<=0.06 and (v24>=20000 or liq>=20000) and total>=50000: return "B"
    if (v24>=10000 or liq>=10000) and total>=25000: return "C"
    return "D"

def polarity_hint(theme,question):
    q=question.lower()
    if theme=="FED_INFLATION":
        if any(x in q for x in ["rate hike","increase","inflation above","cpi above","higher than"]): return "YES↑ = BTC/위험자산 대체로 부담"
        if any(x in q for x in ["rate cut","decrease"]): return "YES↑ = 유동성 측면 긍정 가능; 침체성 인하인지 별도 확인"
    if theme=="GEO_OIL":
        if any(x in q for x in ["ceasefire","peace","reopen","open strait"]): return "YES↑ = 지정학·유가 위험 완화 가능"
        return "YES↑ = 지정학·유가 위험 확대 가능"
    if theme=="CRYPTO_PRICE":
        if any(x in q for x in ["dip","below","fall","drop","under"]): return "YES↑ = 해당 코인 하방 기대 강화"
        if any(x in q for x in ["reach","above","hit","over"]): return "YES↑ = 해당 코인 상방 기대 강화"
    if theme=="US_MACRO" and any(x in q for x in ["recession","shutdown","default"]): return "YES↑ = 위험자산 부담 가능"
    if theme=="CRYPTO_POLICY" and any(x in q for x in ["approve","approval","pass","signed"]): return "YES↑ = 코인 정책환경 개선 가능"
    return "상황형 — MASTER가 뉴스·금리·가격과 함께 해석"

def enrich_market(h,m,theme,base_score,now):
    token,p=yes_token_and_prob(m)
    if p is None or token is None: return None
    v24=num(m.get("volume24hr")) or 0; total=num(m.get("volumeNum") or m.get("volume")) or 0; liq=num(m.get("liquidityNum") or m.get("liquidity")) or 0
    spread=spread_for(h,token); grade=quality_grade(v24,total,liq,spread); condition=str(m.get("conditionId") or ""); oi=oi_for(h,condition) if condition else None
    deltas=history_deltas(h,token,p,now); quality_bonus={"A":14,"B":9,"C":3,"D":-8}[grade]; movement=max([abs(v) for v in deltas.values() if isinstance(v,(int,float))] or [0]); score=round(base_score+quality_bonus+min(10,movement/2),2)
    evs=m.get("events") or []; event_title=evs[0].get("title") if isinstance(evs,list) and evs and isinstance(evs[0],dict) else None
    q=str(m.get("question") or event_title or m.get("slug") or "")
    return {"market_id":str(m.get("id")),"condition_id":condition or None,"slug":m.get("slug"),"event_title":event_title,"question":q,"theme":theme,"probability_yes":round(p*100,2),**deltas,"volume_24h_usd":round(v24,2),"volume_total_usd":round(total,2),"liquidity_usd":round(liq,2),"open_interest_usd":None if oi is None else round(oi,2),"spread_pct_points":None if spread is None else round(spread*100,2),"confidence_grade":grade,"relevance_score":score,"end_date":m.get("endDate") or m.get("endDateIso"),"yes_token_id":token,"polarity_hint":polarity_hint(theme,q),"family":event_family(m),"url":f"https://polymarket.com/event/{m.get('slug')}" if m.get("slug") else None}

def select_top(rows,cfg):
    rows=sorted(rows,key=lambda x:(x["relevance_score"],x["volume_24h_usd"],x["liquidity_usd"]),reverse=True); out=[]; tcount={}; fcount={}; caps=cfg["theme_caps"]
    for r in rows:
        if r["confidence_grade"]=="D": continue
        theme=r["theme"]; fam=r["family"]; tcap=int(caps.get(theme,10)); fcap=int(cfg["family_cap_crypto_price"] if theme=="CRYPTO_PRICE" else cfg["family_cap_default"])
        if tcount.get(theme,0)>=tcap or fcount.get(fam,0)>=fcap: continue
        out.append(r); tcount[theme]=tcount.get(theme,0)+1; fcount[fam]=fcount.get(fam,0)+1
        if len(out)>=int(cfg["top_n"]): break
    if len(out)<int(cfg["top_n"]):
        seen={r["market_id"] for r in out}
        for r in rows:
            if r["market_id"] in seen or r["confidence_grade"]=="D": continue
            out.append(r); seen.add(r["market_id"])
            if len(out)>=int(cfg["top_n"]): break
    for i,r in enumerate(out,1): r["rank"]=i
    return out

def append_history(rows,generated_at):
    DATA_DIR.mkdir(parents=True,exist_ok=True); fields=["snapshot_utc","rank","market_id","theme","question","probability_yes","delta_pp_1h","delta_pp_4h","delta_pp_1d","delta_pp_7d","volume_24h_usd","liquidity_usd","open_interest_usd","spread_pct_points","confidence_grade","relevance_score","slug"]; exists=HISTORY_CSV.exists()
    with HISTORY_CSV.open("a",newline="",encoding="utf-8") as fh:
        w=csv.DictWriter(fh,fieldnames=fields)
        if not exists: w.writeheader()
        for r in rows:
            d={k:r.get(k) for k in fields}; d["snapshot_utc"]=generated_at; w.writerow(d)

def watch_candidates(rows,cfg):
    out=[]
    for r in rows:
        if r.get("confidence_grade") not in ("A","B"): continue
        d4=r.get("delta_pp_4h"); d1=r.get("delta_pp_1d"); hit4=isinstance(d4,(int,float)) and abs(d4)>=float(cfg["watch_move_pp_4h"]); hit1=isinstance(d1,(int,float)) and abs(d1)>=float(cfg["watch_move_pp_1d"])
        if hit4 or hit1: out.append({"rank":r["rank"],"question":r["question"],"theme":r["theme"],"probability_yes":r["probability_yes"],"delta_pp_4h":d4,"delta_pp_1d":d1,"confidence_grade":r["confidence_grade"],"rule":"Polymarket single-signal only; MASTER WATCH requires >=1 independent aligned confirmation"})
    return out

def collect():
    cfg=load_config(); now=now_utc(); h=HTTP(int(cfg["timeout_seconds"])); markets=fetch_markets(h,cfg); candidates=[]
    for m in markets:
        if m.get("closed") is True or m.get("active") is False: continue
        theme,ts=classify_theme(m)
        if not theme: continue
        token,p=yes_token_and_prob(m)
        if token is None or p is None: continue
        total=num(m.get("volumeNum") or m.get("volume")) or 0; liq=num(m.get("liquidityNum") or m.get("liquidity")) or 0
        if total<float(cfg["min_total_volume"]) and liq<float(cfg["min_liquidity"]): continue
        candidates.append((initial_score(m,ts,now),theme,m))
    candidates.sort(key=lambda x:x[0],reverse=True); enriched=[]; failures=[]
    for base,theme,m in candidates[:int(cfg["preselect_n"])]:
        try:
            row=enrich_market(h,m,theme,base,now)
            if row: enriched.append(row)
        except Exception as e: failures.append({"market_id":m.get("id"),"error":str(e)[:240]})
    top=select_top(enriched,cfg); gen=iso(now); OUT_DIR.mkdir(parents=True,exist_ok=True); STATE_DIR.mkdir(parents=True,exist_ok=True)
    summary={"schema_version":"1.0","engine":"MASTER_MARKET_POLYMARKET_EXPECTATION_RADAR_V1","generated_at_utc":gen,"source":"Polymarket public Gamma API + CLOB API + Data API","authentication_required":False,"score_weight":0,"purpose":"Forward-looking expectation/context only; cannot alone flip MASTER direction or score.","selection_policy":"Market-impact relevance + liquidity/volume/spread quality + diversification; sports/entertainment/general election winner markets excluded.","watch_policy":"Polymarket alone never alerts. A/B market move >=10pp/4H or >=15pp/1D is a WATCH candidate only when at least one independent MASTER axis aligns.","top10":top,"watch_candidates":watch_candidates(top,cfg)}
    LATEST_JSON.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8"); append_history(top,gen)
    state={"schema_version":"1.0","generated_at_utc":gen,"status":"PASS" if len(top)>=8 else "PARTIAL","discovered_markets":len(markets),"relevant_candidates":len(candidates),"enriched_candidates":len(enriched),"top_count":len(top),"failures":failures[:20],"auth_required":False,"score_weight":0}
    STATE_JSON.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(state,ensure_ascii=False)); return 0 if len(top)>=5 else 2

def validate():
    if not LATEST_JSON.exists() or not STATE_JSON.exists(): raise SystemExit("missing output/state")
    p=json.loads(LATEST_JSON.read_text(encoding="utf-8")); rows=p.get("top10") or []
    assert p.get("engine")=="MASTER_MARKET_POLYMARKET_EXPECTATION_RADAR_V1" and p.get("score_weight")==0
    if len(rows)<5: raise SystemExit("usable Polymarket rows <5")
    for r in rows:
        if r.get("theme") not in THEMES: raise SystemExit("unknown theme")
        if r.get("confidence_grade") not in ("A","B","C"): raise SystemExit("bad confidence grade")
        if r.get("probability_yes") is None: raise SystemExit("missing probability")
    print("POLYMARKET_VALIDATION=PASS",len(rows)); return 0

def self_test():
    fx={"id":"1","question":"Fed rate hike by September meeting?","description":"","slug":"fed-rate-hike-by-september","category":"Finance","active":True,"closed":False,"outcomes":"[\"Yes\",\"No\"]","outcomePrices":"[\"0.62\",\"0.38\"]","clobTokenIds":"[\"yes-token\",\"no-token\"]","volume24hr":150000,"volumeNum":2000000,"liquidityNum":120000,"endDate":"2026-09-16T18:00:00Z"}
    theme,sc=classify_theme(fx); assert theme=="FED_INFLATION" and sc>0; tok,p=yes_token_and_prob(fx); assert tok=="yes-token" and abs(p-0.62)<1e-9; assert "부담" in polarity_hint(theme,fx["question"])
    btc=dict(fx); btc.update({"id":"2","question":"Will Bitcoin reach $100k in September?","slug":"btc-100k"}); t,_=classify_theme(btc); assert t=="CRYPTO_PRICE"
    junk=dict(fx); junk.update({"id":"3","question":"Will Lakers win the NBA title?","slug":"nba-title","category":"Sports"}); t,_=classify_theme(junk); assert t is None
    print("POLYMARKET_SELF_TEST=PASS"); return 0

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--mode",choices=["collect","validate","self-test"],default="collect"); a=ap.parse_args()
    return self_test() if a.mode=="self-test" else validate() if a.mode=="validate" else collect()

if __name__=="__main__": sys.exit(main())
