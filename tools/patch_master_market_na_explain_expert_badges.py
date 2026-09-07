#!/usr/bin/env python3
import json
from pathlib import Path

PROMPT = Path('master_prompts/master_market_v1_2_current.md')
CONTRACT = Path('state/master_market_v1_2_contract.json')

p = PROMPT.read_text(encoding='utf-8')

na_block = '''\n## N/A EXPLANATION & RECOVERY LOCK — ADDED 2026-09-07\n\nWhenever any user-visible value in SCREEN1~SCREEN5 is `N/A`, `확인 실패`, `확인 제한`, or equivalent unavailable state, the same screen MUST end with a compact `### N/A 안내` table before moving to the next screen.\n\nRequired columns = `N/A 항목 | 이유 | 자동해소 여부 | 예상 노출시점 / 필요조치`.\n\nRules:\n- Every unavailable item shown in that screen must be accounted for. Items with exactly the same cause may be grouped only if every affected item name is explicitly listed.\n- Classify the reason into one of these operational states:\n  1) `축적 대기` = the collector/history is working but not enough actual observations exist yet.\n  2) `원천 갱신 대기` = market close, reporting calendar, release timing, or upstream publication timing.\n  3) `일시 수집 실패` = source/API/parser/query/extraction failed this run but the current architecture can retry automatically.\n  4) `현재 구조상 불가` = the present collector/source/schema cannot produce the field; waiting alone will NOT solve it.\n- For `축적 대기`, show the earliest expected exposure time only from a known first-valid timestamp and required window/cadence. Example: 1D/3D/7D becomes eligible after 24h/72h/168h of actual comparable history. Never invent an ETA when the first-valid timestamp is unknown; instead print `기준점 부족 → 예정시각 계산 불가`.\n- For `원천 갱신 대기`, show the next known source/release time when confirmed; otherwise print `다음 원천 갱신 후`.\n- For `일시 수집 실패`, say `다음 자동수집/다음 OFFICIAL에서 재시도` and never promise that the value will definitely recover by then.\n- For `현재 구조상 불가`, explicitly print `시간을 기다려도 자동 노출 안 됨` and name the missing requirement, e.g. `새 collector/API/source/schema 필요`.\n- Do not convert N/A to 0, do not backfill, interpolate, reuse another venue/source, or copy a past value merely to remove N/A.\n- If a screen has no unavailable values, omit the `N/A 안내` table entirely.\n'''

if '## N/A EXPLANATION & RECOVERY LOCK — ADDED 2026-09-07' not in p:
    anchor = '## CHANGE-WINDOW LOCK — 1D / 3D / 7D\n'
    if anchor not in p:
        raise SystemExit('missing N/A insertion anchor')
    p = p.replace(anchor, na_block + '\n' + anchor, 1)

expert_rule = '''\n### EXPERT VIEW TRAFFIC-LIGHT DISPLAY LOCK — ADDED 2026-09-07\n- Sean Farrell and Stanley Druckenmiller blocks must each show a directional badge immediately beside the expert name/title. Allowed values: `🟢 상승우호` / `🔴 하락압력` / `🟡 중립·혼합` / `⚪ 최신관점 N/A`.\n- The badge represents the directional implication of the latest sufficiently current, directly verified public view for BTC/ETH/ALT or broad risk assets/liquidity. It is not a popularity/sentiment score.\n- If the latest direct view is too old or too context-specific to be treated as current-market guidance, use `⚪ 최신관점 N/A`, show the date of the last verified view, and explain why it is stale/insufficient.\n- If the view contains materially opposing implications, use `🟡 중립·혼합`.\n- Expert badges are display/context only. Sean Farrell and Stanley Druckenmiller remain score weight 0 and cannot directly change MASTER score, final 롱/숏, Risk Veto, or WATCH thresholds.\n'''
if '### EXPERT VIEW TRAFFIC-LIGHT DISPLAY LOCK — ADDED 2026-09-07' not in p:
    anchor = '### 3) 🧠 Sean Farrell 최신 관점 — score 0\n'
    if anchor not in p:
        raise SystemExit('missing expert insertion anchor')
    p = p.replace(anchor, expert_rule + '\n' + anchor, 1)

p = p.replace('### 3) 🧠 Sean Farrell 최신 관점 — score 0\n',
'''### 3) 🧠 Sean Farrell 최신 관점 — `[판정 신호등]` — score 0\nThe title MUST replace `[판정 신호등]` with exactly one of `🟢 상승우호` / `🔴 하락압력` / `🟡 중립·혼합` / `⚪ 최신관점 N/A`.\n''', 1)
p = p.replace('### 4) 🧠 Stanley Druckenmiller 최신 관점 — score 0\n',
'''### 4) 🧠 Stanley Druckenmiller 최신 관점 — `[판정 신호등]` — score 0\nThe title MUST replace `[판정 신호등]` with exactly one of `🟢 상승우호` / `🔴 하락압력` / `🟡 중립·혼합` / `⚪ 최신관점 N/A`.\n''', 1)

PROMPT.write_text(p, encoding='utf-8')

c = json.loads(CONTRACT.read_text(encoding='utf-8'))
c['schema_version'] = '1.5'
c['updated_kst'] = '2026-09-07T10:05:00+09:00'

c['na_explanation_policy'] = {
    'enabled': True,
    'applies_to_screens': [1,2,3,4,5],
    'trigger_values': ['N/A','확인 실패','확인 제한','equivalent unavailable state'],
    'section_title': 'N/A 안내',
    'columns': ['N/A 항목','이유','자동해소 여부','예상 노출시점 / 필요조치'],
    'reason_classes': {
        'ACCUMULATION_WAIT': '축적 대기',
        'SOURCE_UPDATE_WAIT': '원천 갱신 대기',
        'TEMPORARY_COLLECTION_FAILURE': '일시 수집 실패',
        'STRUCTURALLY_UNAVAILABLE': '현재 구조상 불가'
    },
    'accumulation_rule': 'ETA only from known first-valid timestamp + actual required window/cadence; 1D/3D/7D eligibility is 24h/72h/168h. If first-valid timestamp unknown, say 기준점 부족 → 예정시각 계산 불가.',
    'source_update_rule': 'Show next confirmed source/release time; otherwise 다음 원천 갱신 후.',
    'temporary_failure_rule': 'Say 다음 자동수집/다음 OFFICIAL에서 재시도; never guarantee recovery by that time.',
    'structural_rule': 'Explicitly say 시간을 기다려도 자동 노출 안 됨 and name the required new collector/API/source/schema.',
    'grouping_rule': 'Same-cause grouping allowed only when every affected unavailable item is explicitly named.',
    'anti_fabrication': True,
    'no_na_to_zero': True,
    'no_interpolation_backfill_cross_source_fill': True
}

c['expert_badge_policy'] = {
    'enabled': True,
    'experts': ['Sean Farrell','Stanley Druckenmiller'],
    'display_location': 'Immediately beside expert name/title in SCREEN5',
    'values': {
        'bullish': '🟢 상승우호',
        'bearish': '🔴 하락압력',
        'mixed_neutral': '🟡 중립·혼합',
        'stale_or_unavailable': '⚪ 최신관점 N/A'
    },
    'basis': 'Directional implication of latest sufficiently current directly verified public view for crypto/risk assets/liquidity.',
    'stale_rule': 'If too old or context-specific for current-market guidance, use ⚪ 최신관점 N/A, show last verified date, explain staleness/insufficiency.',
    'score_effect': 0,
    'direction_effect': 'NONE',
    'risk_veto_effect': 'NONE',
    'watch_threshold_effect': 'NONE'
}

ro = c.setdefault('required_output', {})
for s in ['screen1','screen2','screen3','screen4','screen5']:
    arr = ro.setdefault(s, [])
    note = 'if any N/A/unavailable value appears, append per-screen N/A 안내 with item|reason|automatic recovery yes/no|expected exposure time or required structural action'
    if note not in arr:
        arr.append(note)

s5 = ro.setdefault('screen5', [])
for item in [
    'Sean Farrell title includes one expert judgement badge: 🟢 상승우호/🔴 하락압력/🟡 중립·혼합/⚪ 최신관점 N/A; score0',
    'Stanley Druckenmiller title includes one expert judgement badge: 🟢 상승우호/🔴 하락압력/🟡 중립·혼합/⚪ 최신관점 N/A; score0'
]:
    if item not in s5:
        s5.append(item)

CONTRACT.write_text(json.dumps(c, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('MASTER_MARKET_NA_EXPERT_BADGE_PATCH=PASS')
