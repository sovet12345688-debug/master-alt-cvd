#!/usr/bin/env python3
"""Idempotent additive patch: show MASTER MARKET whale LONG/SHORT with traffic lights."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPT = ROOT / 'master_prompts/master_market_v1_2_current.md'
CONTRACT = ROOT / 'state/master_market_v1_2_contract.json'

BLOCK = r'''### WHALE SIDE TRAFFIC-LIGHT DISPLAY LOCK — ADDED 2026-09-06
- Every user-visible SCREEN4 whale table must display the current position side with a traffic-light prefix: `🟢 LONG` for long positions, `🔴 SHORT` for short positions, and `⚪ N/A/FLAT` when side is unknown, unavailable, or flat.
- This applies to `지금 움직인 고래 TOP3`, `BTC 핵심고래`, and `ETH 핵심고래`, including compressed continuation rows.
- Put the traffic light in the existing direction/size cell; do not add a redundant extra column unless layout requires it.
- The traffic light is a visual side label only. It does not mean the whole market is bullish/bearish, does not alter whale score, Market Positive Score, direction, Risk Veto, liquidation-risk labels, or WATCH thresholds.
- Existing status/risk markers such as 청산거리 경고 remain separate and must not be removed.
'''


def patch_prompt():
    p = PROMPT.read_text(encoding='utf-8')
    if '### WHALE SIDE TRAFFIC-LIGHT DISPLAY LOCK — ADDED 2026-09-06' not in p:
        marker = '## DERIVATIVES INTERPRETATION\n'
        assert marker in p, 'derivatives marker missing'
        p = p.replace(marker, BLOCK + '\n' + marker, 1)
    screen_marker = 'Whale table keeps actual confirmed fields only. Recommended compact columns = `# | 방향/규모 | 진입 | 배수 | 청산거리 | Δ1H | Δ4H | 상태`; ranks 6-10 may be compressed into a compact continuation table rather than deleted.\n'
    addition = 'Direction/size cells in all whale tables must render side as `🟢 LONG` or `🔴 SHORT`; unknown/flat side = `⚪ N/A/FLAT`. This is display-only and does not change scores or risk logic.\n'
    if addition not in p:
        assert screen_marker in p, 'SCREEN4 whale marker missing'
        p = p.replace(screen_marker, screen_marker + addition, 1)
    PROMPT.write_text(p, encoding='utf-8')


def patch_contract():
    c = json.loads(CONTRACT.read_text(encoding='utf-8'))
    c['schema_version'] = '1.5'
    c['updated_kst'] = '2026-09-06T16:04:00+09:00'
    c['whale_display_policy'] = {
        'enabled': True,
        'long_display': '🟢 LONG',
        'short_display': '🔴 SHORT',
        'unknown_flat_display': '⚪ N/A/FLAT',
        'applies_to': ['moving_whale_top3','btc_core_whales','eth_core_whales'],
        'cell_rule': 'Prefix the existing direction/size cell; no redundant extra column required.',
        'score_effect': 0,
        'direction_effect': 'NONE',
        'risk_veto_effect': 'NONE',
        'watch_threshold_effect': 'NONE',
        'preserve_liquidation_risk_markers': True
    }
    s4 = c.setdefault('required_output', {}).setdefault('screen4', [])
    item = 'whale direction/size cells: 🟢 LONG for long, 🔴 SHORT for short, ⚪ N/A/FLAT for unknown/flat; applies to moving TOP3 + BTC/ETH core whales; display-only, score effect 0'
    if item not in s4:
        s4.append(item)
    CONTRACT.write_text(json.dumps(c, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    patch_prompt(); patch_contract()
    p = PROMPT.read_text(encoding='utf-8')
    c = json.loads(CONTRACT.read_text(encoding='utf-8'))
    assert 'WHALE SIDE TRAFFIC-LIGHT DISPLAY LOCK' in p
    assert '🟢 LONG' in p and '🔴 SHORT' in p and '⚪ N/A/FLAT' in p
    wp = c['whale_display_policy']
    assert wp['enabled'] and wp['score_effect'] == 0
    print('MASTER_MARKET_WHALE_SIDE_DISPLAY_PATCH=PASS')

if __name__ == '__main__':
    main()
