#!/usr/bin/env python3
"""Idempotent one-time/additive patch: lock Polymarket TOP10 into MASTER MARKET V1.2."""
# Triggered after the lock workflow exists on main; safe to re-run idempotently.
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPT = ROOT / "master_prompts/master_market_v1_2_current.md"
CONTRACT = ROOT / "state/master_market_v1_2_contract.json"
GUARD = ROOT / ".github/workflows/master_market_contract_guard.yml"

pm_source_block = r'''### Prediction Market / Polymarket — ADDED 2026-09-06
- Read `polymarket/output/latest_summary.json` every run when fresh and schema/engine guards pass. Its hourly history is `polymarket/data/hourly_top10.csv`.
- Source role = **forward expectation/context only**. Initial `score weight 0`; Polymarket cannot by itself change Market Positive Score, BTC Liquidity Lead, Crypto Money Inflow, ALT Money Inflow, final 롱/숏, or Risk Veto.
- Track market-impact TOP10 across: Fed/rates/inflation/jobs, geopolitics/oil, BTC/ETH/major crypto price events, US recession/financial shock, crypto regulation/ETF/policy.
- Exclude sports, entertainment, celebrity, and generic election-winner markets unless the specific market has a direct material transmission path to rates/DXY/oil/ETF/crypto liquidity.
- Market quality must use actual Polymarket volume/liquidity/spread/open-interest where available. Prefer A/B confidence; C is fallback only; D/filler is excluded.
- Current probability is an expectation, not a confirmed future fact. Always cross-check with MASTER factual axes before interpreting market impact.
- Probability change windows are `Δ1H | Δ4H | Δ1D | Δ7D` in percentage points, using the same market + same YES outcome from official Polymarket CLOB history or exact stored snapshots only. No interpolation/reconstruction.
- Deduplicate related markets and apply event/theme diversification so one price ladder or one event does not crowd out the TOP10.
- WATCH rule: **Polymarket alone never alerts.** An A/B market move of `>=10pp/4H` or `>=15pp/1D` is only a WATCH candidate and requires at least one independent aligned MASTER axis. Polymarket alone never creates LEVEL1/LEVEL2.
- If Polymarket data is stale/unavailable, keep the SCREEN5 block visible and show `N/A` / `확인 실패`; do not substitute guessed probabilities.
'''

pm_screen_block = r'''### 5) 🎯 POLYMARKET — 시장이 돈 걸고 보는 미래 TOP10
Read fresh `polymarket/output/latest_summary.json` when available. Show up to exactly 10 highest-relevance qualifying markets; do not pad with low-quality filler.
Required compact columns = `순위 | 시장/질문 | 현재확률 | Δ1H | Δ4H | Δ1D | Δ7D | 24H거래량 | 유동성/OI | 신뢰도 | 시장영향 | 쉬운해석`.
- `현재확률` is the Polymarket YES probability. All deltas are percentage-point changes, not percent returns.
- Themes are Fed/rates/inflation/jobs, geopolitics/oil, BTC/ETH/major crypto price, US recession/financial shock, crypto regulation/ETF/policy. Keep theme/event diversification; related ladder markets may be shown only when they add distinct actionable information.
- Trust grade must reflect actual liquidity, 24H/total volume, spread, and OI where available. A/B are preferred. Explain thin or one-sided markets instead of treating their probability as equally reliable.
- Add one compact `쉽게 보면:` synthesis that states what prediction-market money is increasingly pricing in, what is easing, and where it agrees/conflicts with MASTER factual data.
- This block is `score weight 0`. It is context/early expectation only and never directly changes MASTER score/direction/Risk Veto.
- WATCH: Polymarket alone never alerts. A/B `>=10pp/4H` or `>=15pp/1D` move becomes a candidate only with >=1 independent aligned MASTER confirmation.
- If fresh validated output is unavailable, retain this block and print `N/A / 확인 실패`.
'''


def patch_prompt():
    p = PROMPT.read_text(encoding="utf-8")
    if "### Prediction Market / Polymarket — ADDED 2026-09-06" not in p:
        marker = "- User explicitly removed the on-chain secondary-confirmation output block on 2026-09-05. Do not output STH-SOPR, STH Realized Price, STH-MVRV, Exchange Netflow, or an on-chain synthesis block unless the user explicitly restores it.\n"
        assert marker in p, "secondary-opinion marker missing"
        p = p.replace(marker, marker + "\n" + pm_source_block + "\n", 1)
    p = p.replace("## SCREEN 5 — 뉴스·경제일정·전문가\n", "## SCREEN 5 — 뉴스·경제일정·전문가·Polymarket\n", 1)
    if "### 5) 🎯 POLYMARKET — 시장이 돈 걸고 보는 미래 TOP10" not in p:
        marker = "Do not treat an old public view as current; if no fresh verified view exists, mark `최신 직접관점 N/A` and show the date of the last verified view.\n"
        assert marker in p, "Stanley block marker missing"
        p = p.replace(marker, marker + "\n" + pm_screen_block + "\n", 1)
    if "- Use `polymarket/output/latest_summary.json` when fresh" not in p:
        marker = "- Use `market_whales/output/latest_summary.json` and events/history when fresh for Hyperliquid official-API-derived position data.\n"
        assert marker in p, "data-source note marker missing"
        p = p.replace(marker, marker + "- Use `polymarket/output/latest_summary.json` when fresh for score-0 forward-expectation TOP10; its probability deltas must remain same-market/same-outcome and cannot substitute factual macro/crypto data.\n", 1)
    if "Polymarket single-signal alert is forbidden" not in p:
        marker = "LEVEL2: at least two independent aligned axes. NO ALERT for unknown wallet alone, price-only move, OI alone, funding alone, stale/time-mismatched liq distance, unstable single source, old event reuse. Same EVENT_ID does not repeat unless direction reversal, meaningful size expansion, new independent confirmation, or Risk Veto onset/clearance.\n"
        assert marker in p, "urgent-watch marker missing"
        p = p.replace(marker, marker + "Polymarket single-signal alert is forbidden. A/B `>=10pp/4H` or `>=15pp/1D` is candidate-only and still requires >=1 independent aligned MASTER confirmation.\n", 1)
    PROMPT.write_text(p, encoding="utf-8")


def patch_contract():
    c = json.loads(CONTRACT.read_text(encoding="utf-8"))
    c["schema_version"] = "1.4"
    c["updated_kst"] = "2026-09-06T12:09:00+09:00"
    rd = c.setdefault("required_data", {})
    rd["prediction_markets"] = [
        "Polymarket market-impact TOP10",
        "YES probability current",
        "probability-point change 1H/4H/1D/7D",
        "24H and total volume",
        "liquidity",
        "open interest",
        "spread",
        "confidence grade",
        "market impact and easy interpretation"
    ]
    sp = c.setdefault("source_policy", {})
    sp["polymarket"] = "Public Gamma/CLOB/Data APIs; forward-expectation context only; score weight 0. Same-market/same-YES-outcome deltas only. Polymarket alone never alerts or flips score/direction/Risk Veto."
    c["polymarket_policy"] = {
        "enabled": True,
        "top_n": 10,
        "score_weight": 0,
        "collector_output": "polymarket/output/latest_summary.json",
        "collector_state": "polymarket/state/collector_state.json",
        "history": "polymarket/data/hourly_top10.csv",
        "themes": ["FED_INFLATION", "GEO_OIL", "CRYPTO_PRICE", "US_MACRO", "CRYPTO_POLICY"],
        "change_windows": ["1H", "4H", "1D", "7D"],
        "quality_inputs": ["24H volume", "total volume", "liquidity", "spread", "open interest"],
        "prefer_grades": ["A", "B"],
        "watch_candidate_thresholds_pp": {"4H": 10, "1D": 15},
        "single_signal_alert_forbidden": True,
        "watch_requires_independent_aligned_confirmation": True,
        "cannot_directly_change": ["Market Positive Score", "BTC Liquidity Lead", "Crypto Money Inflow", "ALT Money Inflow", "direction", "Risk Veto"],
        "missing_behavior": "Keep SCREEN5 Polymarket block visible and mark N/A/CONFIRMATION_FAILED. Never guess probability.",
        "onchain_output_remains_removed": True
    }
    s5 = c.setdefault("required_output", {}).setdefault("screen5", [])
    item = "Polymarket TOP10: rank|market/question|current YES probability|1H|4H|1D|7D probability-point changes|24H volume|liquidity/OI|confidence|market impact|easy interpretation; score0; single-signal alert forbidden"
    if item not in s5:
        s5.append(item)
    CONTRACT.write_text(json.dumps(c, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def patch_guard():
    g = GUARD.read_text(encoding="utf-8")
    g = g.replace("'뉴스·경제일정·전문가·온체인 보조',", "'뉴스·경제일정·전문가·Polymarket',")
    stanley = "              'Stanley Druckenmiller',\n"
    if "              'POLYMARKET',\n" not in g:
        assert stanley in g, "guard Stanley token marker missing"
        g = g.replace(stanley, stanley + "              'POLYMARKET',\n              'TOP10',\n              'Δ1H',\n              'Δ4H',\n              'Δ1D',\n              'Δ7D',\n              'score weight 0',\n", 1)
    old = "for token in ['Coinness/News TOP5','economic calendar','Sean Farrell','Stanley Druckenmiller','STH-SOPR','STH Realized Price','STH-MVRV','Exchange Netflow']:"
    new = "for token in ['Coinness/News TOP5','economic calendar','Sean Farrell','Stanley Druckenmiller','Polymarket','TOP10','1H','4H','1D','7D','STH-SOPR','STH Realized Price','STH-MVRV','Exchange Netflow']:"
    if old in g: g = g.replace(old, new, 1)
    old_groups = "              'whale', 'derivatives', 'news_and_calendar', 'secondary_confirmation'\n"
    new_groups = "              'whale', 'derivatives', 'news_and_calendar', 'secondary_confirmation', 'prediction_markets'\n"
    if old_groups in g: g = g.replace(old_groups, new_groups, 1)
    if "POLYMARKET_TOP10_POLICY=PASS" not in g:
        marker = "          breadth = set(c['required_data']['market_breadth_rotation'])\n"
        assert marker in g, "guard insertion marker missing"
        block = '''          pm = c.get('polymarket_policy') or {}
          assert pm.get('enabled') is True, 'Polymarket policy must remain enabled'
          assert pm.get('top_n') == 10, 'Polymarket TOP10 lock regressed'
          assert pm.get('score_weight') == 0, 'Polymarket score weight must remain 0'
          assert pm.get('single_signal_alert_forbidden') is True, 'Polymarket single-signal alert ban regressed'
          assert pm.get('watch_requires_independent_aligned_confirmation') is True, 'Polymarket independent-confirmation guard regressed'
          if pm.get('change_windows') != ['1H','4H','1D','7D']:
              raise SystemExit('Polymarket change-window regression')
          pmd = set(c.get('required_data', {}).get('prediction_markets') or [])
          for token in ['Polymarket market-impact TOP10','YES probability current','probability-point change 1H/4H/1D/7D']:
              if token not in pmd:
                  raise SystemExit('Polymarket required-data regression: ' + token)
          if 'polymarket' not in (c.get('source_policy') or {}):
              raise SystemExit('Polymarket source policy missing')
          print('POLYMARKET_TOP10_POLICY=PASS')

'''
        g = g.replace(marker, block + marker, 1)
    GUARD.write_text(g, encoding="utf-8")


def main():
    patch_prompt(); patch_contract(); patch_guard()
    p = PROMPT.read_text(encoding="utf-8")
    c = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert "### 5) 🎯 POLYMARKET — 시장이 돈 걸고 보는 미래 TOP10" in p
    assert c["polymarket_policy"]["top_n"] == 10 and c["polymarket_policy"]["score_weight"] == 0
    print("MASTER_MARKET_POLYMARKET_PATCH=PASS")

if __name__ == "__main__":
    main()
