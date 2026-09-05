from __future__ import annotations

import json
import numpy as np
import pandas as pd

import run_v26_v070 as v070

core = v070.core
D = v070.D
HALVINGS = pd.to_datetime(["2012-11-28", "2016-07-09", "2020-05-11", "2024-04-20"], utc=True)
ETF_ERA_START = pd.Timestamp("2024-01-11", tz="UTC")
MODERN_ERA_START = pd.Timestamp("2020-01-01", tz="UTC")


def _halving_era(t: pd.Timestamp) -> str:
    t = pd.Timestamp(t)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    h = HALVINGS[HALVINGS <= t]
    return "PRE_2012" if len(h) == 0 else h.max().date().isoformat()


def _horizon_success(events: pd.DataFrame, t: pd.Timestamp, side: str, horizon: int) -> bool | None:
    if t not in events.index:
        return None
    r = events.loc[t]
    up = pd.to_numeric(pd.Series([r.get(f"hit_up30_{horizon}d")]), errors="coerce").iloc[0]
    dn = pd.to_numeric(pd.Series([r.get(f"hit_dn20_{horizon}d")]), errors="coerce").iloc[0]
    if side == "LONG":
        if pd.isna(up):
            return False
        return bool(pd.isna(dn) or float(up) < float(dn))
    if pd.isna(dn):
        return False
    return bool(pd.isna(up) or float(dn) < float(up))


def _side_audit(indep: pd.DataFrame, events: pd.DataFrame, v070_validation: dict, side: str) -> dict:
    col = "long_grade" if side == "LONG" else "short_grade"
    target = D[0] if side == "LONG" else D[1]
    a = indep[indep[col].eq("A") & indep["actual"].isin(D)].copy()
    a["correct_side"] = a["actual"].eq(target)
    a["halving_era"] = [_halving_era(t) for t in a.index]

    horizon = {}
    for h in [30, 90, 180, 365]:
        vals = [_horizon_success(events, t, side, h) for t in a.index]
        vals = [x for x in vals if x is not None]
        horizon[str(h)] = {
            "cases": len(vals),
            "clean_first_pass_success_pct": None if not vals else round(float(np.mean(vals)) * 100, 2)
        }

    def era_slice(start: pd.Timestamp) -> dict:
        z = a[a.index >= start]
        return {
            "count": int(len(z)),
            "precision_pct": None if z.empty else round(float(z["correct_side"].mean()) * 100, 2)
        }

    validated = bool(v070_validation.get(side, {}).get("A_validated", False))
    distinct_eras = int(a["halving_era"].nunique()) if not a.empty else 0
    diversity_gate = distinct_eras >= 2
    deployment = "VALIDATED_SELECTIVE" if validated and diversity_gate else "RESEARCH_ONLY"
    return {
        "A_count": int(len(a)),
        "A_precision_pct": None if a.empty else round(float(a["correct_side"].mean()) * 100, 2),
        "A_gain_vs_v035_pp": v070_validation.get(side, {}).get("A_gain_vs_v035_pp"),
        "distinct_halving_eras": distinct_eras,
        "halving_era_counts": a["halving_era"].value_counts().to_dict() if not a.empty else {},
        "cross_cycle_diversity_gate": diversity_gate,
        "modern_2020_plus": era_slice(MODERN_ERA_START),
        "institutional_2024_plus": era_slice(ETF_ERA_START),
        "horizon_clean_success": horizon,
        "deployment": deployment,
        "interpretation": "Precision is historical selective-signal precision, not a guaranteed future probability. Era subsets are context only when sample counts are small."
    }


def main_v071() -> None:
    # V0.7.1 is an audit/deployment contract on top of the already-run V0.7.0 outputs.
    cfg, out, data = core.load_config(), core.OUT, core.DATA
    indep = pd.read_csv(out / "episode_independent_v070_asymmetric.csv", parse_dates=["time"]).set_index("time")
    events = pd.read_csv(data / "event_registry.csv", parse_dates=["time"]).set_index("time")
    current = json.loads((out / "current_v070_asymmetric.json").read_text(encoding="utf-8"))
    val = json.loads((out / "v070_validation_summary.json").read_text(encoding="utf-8"))["validation"]

    sides = {s: _side_audit(indep, events, val, s) for s in ["LONG", "SHORT"]}
    current_use = {}
    for s, key in [("LONG", "long"), ("SHORT", "short")]:
        raw_grade = current.get(key, {}).get("grade")
        deployment = sides[s]["deployment"]
        if raw_grade == "A" and deployment == "VALIDATED_SELECTIVE":
            use = "USE_AS_VALIDATED_HISTORICAL_A_SIGNAL"
        elif raw_grade == "A":
            use = "STRONG_SIMILARITY_ONLY_NOT_VALIDATED_A"
        elif raw_grade == "B":
            use = "SUPPORTING_CONTEXT_ONLY"
        else:
            use = "CONTEXT_ONLY"
        current_use[s] = {
            "raw_grade": raw_grade,
            "signal_strength": current.get(key, {}).get("signal_strength"),
            "deployment": deployment,
            "current_use": use
        }

    summary = {
        "engine": "BTC_FRACTAL_V0_7_1_ASYMMETRIC_DEPLOYMENT_AUDIT",
        "schema_version": "0.7.1",
        "architecture": "V070_SIDE_SPECIFIC_A_GRADE + CROSS_CYCLE_DIVERSITY + HORIZON_AUDIT",
        "side_validation": sides,
        "current": {
            "query_date": current.get("query_date"),
            "dominant_side_raw": current.get("dominant_side"),
            "LONG": current_use["LONG"],
            "SHORT": current_use["SHORT"]
        },
        "master_consensus_backtest": {
            "status": "PENDING_HISTORICAL_MASTER_STATE_SERIES",
            "reason": "Historical MASTER BTC TREND decisions/scores were not stored point-in-time across the full backtest window. Do not fabricate a MASTER-consensus hit rate.",
            "live_rule_candidate": "After explicit integration approval, compare the read-only fractal side with the current MASTER direction as agreement/counter-evidence only; it cannot bypass execution gates."
        },
        "next_research": {
            "LONG": "V0.7.2 false-positive reduction. Keep the failed all-history LONG A gate frozen as benchmark; test cycle/extension and price-location features with nested walk-forward or leave-cycle-out validation, not full-sample threshold fitting.",
            "SHORT": "Keep V0.7.0 SHORT A frozen as the validated selective benchmark; seek higher precision only if case count and cross-cycle robustness do not deteriorate."
        },
        "master_integration": "FORBIDDEN_PENDING_MANUAL_REVIEW_AND_EXPLICIT_USER_APPROVAL",
        "pr_merge": "FORBIDDEN_PENDING_MANUAL_REVIEW_AND_EXPLICIT_USER_APPROVAL"
    }
    (out / "v071_deployment_audit.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main_v071()
