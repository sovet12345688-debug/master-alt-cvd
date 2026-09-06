# MONEY MASTER OS — COMMON RULES

These rules apply to every MASTER registered in MONEY MASTER OS unless an explicitly approved MASTER-specific rule is stricter.

1. GitHub canonical source outranks chat-memory reconstruction for version identity.
2. Never silently delete, rename away, or omit a required rule, field, output block, or state key during migration.
3. `N/A`, missing, stale, and zero are different states. Never zero-fill unknown data.
4. Never fabricate price, Entry, Trigger, SL, TP, R:R, score history, FirstSeen, prior-run values, or market data.
5. Closed-candle confirmation must remain distinct from provisional/in-progress candles.
6. Time-mismatched data must not be synthesized as one simultaneous snapshot.
7. Existing collectors and historical data are not modified as a side effect of prompt migration.
8. A destructive or contract-breaking change requires explicit approval and a recorded changelog.
9. `VERSION_DRIFT` and `SOURCE_MISSING` always block automatic room bootstrap.
10. New room bootstrap must report source version, repository path, source SHA when available, state/handoff status, and validation result before execution.
