# MASTER TRADING · V3 SHADOW BACKTEST — SOURCE CHAT EXECUTION COMMAND

Use this command in the current MASTER TRADING source chat. This task MUST be completed in the chat, not delegated to ChatGPT Work.

---

`MASTER TRADING · CURRENT + TIME VALIDITY V2.1`을 기준선으로 두고, 최신 `research/master_trading_daily_entry_v3_design.md`의 **Fibonacci PRICE 포함 DAILY ENTRY ENGINE V3**를 BTC/ETH 과거 데이터로 Point-in-Time Shadow 백테스트해서 기존 MASTER 대비 성능을 검증하라.

## 최상위 목적
이 연구의 목적은 장기투자나 시장전망이 아니라 **데일리 트레이딩 타점 정확도 개선**이다.
기본 시간축은 `1D Context → 4H Regime → 1H Setup/Entry Zone → 30m/15m Trigger`이며, 주 보유기간은 장중~약 1일이다.

## 반드시 먼저 읽을 최신 기준
GitHub `sovet12345688-debug/master-alt-cvd`의 최신 `main`에서:
- `master_prompts/master_trading_current.md`
- `state/master_trading_current_contract.json`
- `money_master_os/masters/trading/MASTER-TRADING-UI-V2-FINAL.md`
- `research/master_trading_daily_entry_v3_design.md`
- 기존 `btc_backtest/output/time_validity_v2/summary.json` 및 관련 point-in-time 연구 산출물

V3 설계는 현재 Production을 자동 변경하는 규칙이 아니라 **SHADOW 연구 후보**다.

## 비교군
A. `CURRENT BASELINE` = MASTER TRADING · CURRENT + TIME VALIDITY V2.1
B. `V3 CORE - FIB`
C. `V3 CORE + FIB`

가능한 경우 추가 Ablation:
- V3 - Order Flow family
- V3 - Liquidity/Microstructure family
- V3 - VWAP/AVWAP/Volume Profile family
- V3의 ATR buffer 후보 `0 / 0.10 / 0.15 / 0.20 x 1H ATR`

과거에 실제 존재하지 않았거나 point-in-time 복원이 불가능한 OI/CVD/Taker/Depth/Liquidation/On-chain 값을 사후 생성하지 말 것. 공통 Coverage 구간 또는 해당 축 제외 Ablation으로 비교할 것. Missing은 0이 아니라 N/A다.

## V3 데이터축
### PRE-TOUCH 70
1. Regime & MTF Alignment 12
2. Structure & Location 22
   - Swing/BOS/Reclaim/Failure 8
   - S/R 5
   - MTF confluence 3
   - VWAP/AVWAP/Volume Profile 3
   - PDH/PDL/Previous Close/Session levels 2
   - Fibonacci PRICE confluence 1
3. Participation & Order Flow 14
4. Derivatives & Liquidity 10
5. Volatility/Extension/Non-Chasing 7
6. Secondary Context 5

### REACTION 30
7. 15m/30m Completed Candle Reaction 10
8. Trigger Participation 9
9. Microstructure Confirmation 6
10. Time Validity/Freshness 5

Score는 순위/품질용일 뿐이며 Hard Gate를 대체하지 않는다.

## Fibonacci 최종규칙
- **Fibonacci PRICE 사용 / Fibonacci Time OFF**
- Retracement: `0.382 / 0.5 / 0.618 / 0.786`
- Extension: `1.272 / 1.618`
- 완료된 객관적 1H/4H Swing 또는 impulse leg만 anchor로 사용
- 임의 anchor cherry-picking 금지
- Fib 단독 Entry/SL/TP 생성 금지
- Fib는 Structure/S&R/VWAP/VP/Liquidity와의 confluence 또는 TP projection 보조만 허용
- Structure invalidation이 Fib보다 우선
- Fib 가중치는 Structure & Location 내부 최대 1점
- Fib extension 하나만으로 3R TP를 만들지 말 것
- 반드시 `V3+Fib vs V3-Fib` OOS Ablation을 별도로 제시할 것

## Entry / SL / TP 실행정의
- Entry Zone: 구조적 Swing/BOS/Reclaim/Failed-break → 1H/4H S/R → PDH/PDL/Session → VWAP/AVWAP/VP → Fib → Liquidity → ATR normalization 순으로 후보 생성
- SL: Trade Frame Lock 후 thesis를 부정하는 구조적 invalidation 기준. ATR/liquidity buffer는 연구 후보일 뿐 사후조정 금지
- TP1: 가장 가까운 실제 구조/유동성 목표
- TP2: 주 구조 목표. 가능하면 TP2 자체가 >=3R인 Setup 선호
- TP3: 확장/상위 프레임 목표
- 먼 TP3 하나만으로 억지 3R을 만드는 Setup은 낮은 품질 또는 WAIT

## Trigger
완성봉만 사용한다.
- LONG Pullback: 지지 도달 후 15m/30m `아랫꼬리+양봉마감` 또는 지지이탈→즉시회복 양봉마감, 다음 캔들이 저점 유지
- LONG Breakout: 저항 위 15m/30m 완성봉 종가 돌파 → Retest 지지
- SHORT: 저항에서 `윗꼬리+음봉` 또는 Fake Break→저항 아래 재마감 + Hold/Failed Retest
진행봉은 PROVISIONAL이며 Trigger로 계산하지 않는다.

## Hard Gate
실거래 후보는 다음을 모두 만족해야 한다:
`CurrentFresh | TradeFrameLocked | StructuralEntryOrRetest | RequiredCompletedTrigger | Participation | NonChasing | StructuralSL | TP1/2/3 | realistic R:R>=3 | NoSevereRiskVeto | TimeValidity`
하나라도 핵심 Gate 실패면 ENTER가 아니라 WAIT.

## 백테스트 방법론
- Point-in-Time only
- No Look-ahead
- Train / Validation / OOS 또는 Walk-forward
- BTC / ETH 각각 + 합산
- Trend / Range / Transition Regime 분리
- 공통 데이터 Coverage 명시
- 사후복원 금지
- 가중치는 단일 최적점이 아니라 범위/민감도 검증
- Bootstrap/Confidence Interval은 표본이 허용할 때 수행
- 승률 단독 최적화 금지

## 필수 성능지표
- Trade Count / Coverage
- Win Rate(참고)
- Expectancy R/trade
- Profit Factor
- Max Drawdown R
- False-start loss rate
- MFE / MAE
- Entry Efficiency
- TP1/TP2/TP3 hit rate
- Missed-move rate
- Time-to-trigger / Time-to-progress
- BTC / ETH별 성능
- Regime별 성능
- Long / Short별 성능

## 기존 MASTER 대비 최종 판정
반드시 셋 중 하나만 판정:
- `V3 PASS — WORK MIGRATION CANDIDATE`
- `V3 PARTIAL — MORE VALIDATION REQUIRED`
- `V3 FAIL — KEEP CURRENT BASELINE`

PASS는 단순 승률 상승이 아니다. 최소한:
1. OOS Expectancy와 PF가 baseline보다 개선,
2. MaxDD가 실질적으로 악화되지 않음,
3. False-start가 개선 또는 최소 악화되지 않음,
4. BTC와 ETH 중 한 종목에만 의존하지 않음,
5. Trend/Range/Transition 중 하나에만 과적합되지 않음,
6. 성능개선이 단일 Optional source에만 의존하지 않음,
7. Fib 포함 효과가 별도 Ablation에서 긍정 또는 운영적으로 유의미한 중립이어야 함.

표본/신뢰도가 부족하면 PASS가 아니라 PARTIAL.

## 결과물
1. Baseline vs V3-Fib vs V3+Fib 종합 비교표
2. BTC / ETH 개별 결과
3. Regime / Long-Short 세부 결과
4. Ablation 결과
5. Fib 기여도 판정
6. 데이터 Coverage/N/A 한계
7. 과최적화 위험 및 반대근거
8. 최종 PASS/PARTIAL/FAIL
9. Production 승격 시 유지/폐기할 V3 요소
10. Work 마이그레이션에 전달할 `VALIDATED_ENGINE_SPEC` 요약

## 지속 실행 규칙
- 계획만 제시하고 멈추지 말고, 이 채팅에서 가능한 모든 읽기/데이터 확보/코드 작성/백테스트/검증/결과 정리를 끝까지 진행한다.
- 되돌릴 수 있는 작은 연구 코드/산출물/테스트는 별도 승인 없이 진행한다.
- 실제 장애, 핵심 데이터 부재로 정당한 비교가 불가능한 경우에만 중단하고 의사결정을 요청한다.
- 성공한 테스트는 새로운 문제가 없는 한 불필요하게 반복하지 않는다.
- Production canonical/UI는 이 백테스트 동안 변경하지 않는다.

## Durable handoff
백테스트 완료 후 GitHub에 TRADING 전용 연구 결과를 저장한다. 최소:
- methodology/config
- code or reproducible notebook/script where possible
- machine-readable metrics
- final report
- `VALIDATED_ENGINE_SPEC` 또는 `V3_NOT_VALIDATED`

Work는 나중에 이 durable artifact만 읽어도 이 채팅의 결과를 재구성할 수 있어야 한다. 개인 계좌/실제 포지션/민감정보는 저장하지 않는다.

최종 답변은 사용자에게 **기존 MASTER 대비 실제로 좋아졌는지**, **얼마나 좋아졌는지**, **어디서는 오히려 나빠졌는지**, **Work로 가져가도 되는지**를 쉬운 한국어로 먼저 설명한 뒤 상세표를 제시하라.
`