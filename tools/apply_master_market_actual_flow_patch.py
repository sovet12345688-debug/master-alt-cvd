from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANON = ROOT / "master_prompts" / "master_market_v1_2_current.md"
CONTRACT = ROOT / "state" / "master_market_v1_2_contract.json"
KST = timezone(timedelta(hours=9))


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise SystemExit(f"canonical patch anchor missing: {label}")
    return text.replace(old, new, 1)


def patch_canonical() -> None:
    text = CANON.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "BTC ETF and ETH ETF 1D/3D/5D/20D; institutional flow; USDT/USDC/total stablecoin supply.",
        "BTC ETF and ETH ETF 1D/3D/5D/7D/20D; institutional flow; USDT/USDC/total stablecoin supply.",
        "required source ETF windows",
    )
    text = replace_once(
        text,
        "ETF: today/3D/5D/20D where confirmed. Stablecoin supply increase != actual buy.",
        "ETF: 1D/3D/5D/7D/20D where confirmed. Stablecoin supply increase != actual buy.",
        "ETF/STABLECOIN summary",
    )
    text = replace_once(
        text,
        "for BTC/ETH ETF `1D/3D/5D/20D`.",
        "for BTC/ETH ETF `1D/3D/5D/7D/20D`.",
        "free recovery ETF windows",
    )
    text = replace_once(
        text,
        "BTC/ETH ETF preserves confirmed applicable `1D / 3D / 5D / 20D`.",
        "BTC/ETH ETF preserves confirmed applicable `1D / 3D / 5D / 7D / 20D`.",
        "SCREEN3 ETF windows",
    )
    text = replace_once(
        text,
        "Use `market_vault/output/latest_etf_flows.json` when fresh for BTC/ETH ETF 1D/3D/5D/20D; its mirror is transport/cache only, not a new score/source owner.",
        "Use `market_vault/output/latest_etf_flows.json` when fresh for BTC/ETH ETF 1D/3D/5D/7D/20D; its mirror is transport/cache only, not a new score/source owner.\n- Use `market_vault/output/latest_stablecoin_windows.json` when fresh for same-source Stablecoin prior/1D/3D/5D/7D/20D comparisons; missing history stays N/A and is never interpolated/backfilled.\n- Use `market_vault/output/latest_actor_flows.json` when fresh for SCREEN4 actual-value institution/whale/retail-proxy comparisons; this adapter adds no score weight and does not alter Market Positive Score, Risk Veto, WATCH, or official score history.",
        "implementation adapter notes",
    )

    old_actor = """### 기관 vs 고래 vs 개미
Required table columns = `신호 | 주체 | 점수 | 직전 | 1D | 3D | 7D | 현재상태`.
Required subjects where available = 기관 / BTC 고래 / ETH 고래 / 개미·리테일.
Interpretation must be short plain Korean, e.g. `기관 매수 우위`, `BTC 고래 혼조`, `ETH 고래 숏 우위`, `개미 과열`, `레버리지 완화`.
"""
    new_actor = """### 기관 vs 고래 vs 개미
Required table columns = `신호 | 주체 | 현재 실제수치 | 1D | 3D | 7D | 현재상태`.
Do NOT show a synthetic institution/whale/retail score in this SCREEN4 table.
Use directly observable stored values only:
- 기관 BTC/ETH = actual spot ETF net-flow USD from the ETF collector. Use actual 1D/3D/7D cumulative trading-row flow where available.
- BTC/ETH 고래 = actual Hyperliquid large-position exposure aggregated from stored signed position history. Show `LONG 총액 / SHORT 총액 / NET USD`; large-position aggregate baseline = positions with absolute position value >= $20M. Compare current with actual same-source 1D/3D/7D snapshots only.
- 개미/리테일 = Bitget futures active long/short position-ratio proxy, not verified wallet identity. Show actual LONG% / SHORT% and current funding context; compare actual same-venue 1D/3D/7D observations when available.
Interpretation must be short plain Korean, e.g. `기관 순유입`, `BTC 고래 NET 숏`, `ETH 고래 NET 롱`, `개미 롱 과열`, `중립`.
If a comparison window has insufficient actual history, keep N/A; never manufacture a score or infer missing values.
"""
    text = replace_once(text, old_actor, new_actor, "SCREEN4 actual actor values")

    follow_anchor = "OFFICIAL output includes exactly 5 compact follow-up questions or recommendations before footer. Keep them brief and directly related to current market/data validation/useful next analysis/output improvement. Recommendations never modify the MASTER contract automatically."
    follow_new = follow_anchor + "\n\n### GITHUB CHANGE FOLLOW-UP LOCK\nWhenever the user requests any output/data/engine/schema/rule change, first determine whether a GitHub canonical/contract/collector/schema patch is actually required. Only when a GitHub patch is required, follow-up question #4 must be exactly `변경 사항 발생. github 변경 패치 작업 진행 도와줄까?`. If no GitHub patch is required, do not show that sentence and use a normal relevant #4 follow-up instead."
    text = replace_once(text, follow_anchor, follow_new, "GitHub follow-up lock")

    CANON.write_text(text, encoding="utf-8")


def patch_contract() -> None:
    c = json.loads(CONTRACT.read_text(encoding="utf-8"))
    c["schema_version"] = "1.7"
    c["updated_kst"] = datetime.now(KST).replace(microsecond=0).isoformat()

    cm = c.setdefault("change_management", {})
    cm["github_patch_followup"] = {
        "decision_required_on_every_change_request": True,
        "only_when_patch_required": True,
        "follow_up_slot": 4,
        "exact_text": "변경 사항 발생. github 변경 패치 작업 진행 도와줄까?",
        "when_not_required": "Do not show the fixed GitHub patch sentence; use a normal relevant follow-up question #4.",
    }

    flows = c.get("required_data", {}).get("crypto_capital_flow", [])
    for i, item in enumerate(flows):
        if item == "BTC ETF 1D/3D/5D/20D":
            flows[i] = "BTC ETF 1D/3D/5D/7D/20D"
        if item == "ETH ETF 1D/3D/5D/20D":
            flows[i] = "ETH ETF 1D/3D/5D/7D/20D"

    s3 = c.get("required_output", {}).get("screen3", [])
    for i, item in enumerate(s3):
        if item == "BTC/ETH ETF preserve confirmed 1D/3D/5D/20D":
            s3[i] = "BTC/ETH ETF preserve confirmed 1D/3D/5D/7D/20D"
    extra_s3 = "stablecoin same-source comparison adapter: prior|1D|3D|5D|7D|20D; missing actual history remains N/A; no interpolation/backfill"
    if extra_s3 not in s3:
        s3.append(extra_s3)

    s4 = c.get("required_output", {}).get("screen4", [])
    old = "institution vs whale vs retail table columns: signal|subject|score|prior|1D|3D|7D|current status"
    new = "institution vs whale vs retail table columns: signal|subject|current actual value|1D|3D|7D|current status; no synthetic actor score in SCREEN4"
    if old in s4:
        s4[s4.index(old)] = new
    elif new not in s4:
        raise SystemExit("contract SCREEN4 actor-table anchor missing")
    additions = [
        "institution BTC/ETH actual value = spot ETF net-flow USD; use actual 1D/3D/7D cumulative flow",
        "BTC/ETH whale actual value = Hyperliquid >=$20M position LONG USD|SHORT USD|NET USD using stored signed-position history; compare actual 1D/3D/7D snapshots",
        "retail actual value = Bitget futures active LONG%|SHORT% ratio proxy plus current funding context; not verified retail wallet identity; compare same-venue 1D/3D/7D observations",
        "missing actor comparison history remains N/A; never manufacture actor scores or backfill values",
    ]
    for x in additions:
        if x not in s4:
            s4.append(x)

    sp = c.setdefault("source_policy", {})
    sp["stablecoin_window_adapter"] = "Read market_vault/output/latest_stablecoin_windows.json when fresh for same-source prior/1D/3D/5D/7D/20D Stablecoin comparisons. No interpolation or backfill."
    sp["actor_flow_adapter"] = "Read market_vault/output/latest_actor_flows.json when fresh for SCREEN4 actual-value institution/whale/retail-proxy comparisons. Adapter score weight is 0 and does not alter underlying score engine, Risk Veto, WATCH, or official history."

    CONTRACT.write_text(json.dumps(c, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate() -> None:
    text = CANON.read_text(encoding="utf-8")
    c = json.loads(CONTRACT.read_text(encoding="utf-8"))
    checks = [
        "BTC/ETH ETF preserves confirmed applicable `1D / 3D / 5D / 7D / 20D`.",
        "Required table columns = `신호 | 주체 | 현재 실제수치 | 1D | 3D | 7D | 현재상태`.",
        "latest_stablecoin_windows.json",
        "latest_actor_flows.json",
        "변경 사항 발생. github 변경 패치 작업 진행 도와줄까?",
    ]
    for x in checks:
        if x not in text:
            raise SystemExit(f"canonical validation missing: {x}")
    if c.get("schema_version") != "1.7":
        raise SystemExit("contract schema version not 1.7")
    s4 = c.get("required_output", {}).get("screen4", [])
    if not any("no synthetic actor score" in x for x in s4):
        raise SystemExit("contract actual actor table rule missing")
    if c.get("change_management", {}).get("github_patch_followup", {}).get("follow_up_slot") != 4:
        raise SystemExit("contract GitHub follow-up lock missing")
    print("MASTER_MARKET_ACTUAL_FLOW_PATCH=PASS")


def main() -> None:
    patch_canonical()
    patch_contract()
    validate()


if __name__ == "__main__":
    main()
