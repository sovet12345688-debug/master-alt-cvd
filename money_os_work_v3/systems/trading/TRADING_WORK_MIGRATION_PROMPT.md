# DEPRECATED — DO NOT USE

This combined prompt has been superseded by the user-approved two-step TRADING flow.

Use instead:

1. Current MASTER TRADING source chat backtest command:
`money_os_work_v3/systems/trading/TRADING_CHAT_V3_BACKTEST_COMMAND.md`

2. Only after the source chat returns `V3 PASS — WORK MIGRATION CANDIDATE`, use the Work migration/development prompt:
`money_os_work_v3/systems/trading/TRADING_WORK_AFTER_PASS_MIGRATION_PROMPT.md`

Backtesting belongs to the current TRADING chat. Work must not rerun it as a substitute. Work consumes the durable validated result and then performs migration/development/QA up to the final Production merge/cutover approval point.
