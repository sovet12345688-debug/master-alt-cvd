# ALT2 LIVE LARGE FLOW V1

목적: 기존 완료 일별 CVD(26W/13W/7W/4W)를 그대로 보존하면서, Binance Spot 공개 `aggTrades` API로 **완료된 시간봉 단위** Large Flow를 별도로 누적해 `1H/4H/24H`를 계산한다.

## 역할 분리
- 기존 `whale_cvd_collector.py`: 장기 방향 26W/13W/7W/4W, 완료 daily archive 기반.
- `live_flow/collector.py`: 단기 속도 1H/4H/24H, 완료 UTC hour 기반.
- 두 엔진은 서로의 raw/history/state를 덮어쓰지 않는다.

## 동일한 체결 분류
- R1 < $1K
- R2 $1K~<10K
- M $10K~<100K
- W $100K~<1M
- MW >=$1M
- Large=W+MW, Retail=R1+R2
- `m=false` aggressive buy, `m=true` aggressive sell
- Normalized CVD=(Buy$-Sell$)/(Buy$+Sell$)*100

## 데이터 규칙
1. Binance Spot 단일 venue. 다른 거래소로 빈 구간을 채우지 않는다.
2. 진행 중인 시간은 사용하지 않고 직전 **완료된 UTC hour**만 저장한다.
3. API 실패는 0으로 저장하지 않는다.
4. Spot pair 미지원은 `UNSUPPORTED`.
5. 4H/24H는 필요한 완료 hour가 전부 쌓일 때만 실제 값으로 생성한다. 그 전에는 BUILDING_HISTORY 계열 상태.
6. Large trade는 지갑/고래 신원 확인이 아니라 체결규모 기반 big-money proxy다.
7. Live Flow는 확인/반대근거 전용이다. ALT2 Core Score, CVD 장기점수, ENTER Gate를 우회하지 않는다.

## 출력
- `data/hourly_large_flow_bins.csv`: 시간별 R1/R2/M/W/MW buy/sell 집계. raw trade 저장 안 함.
- `output/live_large_flow_summary.json`
- `output/live_large_flow_summary.csv`
- `state/collector_state.json`

## 핵심 읽는 법
- 장기 26W→13W→7W→4W 개선 + 단기 24H→4H→1H 매수 강화 + 가격 Non-Chase = 집중관찰 가치 상승.
- 1H 양수 하나만으로 매수 판단 금지.
- 가격 급등 중 1H Large Flow 약화/음전은 추격 경고 근거가 될 수 있음.
- 실제 ENTER는 기존 ALT2의 Current/Entry/15m·30m Trigger/SL/TP/R:R>=3/Risk Veto 규칙이 그대로 우선.

## 운영 제안
Production 전용 GitHub Actions는 매시간 `:27` 실행을 권장한다. GitHub Actions 지연 가능성이 있으므로 15m Trigger 엔진이 아니라 1H Flow 분석용이다.
