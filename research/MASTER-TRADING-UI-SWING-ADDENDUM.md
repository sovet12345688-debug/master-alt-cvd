# MASTER TRADING — SWING UI ADDENDUM

Status: RESEARCH / REVIEW BRANCH ONLY
Purpose: preserve the existing locked Screens 1~6 exactly and add one new user-visible Swing Trading table screen.

## 0. HARD RULE
- Existing MASTER-TRADING-UI-V2-FINAL Screens 1~6 remain unchanged in field order, labels, formulas, logic and rendering.
- This addendum is additive-only.
- No existing Daily Entry/SL/TP/R:R may be moved, recalculated or reinterpreted because Swing exists.
- Swing uses separate setup IDs, Trade Frames, Entry/SL/TP/R:R and Time Validity.

# SCREEN 7 — SWING LONG / SHORT 타점

Purpose: show the best current multi-day LONG and SHORT swing setups in one compact mobile table so Daily and Swing horizons are not confused.

| 항목 | SWING LONG | SWING SHORT |
|---|---|---|
| 상태 | ENTER / SMALL ENTER / WATCH / WAIT / AVOID | ENTER / SMALL ENTER / WATCH / WAIT / AVOID |
| 기준 TF | 4H / 1D / 1W | 4H / 1D / 1W |
| 예상 보유기간 | 약 x일~x일 | 약 x일~x일 |
| 진입 구간 | 작은값~큰값 | 큰값~작은값 |
| 1차 | x | x |
| 2차 | x | x |
| 평균가 | x | x |
| Trigger | 어느 가격 / 어떤 1H·4H 완성봉 / 어디에 마감 | 어느 가격 / 어떤 1H·4H 완성봉 / 어디에 마감 |
| SL | 구조적 무효화 가격 | 구조적 무효화 가격 |
| TP1 | x | x |
| TP2 | x | x |
| TP3 | x | x |
| R:R | TP1 / TP2 / TP3 | TP1 / TP2 / TP3 |
| 핵심 무효화 | 한 줄 | 한 줄 |
| Time Validity | 절대 KST 또는 N/A·재검증 필요 | 절대 KST 또는 N/A·재검증 필요 |
| 핵심 근거 | 1W/1D/4H 구조·수급·위치 요약 | 1W/1D/4H 구조·수급·위치 요약 |
| Action | 한 줄 | 한 줄 |

### SCREEN 7 HARD RULES
1. LONG 진입구간은 작은값→큰값, SHORT는 큰값→작은값.
2. Swing Trade Frame은 Daily Trade Frame과 독립적으로 Lock한다.
3. 1H/4H completed Trigger가 필요한 setup이면 진행봉을 PASS로 쓰지 않는다.
4. 15m/30m은 Swing thesis를 만드는 프레임이 아니라 실행 refinement only.
5. Structural SL이 불명확하거나 realistic core R:R < 3이면 ENTER 금지.
6. Daily setup이 실패했다고 Swing setup으로 승격/연장하지 않는다.
7. Optional data N/A alone is not a veto; execution-critical fact N/A is WAIT.
8. Existing Screens 3/4의 Daily 타점과 Screen 7 Swing 타점은 별도 setup으로 표시한다.

### SCREEN 7 핵심 요약
`SWING LONG: ___ / SWING SHORT: ___ / 현재 실제 Swing ENTER 가능: ___ / 가장 먼저 필요한 Trigger: ___`

## FOOTER / FOLLOW-UP
- Existing follow-up and footer contract remains unchanged.
- Screen 7 appears before the 5 follow-up questions and footer.
- Nothing is written after the footer.

This addendum becomes Production UI only after explicit final approval/cutover.