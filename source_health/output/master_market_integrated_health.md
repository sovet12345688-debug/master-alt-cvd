# MASTER MARKET 통합 건강판

- 평가시각(UTC): `2026-09-18T17:16:04.138602Z`
- 종합상태: 🔴 **FAILED**
- 상태 기준: 🟢 CURRENT OK / 🟡 HISTORY RECOVERING / 🟠 STALE / 🔴 FAILED
- 이 건강판은 운영 진단용이며 MASTER MARKET 점수·롱/숏·WATCH 판단을 변경하지 않습니다.

| 영역 | 상태 | 데이터 나이(분) | History | 핵심 원인 | 다음 조치 |
|---|---|---:|---|---|---|
| Data Vault | 🟢 CURRENT OK | 17.4 | OK | OK | 조치 없음 |
| Stablecoin Windows | 🟡 HISTORY RECOVERING | 17.4 | PARTIAL_WINDOWS | CURRENT_OK_COMPARISON_HISTORY_INCOMPLETE | 현재 공급량은 사용; 누락 비교창은 실제 history 축적 대기 |
| Derivatives | 🟢 CURRENT OK | 58.6 | OK | OK | 조치 없음 |
| Actor Retail | 🟢 CURRENT OK | 17.4 | OK | OK | 조치 없음 |
| Yen Carry | 🟢 CURRENT OK | 1059.1 | OK | OK | 조치 없음 |

## 연관 MARKET Source Health 경보

| Source | 영향도 | 상태 | 원인 | Workflow |
|---|---|---|---|---|
| MARKET Macro Backbone | CORE | 🔴 FAILED | QUALITY_CHECK_FAILED, LATEST_WORKFLOW_FAILED | market_macro_liquidity_free_hourly.yml |
| MARKET Treasury Buyback | OPTIONAL | 🔴 FAILED | LATEST_WORKFLOW_FAILED | market_macro_liquidity_free_hourly.yml |
