# ACCUM Fastpath V1 Proxy Rationale

This path intentionally avoids inventing the missing V2.6 accumulation thresholds.

The first recovery target is only to remove structural N/A when enough already-validated facts exist. Therefore the fast path reuses R2.2 completed-candle structure states and current BTC ETF flow as conservative proxies for four canonical ACCUMULATION buckets. It does not claim to implement the missing volume-lead, CVD/taker/derivatives, NonChase/value, EMA/wave, or macro thresholds.

The 1D and 4H mappings favor transition/improvement states. `STRONG_LONG` is not treated as maximum base evidence because accumulation may already be too advanced. The 1W mapping is deliberately conservative because exact distance-to-support logic is not approved. Positive ETF flow contributes as accumulation support.

Because exact NonChase/value logic is unavailable, any `STRONG_LONG` on 1D or 4H activates a conservative rapid-rise proxy and caps the partial score at the canonical V2.6 maximum of 64 for rapid-rise states.

This design is suitable only for Coverage-gated partial display until additional independent features raise coverage and are validated out of sample.
