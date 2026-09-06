#!/usr/bin/env python3
"""Idempotent additive patch: add a 3rd-column judgement traffic light to MASTER MARKET Polymarket TOP10."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPT = ROOT / "master_prompts/master_market_v1_2_current.md"
CONTRACT = ROOT / "state/master_market_v1_2_contract.json"

OLD_COLS = "Required compact columns = `순위 | 시장/질문 | 현재확률 | Δ1H | Δ4H | Δ1D | Δ7D | 24H거래량 | 유동성/OI | 신뢰도 | 시장영향 | 쉬운해석`."
NEW_COLS = "Required compact columns = `순위 | 시장/질문 | 판정 | 현재확률 | Δ1H | Δ4H | Δ1D | Δ7D | 24H거래량 | 유동성/OI | 신뢰도 | 시장영향 | 쉬운해석`."
JUDGEMENT_BLOCK = '''- `판정`은 반드시 3번째 열에 둔다. 표시값은 `🟢 상승우호` / `🔴 하락압력` / `🟡 중립·혼합` / `⚪ N/A` 중 하나다.
- `판정`은 그 Polymarket 항목이 현재 BTC/ETH/ALT·위험자산에 주는 방향적 함의를 한눈에 보여주는 **표시용 신호등**이다. 단순 YES 확률 크기나 신뢰등급 자체를 색으로 바꾸지 말고, 사건의 전파경로(Fed·DXY·금리·유가·ETF·규제·BTC/ETH 가격조건 등)와 현재 방향 의미를 기준으로 해석한다.
- 판정이 조건부·양면적이거나 방향을 확정하기 어렵다면 `🟡 중립·혼합`, 데이터가 부족하면 `⚪ N/A`를 사용한다.
- 이 판정 신호등은 display-only이며 Polymarket의 기존 `score weight 0` 원칙을 유지한다. 자체적으로 MASTER score, 최종 롱/숏, Risk Veto, WATCH threshold를 바꾸지 않는다.
'''


def patch_prompt():
    p = PROMPT.read_text(encoding="utf-8")
    if OLD_COLS in p:
        p = p.replace(OLD_COLS, NEW_COLS, 1)
    elif NEW_COLS not in p:
        raise SystemExit("Polymarket required-column marker not found")

    anchor = "- `현재확률` is the Polymarket YES probability. All deltas are percentage-point changes, not percent returns.\n"
    if "`판정`은 반드시 3번째 열에 둔다." not in p:
        if anchor not in p:
            raise SystemExit("Polymarket current-probability anchor missing")
        p = p.replace(anchor, JUDGEMENT_BLOCK + anchor, 1)
    PROMPT.write_text(p, encoding="utf-8")


def patch_contract():
    c = json.loads(CONTRACT.read_text(encoding="utf-8"))
    c["schema_version"] = "1.5"
    c["updated_kst"] = "2026-09-06T21:29:00+09:00"

    rd = c.setdefault("required_data", {}).setdefault("prediction_markets", [])
    req = "traffic-light judgement for current market impact"
    if req not in rd:
        rd.append(req)

    s5 = c.setdefault("required_output", {}).setdefault("screen5", [])
    old = "Polymarket TOP10: rank|market/question|current YES probability|1H|4H|1D|7D probability-point changes|24H volume|liquidity/OI|confidence|market impact|easy interpretation; score0; single-signal alert forbidden"
    new = "Polymarket TOP10: rank|market/question|judgement traffic-light as 3rd column|current YES probability|1H|4H|1D|7D probability-point changes|24H volume|liquidity/OI|confidence|market impact|easy interpretation; score0; single-signal alert forbidden"
    if old in s5:
        s5[s5.index(old)] = new
    elif new not in s5:
        s5.append(new)

    pm = c.setdefault("polymarket_policy", {})
    pm["judgement_display"] = {
        "enabled": True,
        "column_name": "판정",
        "column_position": 3,
        "values": {
            "bullish": "🟢 상승우호",
            "bearish": "🔴 하락압력",
            "mixed_neutral": "🟡 중립·혼합",
            "unknown": "⚪ N/A"
        },
        "basis": "Directional implication for BTC/ETH/ALT/risk assets from the event transmission path and current meaning; do not color solely from YES probability magnitude or confidence grade.",
        "conditional_behavior": "Use 🟡 중립·혼합 when direction is conditional/two-sided; use ⚪ N/A when insufficient.",
        "score_effect": 0,
        "direction_effect": "NONE",
        "risk_veto_effect": "NONE",
        "watch_threshold_effect": "NONE"
    }
    CONTRACT.write_text(json.dumps(c, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    patch_prompt()
    patch_contract()
    p = PROMPT.read_text(encoding="utf-8")
    c = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert NEW_COLS in p
    assert "`판정`은 반드시 3번째 열에 둔다." in p
    jd = c["polymarket_policy"]["judgement_display"]
    assert jd["enabled"] is True and jd["column_position"] == 3 and jd["score_effect"] == 0
    assert jd["values"]["bullish"] == "🟢 상승우호"
    assert jd["values"]["bearish"] == "🔴 하락압력"
    print("MASTER_MARKET_POLYMARKET_JUDGEMENT_DISPLAY=PASS")

if __name__ == "__main__":
    main()
