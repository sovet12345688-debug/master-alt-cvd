# MASTER TRADING V3 SHADOW BACKTEST REPORT

**최종판정: V3 FAIL — KEEP CURRENT BASELINE**

## 방법
- Binance 30m Spot + USD-M Futures 공개 월별 원천데이터
- 2023 Train / 2024 Validation / 2025 OOS
- 4H Regime → 1H Setup → 30m 완성봉 Trigger; 1D Context
- Current MASTER는 수동/재량 시스템이므로 Baseline은 최신 Canonical 명시 규칙의 deterministic proxy이며 과거 채팅 의사결정을 재구성하지 않음.
- OI/Depth/가격별 청산맵/온체인 등 장기 point-in-time 복원 불가능 축은 N/A; 0으로 대체하지 않음.

## OOS 핵심 비교
- CURRENT BASELINE proxy: n=272, Exp=0.055R, PF=1.07, MaxDD=-25.3R, FalseStart=36.8%
- V3 CORE - FIB: n=54, Exp=-0.095R, PF=0.87, MaxDD=-17.0R, FalseStart=31.5%
- V3 CORE + FIB: n=56, Exp=-0.338R, PF=0.56, MaxDD=-22.0R, FalseStart=33.9%
- 선택 Score threshold: 70; ATR buffer: 0.2 × 1H ATR

## 판정 체크
- primary_exp_pf: FAIL
- maxdd_ok: PASS
- false_start_ok: PASS
- asset_stability: FAIL
- fib_ablation_ok: FAIL
- bootstrap_not_materially_negative: FAIL

## 데이터 한계
- 역사적 OI는 Binance 공식 API가 최근 약 1개월만 제공하므로 장기 OOS에 사후 생성하지 않음.
- Depth 및 가격별 Liquidation Heatmap의 신뢰 가능한 장기 point-in-time archive가 없어 N/A.
- Volume Profile은 30m OHLCV만으로 가격대별 실제 체결분포를 정확히 복원할 수 없으므로 이 1차 검증에서는 N/A.
- 따라서 본 결과는 V3의 가격구조/위치/ATR/VWAP/AVWAP/Fib/Volume/CVD-Taker proxy 축 중심의 Shadow 검증이며, Work 승격 전 실시간 Shadow에서 미복원 축을 추가 검증해야 함.