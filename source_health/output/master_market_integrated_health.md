# MASTER MARKET 통합 건강판

- 평가시각(UTC): `2026-09-10T01:38:37.021200Z`
- 종합상태: 🔴 **FAILED**
- 상태 기준: 🟢 CURRENT OK / 🟡 HISTORY RECOVERING / 🟠 STALE / 🔴 FAILED
- 이 건강판은 운영 진단용이며 MASTER MARKET 점수·롱/숏·WATCH 판단을 변경하지 않습니다.

| 영역 | 상태 | 데이터 나이(분) | History | 핵심 원인 | 다음 조치 |
|---|---|---:|---|---|---|
| Data Vault | 🟡 HISTORY RECOVERING | 34.8 | DEGRADED_HISTORY | CURRENT_OK_HISTORY_INCOMPLETE | 현재값은 사용; 실제 동일원천 history 자동 축적 대기, backfill 금지 |
| Stablecoin Windows | 🟡 HISTORY RECOVERING | 34.8 | PARTIAL_WINDOWS | CURRENT_OK_COMPARISON_HISTORY_INCOMPLETE | 현재 공급량은 사용; 누락 비교창은 실제 history 축적 대기 |
| Derivatives | 🟡 HISTORY RECOVERING | 21.3 | RECOVERING | CURRENT_OK_EXACT_WINDOWS_INCOMPLETE | 현재 파생값은 사용; exact 1H/4H/24H history 자동 축적 대기 |
| Actor Retail | 🟡 HISTORY RECOVERING | 34.8 | PARTIAL_WINDOWS | CURRENT_OK_COMPARISON_HISTORY_INCOMPLETE | 현재 Retail proxy는 사용; 누락 1D/3D/7D는 동일원천 history 축적 대기 |
| Yen Carry | 🟢 CURRENT OK | 31.4 | OK | OK | 조치 없음 |

## 연관 MARKET Source Health 경보

| Source | 영향도 | 상태 | 원인 | Workflow |
|---|---|---|---|---|
| MARKET Macro Liquidity | CORE | 🔴 FAILED | QUALITY_CHECK_FAILED, LATEST_WORKFLOW_FAILED | market_macro_liquidity_free_hourly.yml |
| US Net Liquidity Proxy | OPTIONAL | 🔴 FAILED | STATE_FILE_MISSING, WORKFLOW_RUNNING | net_liquidity_hourly.yml |
