# YOUTUBER VIEW — EXISTING RULES CANONICALIZATION

Status: V1 RULES CANONICALIZED FOR WORK MIGRATION
Purpose: preserve the existing 유튜버 관점방 rules durably so chat migration cannot erase them.
UI status: CURRENT ROOM VISIBLE UI FROZEN — NO REDESIGN AUTHORIZED.

## 1. ROLE
Track each YouTuber's market view before the outcome is known, preserve the forecast with a unique Forecast ID, and evaluate it later for accuracy and lead quality.

This system is independent. It must not read or depend on ALT 1, ALT 2, MARKET, BTC TREND, TRADING, or any other MONEY system's score/state/direction/permission/history.

## 2. FORECAST RECORD — PROSPECTIVE ONLY
Canonical record fields:
`Forecast ID | 게시시각 | FirstSeen | 시간대 | 방향 | 핵심 가격대 | Trigger | Invalidation | 예상경로 | 평가기한 | Status`

Allowed primary statuses:
- `OPEN`
- `PARTIAL HIT`
- `HIT`
- `INVALIDATED`
- `EXPIRED`

Rules:
- Record the view when it is first observed.
- Never create an old forecast retrospectively after seeing the market outcome.
- Never alter an old forecast to make it fit later price action.
- When the evaluation period is not finished, official accuracy scoring remains pending.
- Actual market outcome must be evaluated from subsequently observed data only.

## 3. ACCURACY /100
Official Accuracy components:
- 방향 적중: 35
- 핵심 가격대 반응: 25
- 예상 경로 일치: 20
- 타이밍 적합: 10
- 무효화/리스크 적절성: 10

Total = 100.
Official score is calculated only after the forecast's evaluation period is complete or the forecast is objectively resolved earlier by its own rules.

## 4. LEAD /100
Lead quality measures whether the YouTuber was useful before the crowd/move rather than merely correct afterward.

Components:
- 움직임 전 선행성: 40
- 비추격/저이격: 20
- Trigger 명확성: 20
- 좋은 진입위치 제시: 20

Total = 100.

## 5. RELIABILITY
`Reliability = Accuracy 70% + Lead 30%`.

Sample-size interpretation:
- `N < 5`: 표본 부족
- `N = 5~9`: 잠정 신뢰도
- `N >= 10`: 의미 있는 트랙레코드로 검토 가능

Do not claim a definitive best YouTuber from an insufficient sample.

## 6. CHART / INPUT HANDLING
The user may upload YouTuber screenshots, chart images, written summaries, or video-viewpoint captures manually.

Rules:
- Use only what is actually visible/confirmed in the supplied material or independently verified source.
- Unreadable prices/text are not guessed.
- When a chart image is supplied, interpret it for this system only.
- The image/derived viewpoint must not be exposed to or reused by another MONEY system.

## 7. COUNTER-ANALYSIS
The room may provide current-data-based counterevidence to the YouTuber's view for user decision support.
Counter-analysis must remain separate from the stored forecast and must never rewrite the original forecast record.

## 8. DURABLE HISTORY
Work migration storage is system-local only:
- `runtime/history/forecasts/`: append-only forecasts
- `runtime/history/outcomes/`: later outcome evaluations
- `runtime/latest/`: latest current room state
- `runtime/inputs/`: source/image metadata
- `runtime/artifacts/`: scorecards and summaries

No shared MONEY history or state is allowed.
No backfill from memory is allowed when a historical forecast was not actually recorded.

## 9. UI FREEZE
This file does not define a new visible layout.
The current user-visible YouTuber room structure, wording, ordering and presentation remain unchanged during Work migration.
Only persistence, bootstrap stability and history durability are being upgraded.

## 10. FOLLOW-UP BEHAVIOR
Preserve the existing room's five follow-up suggestions/questions behavior. One applicable follow-up should continue to offer an easy infographic image when contextually useful.
