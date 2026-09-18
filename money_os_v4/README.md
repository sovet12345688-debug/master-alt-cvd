# MONEY OS V4 — W5 Trading Room development slice

This additive slice implements the **W5 TRADING ROOM / EXECUTION** shadow
interface and a BTC_DAILY research profile. It is not a claim that the complete
V4 runtime or durable state already exists. Existing MASTER TRADING remains
available unchanged outside this slice.

The current BTC_DAILY candidate is **RESEARCH_REJECTED**. It failed the requested
net win-rate target and had negative expectancy in validation and the final check.
The worker returns WAIT, emits no trading trigger alert and has no broker access.

## Ownership

Shared Facts → independent analysis → Navigator typed request → W5 plan →
Final Navigator maintain/downgrade → Alert Router. W5 owns final prices. No
additional W6 execution owner, no worker alert sending, no scheduler activation.

Files:

- `workers/w5_trading_room/model.py`: deterministic challenger plan builder.
- `workers/w5_trading_room/worker.py`: callable typed shadow preview and gates.
- `contracts/w5_btc_daily.json`: ownership, inputs, KPI definitions and invariants.
- `contracts/w5_btc_daily_release.json`: actual research rejection, not promotion.
- `research/btc_daily/`: public-data downloader, replay, 252-variant study and audit.
- `changes/CODEX_CHANGE_HANDOFF_BTC_DAILY_20260918.md`: complete development handoff.

## Execute a shadow preview

From the repository root, with Python 3.12 and research dependencies installed:

```sh
python -m money_os_v4.workers.w5_trading_room --request request.json --facts facts.json
```

Request shape: `request_id`, `strategy_type=BTC_TRADE`, `asset=BTCUSDT`,
`request_mode=PLAN|TRIGGER_CHECK|ADD_CHECK|REVALIDATE`, `permission=ALLOW`.
Navigator must not pass final prices, leverage or position sizing.

Facts: current `{price, asof}`, completed-candle `closed_asof`, `completed_candles`,
`severe_risk_veto=false`, `core_source_health=OK`, and `h1` with the deterministic
features consumed by `model.build_plan`. Research `shared_facts` is the inspectable
public-data adapter; production Shared Facts integration is still required.
Optional funding missing uses a labeled 0.03%/settlement planning reserve; observed
funding remains N/A. The adapter neither creates official state nor stores private
information. Uncalibrated live fill probability is null.

## Replay the study

```sh
python -m pip install -r money_os_v4/research/btc_daily/requirements-lock.txt
python money_os_v4/research/btc_daily/download_data.py --out /path/to/research-data --cache /path/to/public-archive-cache
python money_os_v4/research/btc_daily/validate_engine.py --data /path/to/research-data
python money_os_v4/research/btc_daily/search.py --data /path/to/research-data
python money_os_v4/research/btc_daily/refine.py --data /path/to/research-data
python money_os_v4/research/btc_daily/evaluate.py --data /path/to/research-data
python money_os_v4/research/btc_daily/audit_final.py --data /path/to/research-data
```

Use the study's normalized inputs for identical reproduction; public archives
may be revised. The snapshot contains October 2023 warm-up through September 17,
2026. Training is 2024; validation/model choice is 2025; one fixed config is checked
on 2026. The final two days before each boundary are withheld for full entry/exit
observation. The current study already viewed 2026 and is not fresh forward data.

The original 216 configurations remain recorded. One bounded, explicitly recorded
36-configuration refinement used only 2024/2025. No parameter search follows the
2026 check. Actual legacy Champion performance remains N/A; the comparison is a
legacy **price proxy**. No claim that this code reproduces discretionary decisions.

R is a fixed abstract full-plan risk budget, with E1=0.4 and E2=0.6 sized by each
entry's stop distance plus entry/stop execution costs. It differs from older
price-distance R conventions. Fee=0.06% and adverse execution cost=0.02% each side;
funding uses actual historical rate and mark. First-fill probability includes all
issued plans and differs from full E1+E2 fill. 15m OHLC cannot establish exact tick
ordering or queue fills; the replay uses documented adverse/conservative rules.

The code supplies price-plan research and an interface. Exact legacy adapter
parity, full clock persistence, live source plumbing, Final Navigator integration,
forward calibration and production approval are separate outstanding work.
