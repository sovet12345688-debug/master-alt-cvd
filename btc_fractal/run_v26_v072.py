from __future__ import annotations

import json
import numpy as np
import pandas as pd

import run_v26_v071 as v071

core = v071.core
D = v071.D
HALVINGS = v071.HALVINGS

# Canonical cycle windows, declared before this V0.7.2 validation run:
# - first 180 calendar days after a halving = early post-halving transition
# - day 1095+ after the prior halving = final ~365 days of a nominal four-year cycle / pre-halving year
EARLY_POST_HALVING_DAYS = 180
PRE_HALVING_YEAR_START_DAY = 1095


def _days_since_halving(t: pd.Timestamp) -> int | None:
    t = pd.Timestamp(t)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    h = HALVINGS[HALVINGS <= t]
    if len(h) == 0:
        return None
    return int((t - h.max()).days)


def _cycle_window(days: int | None) -> str:
    if days is None:
        return "UNKNOWN"
    if days <= EARLY_POST_HALVING_DAYS:
        return "EARLY_POST_HALVING"
    if days >= PRE_HALVING_YEAR_START_DAY:
        return "PRE_HALVING_YEAR"
    return "MID_CYCLE"


def main_v072() -> None:
    cfg, out, _data = core.load_config(), core.OUT, core.DATA
    indep = pd.read_csv(out / "episode_independent_v070_asymmetric.csv", parse_dates=["time"]).set_index("time")
    v070_validation = json.loads((out / "v070_validation_summary.json").read_text(encoding="utf-8"))["validation"]
    current = json.loads((out / "current_v070_asymmetric.json").read_text(encoding="utf-8"))

    z = indep[indep["long_grade"].eq("A") & indep["actual"].isin(D)].copy()
    z["days_since_halving"] = [_days_since_halving(t) for t in z.index]
    z["cycle_window"] = [_cycle_window(x) for x in z["days_since_halving"]]
    z["cycle_qualified"] = z["cycle_window"].isin(["EARLY_POST_HALVING", "PRE_HALVING_YEAR"])
    z["correct_long"] = z["actual"].eq(D[0])

    q = z[z["cycle_qualified"]].copy()
    raw_long_a = v070_validation.get("LONG", {}).get("A", {})
    raw_precision = raw_long_a.get("precision_pct")
    precision = None if q.empty else round(float(q["correct_long"].mean()) * 100, 2)
    gain = None if precision is None or raw_precision is None else round(float(precision) - float(raw_precision), 2)
    eras = [_cycle_window(_days_since_halving(t)) + "|" + v071._halving_era(t) for t in q.index]
    distinct_halving_eras = len({v071._halving_era(t) for t in q.index})

    # Retrospective temporal stress test. It is NOT called an untouched holdout because this rule was designed after prior research.
    split = pd.Timestamp("2024-01-01", tz="UTC")
    pre = q[q.index < split]
    post = q[q.index >= split]
    pre_precision = None if pre.empty else round(float(pre["correct_long"].mean()) * 100, 2)
    post_precision = None if post.empty else round(float(post["correct_long"].mean()) * 100, 2)

    gates = {
        "case_gate": len(q) >= 6,
        "precision_gate": precision is not None and precision >= 75.0,
        "gain_gate": gain is not None and gain >= 15.0,
        "cross_cycle_gate": distinct_halving_eras >= 2,
        "retrospective_post2024_case_gate": len(post) >= 4,
        "retrospective_post2024_precision_gate": post_precision is not None and post_precision >= 75.0
    }
    retrospective_pass = all(gates.values())

    current_days = _days_since_halving(pd.Timestamp(current.get("query_date"), tz="UTC"))
    current_window = _cycle_window(current_days)
    current_raw_a = current.get("long", {}).get("grade") == "A"
    current_cycle_qualified = current_window in ["EARLY_POST_HALVING", "PRE_HALVING_YEAR"]

    result = {
        "engine": "BTC_FRACTAL_V0_7_2_CYCLE_CONDITIONED_LONG",
        "schema_version": "0.7.2",
        "architecture": "V070_LONG_A + CANONICAL_HALVING_CYCLE_WINDOW_FALSE_POSITIVE_FILTER",
        "rule_freeze": {
            "early_post_halving_days": EARLY_POST_HALVING_DAYS,
            "pre_halving_year_start_day": PRE_HALVING_YEAR_START_DAY,
            "eligible_windows": ["EARLY_POST_HALVING", "PRE_HALVING_YEAR"],
            "reason": "Use protocol-known cycle timing as a read-only historical context filter to reduce late/mid-cycle LONG false positives."
        },
        "validation": {
            "raw_v070_LONG_A": raw_long_a,
            "cycle_qualified_LONG_A": {
                "count": int(len(q)),
                "precision_pct": precision,
                "gain_vs_raw_LONG_A_pp": gain,
                "distinct_halving_eras": int(distinct_halving_eras),
                "window_counts": q["cycle_window"].value_counts().to_dict() if not q.empty else {},
                "halving_era_counts": {v071._halving_era(t): int(sum(v071._halving_era(x) == v071._halving_era(t) for x in q.index)) for t in q.index} if not q.empty else {},
                "cases": [
                    {
                        "date": t.date().isoformat(),
                        "window": r["cycle_window"],
                        "days_since_halving": int(r["days_since_halving"]),
                        "actual": r["actual"],
                        "correct": bool(r["correct_long"])
                    }
                    for t, r in q.iterrows()
                ]
            },
            "retrospective_temporal_stress": {
                "warning": "Not an untouched holdout; reported only as a temporal robustness stress test.",
                "pre_2024": {"count": int(len(pre)), "precision_pct": pre_precision},
                "post_2024": {"count": int(len(post)), "precision_pct": post_precision}
            },
            "gates": gates,
            "retrospective_stage": "PASS" if retrospective_pass else "FAIL"
        },
        "current": {
            "query_date": current.get("query_date"),
            "raw_LONG_grade": current.get("long", {}).get("grade"),
            "raw_LONG_strength": current.get("long", {}).get("signal_strength"),
            "days_since_halving": current_days,
            "cycle_window": current_window,
            "cycle_qualified": current_cycle_qualified,
            "LONG_A_plus_active": bool(current_raw_a and current_cycle_qualified and retrospective_pass)
        },
        "deployment": {
            "status": "PROVISIONAL_RESEARCH_PASS" if retrospective_pass else "RESEARCH_FAIL",
            "why_not_full_validated": "The cycle filter was added after prior V0.7.0 research, so a truly untouched prospective sample is still required before calling LONG A+ empirically validated.",
            "prospective_upgrade_rule": "Require at least 3 future independent matured LONG A+ cases with >=67% precision before VALIDATED_SELECTIVE_LONG consideration; no gate lowering.",
            "MASTER_effect": "0% until explicit manual approval. Even if approved, use as read-only historical evidence/counter-evidence and never bypass Entry/Safety/NonChase gates."
        },
        "next_step": "Track prospective LONG A+ outcomes; in parallel test price-location/extension features with nested walk-forward or leave-cycle-out validation without tuning on the full OOS sample.",
        "master_integration": "FORBIDDEN_PENDING_MANUAL_REVIEW_AND_EXPLICIT_USER_APPROVAL"
    }
    (out / "v072_cycle_conditioned_long.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main_v072()
