# MASTER-TRADING-UI-V2-FINAL

**Status:** FINAL LOCK  
**Scope:** USER-VISIBLE OUTPUT UI ONLY  
**Locked at:** 2026-09-07 14:56 KST  
**Applies to:** MASTER TRADING manual/on-demand reports for BTC, ETH, and any other supported crypto asset  
**Canonical parent:** `master_prompts/master_trading_current.md`  
**Engine version:** `CURRENT + TIME VALIDITY V2.1 OVERLAY`

---

## 0. NON-DESTRUCTIVE UI-ONLY HARD RULE

This file changes **presentation only**.

The following are immutable and MUST NOT be changed by this UI patch:

- market-direction logic
- Weekly / Daily / Hourly structure
- Main Scenario Engine V3.2 logic/weights
- existing Entry Zone derivation
- Structural SL
- TP1 / TP2 / TP3
- R:R calculation
- Trade Frame Lock
- 2-STAGE ENTRY
- SMALL ENTER → ADD logic
- 15m/30m closed-candle Trigger definition
- Non-Chasing
- Risk Veto
- R:R >= 3:1 execution gate
- TIME VALIDITY V2.1 internal logic
- Wave Energy = context-only
- Fibonacci Time = OFF
- all historical/Legacy records and attached source history

**UI must never create, move, overwrite, or reinterpret Entry / SL / TP / S&R / Trigger / Trade Frame / historical records.**

If this UI spec conflicts with the analytical/execution canonical, the analytical/execution canonical controls calculations and this file controls presentation only.

---

## 1. GLOBAL MOBILE OUTPUT RULES

1. Mobile-first readability is the highest presentation priority.
2. Default output is exactly **5 visual screens**, in the fixed order below.
3. Each screen uses a compact table first, followed by **one concise 핵심 요약** line.
4. Long trigger explanations, conditional prose, and repetitive reasons are minimized.
5. Output is result-first. Supporting detail is shown only when execution-relevant.
6. BTC and ETH MUST use the same screen order, same table structure, same labels, and same UI logic.
7. Do not change the screen order, field order, labels, or layout without explicit user approval.
8. Missing/unverified values are shown as `N/A`; never fabricate.
9. Live/in-progress candles remain PROVISIONAL internally and cannot be displayed as confirmed Trigger facts.
10. The existing analytical engine remains the only source of calculated trading values.

---

# SCREEN 1 — 파동 시나리오

## 1-1. Default mobile table

| 항목 | 장기 대파동 | 중기 중파동 | 단기 소파동 |
|---|---|---|---|
| 기준 TF | 1W~1D | 4H~1D | 15m~1H |
| 현재 상태 | 엔진 판정 | 엔진 판정 | 엔진 판정 |
| 우세 방향 | 상승/조정/하락 | 상승/조정/하락 | 상승/조정/하락/수렴 |
| 시나리오 확률 | 약 XX% | 약 XX% | 약 XX% |
| BEST 시나리오 | 한 줄 | 한 줄 | 한 줄 |
| 핵심 경로 | 핵심 가격대만 | 핵심 가격대만 | 핵심 가격대만 |
| 무효화 가격 | 장기 별도 | 중기 별도 | 단기 별도 |

### SCREEN 1 핵심 요약
`장기 ___ / 중기 ___ / 단기 ___ → 현재 가장 확률 높은 전체 파동 시나리오: ______`

### 1-2. 파동 인포그래픽 출력 규칙
사용자가 인포그래픽 출력 또는 후속질문 5번을 요청한 경우, **SCREEN 1에 파동 시나리오 인포그래픽을 반드시 포함**한다.

고정 구조:
1. 상단 제목 + 현재가
2. `가장 확률 높은 시나리오` 배너
3. `① 장기프레임 대파동`
4. `② 중기프레임 중파동`
5. `③ 단기프레임 소파동`
6. 하단 `한눈에 결론`
7. 장기/중기/단기 각각 확률과 무효화 가격 표시

사용자가 제공한 BTC 파동 시나리오 이미지의 **레이아웃 구조, 정보 위계, 카드 구성, 방향 화살표, 장기/중기/단기 3단 구성**을 고정 레퍼런스로 사용한다.

단, 새 실행에서는 자산명·현재가·파동 번호·핵심 가격·확률·무효화 가격을 최신 분석값으로 교체한다. 과거 가격을 복사하지 않는다.

---

# SCREEN 2 — 차트분석 SCORE

목적: 현재 타점 제시가 충분한 근거 위에 있는지 한 화면에서 검증.

**기존 MASTER TRADING Main Scenario Engine V3.2의 8개 축/가중치를 그대로 표시하며, UI 때문에 산식을 변경하거나 새 점수를 만들지 않는다.**

| 분석축 | 최대점수 | 신호등 | 쉬운 해석 | 현재 SCORE |
|---|---:|:---:|---|---:|
| 가격구조·추세 | 20 | 🟢/🟡/🔴 | 한 줄 | x/20 |
| 거래량·참여 | 13 | 🟢/🟡/🔴 | 한 줄 | x/13 |
| 위치·S/R·이격 | 12 | 🟢/🟡/🔴 | 한 줄 | x/12 |
| 파생·수급 | 13 | 🟢/🟡/🔴 | 한 줄 | x/13 |
| 매크로·시장 | 10 | 🟢/🟡/🔴 | 한 줄 | x/10 |
| Elliott/Fib | 12 | 🟢/🟡/🔴 | 한 줄 | x/12 |
| 캔들·가격행동 | 12 | 🟢/🟡/🔴 | 한 줄 | x/12 |
| 상대강도·시장폭 | 8 | 🟢/🟡/🔴 | 한 줄 | x/8 |
| **합계** | **100** |  |  | **xx/100** |

### 최종 방향/타점 근거
| LONG 우위 | SHORT 우위 | Entry Quality | 결론 |
|---:|---:|---:|---|
| xx | xx | xx/100 | LONG / SHORT / WAIT |

- `Direction Score`와 `Entry Quality Score`는 계속 분리한다.
- 화면의 SCORE는 기존 엔진 산출값/정규화값을 표현할 뿐, UI 전용 재계산을 금지한다.
- Coverage 부족 또는 필수 데이터 불명확 시 강한 확신 표기를 금지한다.

### SCREEN 2 핵심 요약
`현재는 ___ 우위. 다만 실제 타점 실행 품질은 ___/100 → ______.`

---

# SCREEN 3 — LONG 타점 3개

가로축은 반드시:
`단기 | ⭐ BEST | 최대 마지노선`

세로축은 반드시 아래 순서:

| 항목 | 단기 | ⭐ BEST | 최대 마지노선 |
|---|---|---|---|
| 타임프레임 | 1H/4H/1D/1W | 1H/4H/1D/1W | 1H/4H/1D/1W |
| 진입 구간 | x~x | x~x | x~x |
| 1차 | x | x | x |
| 2차 | x | x | x |
| 평균가 | x | x | x |
| SL | x | x | x |
| TP1 | x | x | x |
| TP2 | x | x | x |
| TP3 | x | x | x |
| R:R | TP1/TP2/TP3 | TP1/TP2/TP3 | TP1/TP2/TP3 |
| 도달확률 | 약 xx% | 약 xx% | 약 xx% |
| 시간 유효성 | `MM/DD HH:mm KST까지` | `MM/DD HH:mm KST까지` | `MM/DD HH:mm KST까지` |
| 핵심 근거 | 한 줄 | 한 줄 | 한 줄 |

### 반드시 표 아래 한 줄
`이 LONG 타점이 노리는 파동: ______`

### SCREEN 3 핵심 요약
`LONG 우선순위: ______ / 현재 즉시 ENTER 가능: O개 또는 0개.`

---

# SCREEN 4 — SHORT 타점 3개

가로축은 반드시:
`단기 | ⭐ BEST | 최대 마지노선`

세로축은 반드시 아래 순서:

| 항목 | 단기 | ⭐ BEST | 최대 마지노선 |
|---|---|---|---|
| 타임프레임 | 1H/4H/1D/1W | 1H/4H/1D/1W | 1H/4H/1D/1W |
| 진입 구간 | x~x | x~x | x~x |
| 1차 | x | x | x |
| 2차 | x | x | x |
| 평균가 | x | x | x |
| SL | x | x | x |
| TP1 | x | x | x |
| TP2 | x | x | x |
| TP3 | x | x | x |
| R:R | TP1/TP2/TP3 | TP1/TP2/TP3 | TP1/TP2/TP3 |
| 도달확률 | 약 xx% | 약 xx% | 약 xx% |
| 시간 유효성 | `MM/DD HH:mm KST까지` | `MM/DD HH:mm KST까지` | `MM/DD HH:mm KST까지` |
| 핵심 근거 | 한 줄 | 한 줄 | 한 줄 |

### 반드시 표 아래 한 줄
`이 SHORT 타점이 노리는 파동: ______`

### SCREEN 4 핵심 요약
`SHORT 우선순위: ______ / 현재 즉시 ENTER 가능: O개 또는 0개.`

---

## SCREEN 3/4 공통 — `시간 유효성` 사용자 표시 정의

사용자가 원하는 `시간 유효성`은 내부 상태명 `신선 유효 / 유효`가 아니다.

**반드시 “이 신규 타점 주문을 현재 구조 기준으로 몇 시까지 체결 대상으로 둘 수 있는가”를 절대시각으로 표시한다.**

표시 형식:
- 기본: `MM/DD HH:mm KST까지`
- 같은 날짜가 명백한 좁은 화면에서는 `HH:mm KST까지` 허용
- 정확한 최대 체결 허용 시각을 검증할 수 없으면 `N/A · 재검증 필요`

TIME VALIDITY V2.1과의 관계:
- 이것은 고정 4H/8H/12H Universal TTL이 아니다.
- 표시 시각까지는 현재 LastValidated 구조를 기준으로 신규 체결 후보로 유지한다.
- 표시 시각이 지나면 **자동 가격 무효화가 아니라 신규 체결을 일시 중지하고 재검증**한다.
- 재검증 PASS 시 새 최대 체결 허용 시각을 갱신할 수 있다.
- 구조적 가격 무효화/R:R 붕괴/Risk Veto는 시간과 별도로 즉시 적용한다.
- TIME UI가 Entry / SL / TP / S&R을 이동시키는 것은 금지한다.

`다음 재검증`, `신선 유효`, `유효` 같은 내부 운영 라벨은 기본 타점표에서 제거한다.

---

## SCREEN 3/4 공통 — 도달확률 정의

- 사용자-visible 필드명은 `도달확률`.
- 가능하면 `%`로 표시한다.
- 이는 해당 Entry Zone까지 가격이 도달할 가능성에 대한 **현재 구조 기반 추정치**이며, 보장된 통계확률로 표현하지 않는다.
- 충분한 근거가 없으면 숫자를 만들지 않고 `N/A` 처리한다.
- 도달확률은 Entry Quality나 실제 ENTER Gate를 대체하지 않는다.

---

# SCREEN 5 — 파생 데이터 + 파동 시나리오 최종 조합 결론

## 5-1. 결합 판단 테이블

| 축 | 현재 판정 | LONG 영향 | SHORT 영향 |
|---|---|---:|---:|
| 파동 시나리오 | 한 줄 | +/0/- | +/0/- |
| 가격구조 | 한 줄 | +/0/- | +/0/- |
| 거래량·참여 | 한 줄 | +/0/- | +/0/- |
| OI/Funding | 한 줄 | +/0/- | +/0/- |
| CVD/Taker | 한 줄 | +/0/- | +/0/- |
| Non-Chasing | PASS/FAIL | +/0/- | +/0/- |
| Risk Veto | 없음/있음 | +/0/- | +/0/- |
| TIME Validity | 체결 가능/재검증 | +/0/- | +/0/- |

## 5-2. 현재 시점 BEST 시나리오
`________________________________`

## 5-3. 지금 해야 할 행동
`ENTER / SMALL ENTER / WATCH / WAIT / AVOID`

필요하면 LONG/SHORT 중 하나를 우선 표시하되, Trigger나 실제 실행 Gate가 미완성이면 방향 우위와 실행을 분리해 `WAIT`로 표시한다.

### SCREEN 5 핵심 요약
`현재 최적 행동: ______. 가장 좋은 타점: ______. 추격: 가능/금지.`

---

# FOOTER — 고정

모든 정상 MASTER TRADING 출력은 아래 한 줄 형식을 사용한다.

`MASTER TRADING | 실행 완료 시간 : YYYY-MM-DD HH:mm KST | 다음 정식 보고 시간 : 수동 실행`

MASTER TRADING recurring automation은 현재 OFF이므로 실제 예약시간을 임의 생성하지 않는다.

---

# FOLLOW-UP QUESTIONS — 정확히 5개

정상 MASTER TRADING 출력은 Footer 직전에 정확히 5개 후속 질문/제안을 표시한다.

다음 2개는 항상 포함:
1. `누적 트레이딩 복기해줄까?`
2. `인포그래픽을 생성해줄까?`

3~4번은 현재 시장/타점에 맞게 짧게 생성한다.

5번은 아래 문구를 **고정 문구로 반드시 사용**한다.

5. `스크린2 파동분석을 첨부한 차트 기반으로 엘리엇파동을 숫자(1~5, A-B-C) 직접 표시한 버전으로 더 직관적으로 그려줄까?`

사용자가 `5`라고 답하면:
- 첨부된 차트 자체를 기반으로 파동 숫자 `1~5`, 조정 `A-B-C`를 직접 표시한 이미지를 생성한다.
- 사용자 레퍼런스 이미지의 고정된 인포그래픽 구성/레이아웃을 지속 사용한다.
- 장기/중기/단기 각각 확률과 무효화 가격을 표시한다.
- 최신 차트값을 사용하며 오래된 가격/파동 숫자를 그대로 재사용하지 않는다.
- 이미지 생성은 분석 엔진을 변경하지 않는다.

---

# ASSET UI PARITY HARD LOCK

BTC / ETH / 기타 지원 자산 모두 동일 UI를 적용한다.

금지:
- BTC는 5 SCREEN인데 ETH는 축약형으로 출력
- 자산별로 타점표 열/행 순서 변경
- 도달확률 누락
- 시간 유효성 누락
- 파동 시나리오 누락
- 사용자 승인 없이 화면을 4 SCREEN 또는 다른 구조로 되돌림

---

# QUICK COMMAND `고`

차트가 첨부된 상태에서 사용자가 `고`를 입력하면:
- 기존 MASTER TRADING 분석 엔진을 그대로 실행한다.
- 출력은 반드시 이 `MASTER-TRADING-UI-V2-FINAL`의 SCREEN 1 → 5 순서를 따른다.
- BTC/ETH 동일하다.
- 화면을 줄인다는 이유로 필수 Screen/타점 필드를 생략하지 않는다.
- 데이터 부족은 `N/A`; 임의 추정값 생성 금지.

---

# CHANGE CONTROL

이 UI는 **FINAL LOCK**이다.

향후 변경 조건:
- 사용자의 명시적 화면/UI 수정 요청이 있을 때만 변경한다.
- 모델이 보기 좋다는 이유로 임의 변경 금지.
- 엔진 개선/연구 결과가 나와도 UI를 자동 변경하지 않는다.
- UI 변경이 분석 엔진/가격 엔진을 수정하는 것으로 해석되어서는 안 된다.

Any future UI revision must record:
`UI_VERSION | APPROVED_KST | CHANGED_SCREENS | ENGINE_CHANGE=false`

---

# FINAL ACCEPTANCE CHECK

- [x] 5 SCREEN 순서 고정
- [x] 모바일 우선
- [x] Screen 1 장기/중기/단기 파동 + 확률 + 개별 무효화
- [x] Screen 2 기존 V3.2 100점 분석축 표시
- [x] Screen 3 LONG 세로형 3타점
- [x] Screen 4 SHORT 세로형 3타점
- [x] 도달확률 고정
- [x] 시간 유효성 = 최대 신규 체결 허용시각
- [x] 타점별 노리는 파동 한 줄
- [x] Screen 5 파생 + 파동 조합 결론 + 행동
- [x] BTC/ETH UI 동일
- [x] 후속 질문 정확히 5개
- [x] 후속 5번 Elliott 숫자/A-B-C 이미지 문구 고정
- [x] 기존 Entry/SL/TP/R:R/Trigger/Trade Frame/2-STAGE/TIME V2.1 엔진 무변경

**FINAL LOCK: `MASTER-TRADING-UI-V2-FINAL`**
