# MASTER ALT V2.1 — FREE LARGE-TRADE SPOT CVD

## 한 줄 요약
Binance Spot 무료 공개 `aggTrades`로 FINAL20의 **대형체결 vs 소형체결 CVD**를 직접 만들고,
`26W → 13W → 7W → 4W` 흐름과 가격/RS를 함께 계산합니다.

## 중요한 정의
이 데이터는 **고래 지갑 추적이 아닙니다.**
- R1: < $1K
- R2: $1K ~ < $10K
- M: $10K ~ < $100K
- W: $100K ~ < $1M
- MW: >= $1M
- Large = W + MW
- Retail = R1 + R2

`m=false` = aggressive buy, `m=true` = aggressive sell.

Normalized CVD:
`(Aggressive Buy$ - Aggressive Sell$) / (Aggressive Buy$ + Aggressive Sell$) * 100`

## 최초 1회
```bash
pip install -r requirements.txt
python whale_cvd_collector.py --mode bootstrap --lookback-days 182
```

원시 ZIP은 임시로만 받고 바로 삭제합니다. 저장되는 것은 일별 Bin 합계뿐입니다.

## 이후 업데이트
```bash
python whale_cvd_collector.py --mode update
```

## 출력
- `output/coverage.csv`: 종목별 26/13/7/4W CVD coverage
- `output/final20_cvd_summary.csv`
- `output/final20_cvd_summary.json`
- `output/large_trade_cvd_top5.csv`
- `data/daily_cvd_bins.csv`: compact daily aggregated history
- `state/collector_state.json`: incremental state

## GitHub Actions — PC 없이
`.github/workflows/cvd_update.yml` 포함.
1. 이 폴더를 GitHub 저장소에 올림.
2. 첫 실행에서 baseline 파일이 없으면 **자동으로 26W bootstrap**.
3. 이후에는 새로 공개된 완료 일별 archive만 incremental update.
4. 최신 archive가 아직 공개되지 않았으면 상태를 넘기지 않고 다음 실행에서 재시도.
5. 하루 4회 확인하도록 설정되어 있지만, 새 daily archive가 없으면 계산값은 변하지 않음.
6. ChatGPT가 GitHub 저장소의 `output/final20_cvd_summary.json`을 읽으면 V2.1 분석에 바로 연결 가능.

### 왜 매시간 전체 26W를 다시 계산하지 않나?
26W CVD는 long-window 데이터라 **완료 일별 archive를 누적**하는 것이 비용/안정성 면에서 최적입니다.
V2.1의 매시간 WATCH는 가격/구조를 계속 보고, CVD는 마지막 완료 일 데이터 기준으로 사용합니다.
필요하면 별도 `live-tail` 모듈을 추가해 최근 aggTrades만 시간별로 덧붙일 수 있습니다.

## GitHub 비용 주의
GitHub Actions 무료 제공량은 계정/저장소 유형에 따라 다릅니다. 대형 26W bootstrap은 시간이 오래 걸릴 수 있습니다.
가장 안전한 운영은:
- 최초 bootstrap: 한 번
- 이후 daily incremental: 매우 가벼움
- raw ZIP 저장 금지

## 데이터 현실성 규칙
- Binance Spot pair 없으면 CVD 필드 숨김.
- Coverage < 70%면 CVD Score로 PRE-RUNNER/ENTER 승격 금지.
- 다른 거래소 데이터를 섞어 빈 구간을 메우지 않음.
- 대형체결을 특정 고래/기관 지갑으로 단정하지 않음.
- CVD가 강해도 Entry/15m·30m Trigger/SL/TP/R:R>=3 없으면 ENTER 금지.
