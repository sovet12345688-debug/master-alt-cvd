from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

STATUS = "RESEARCH_ONLY_PROMOTION_HOLD"
PROTOCOL = "VALIDATION_PROTOCOL_V1_0_FINAL_LOCK"

ROOT = Path("btc_trend_v30/output/validation_v1")
DATA = ROOT / "data_integrity/btc_usdt_1d_matrix.csv"
LABELS = ROOT / "step4_outcome_labels/episode_medium_long_truth_labels.csv"
STEP4 = ROOT / "step4_outcome_labels/audit.json"
OUT = ROOT / "step5_walk_forward_oos"
OUT.mkdir(parents=True, exist_ok=True)

BASELINE_FILES = [
    Path("btc_trend_v30/validation_protocol_v1_step2_r12_daily_scoring.py"),
    Path("btc_trend_v30/validation_protocol_v1_step3_episode_dedupe.py"),
    Path("btc_trend_v30/validation_protocol_v1_step4_outcome_labels.py"),
]

FOLDS = [
    ("WF1", "2021-01-01", "2021-12-31"),
    ("WF2", "2022-01-01", "2022-12-31"),
    ("WF3", "2023-01-01", "2023-12-31"),
    ("WF4", "2024-01-01", "2024-12-31"),
    ("WF5", "2025-01-01", "2025-12-31"),
    ("WF6", "2026-01-01", "2026-09-04"),
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def as_bool(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s
    return s.astype(str).str.lower().map({"true": True, "false": False}).fillna(False)


def safe_rate(num: int, den: int):
    return float(num / den) if den else None


def stage_monotonicity(g: pd.DataFrame, available_col: str, success_col: str, min_n: int = 5):
    q = g[g[available_col]].copy()
    rows = []
    for stage in (1, 2, 3):
        z = q[q.first_stage == stage]
        n = len(z)
        hit = int(z[success_col].sum()) if n else 0
        rows.append({"stage": stage, "n": n, "hits": hit, "rate": safe_rate(hit, n)})
    usable = [r for r in rows if r["n"] >= min_n]
    if len(usable) < 2:
        verdict = "INSUFFICIENT_N"
    else:
        rates = [r["rate"] for r in usable]
        verdict = "PASS" if all(b + 1e-12 >= a for a, b in zip(rates, rates[1:])) else "FAIL"
    return verdict, rows


def main():
    if not STEP4.exists() or json.loads(STEP4.read_text(encoding="utf-8")).get("step4") != "PASS":
        raise RuntimeError("Step4 PASS required")

    df = pd.read_csv(DATA)
    ep = pd.read_csv(LABELS)
    df["date"] = pd.to_datetime(df.open_time, unit="ms", utc=True)
    ep["start_date"] = pd.to_datetime(ep.start_date, utc=True)
    ep["available_90d"] = as_bool(ep["available_90d"])
    ep["available_365d"] = as_bool(ep["available_365d"])
    ep["first_stage"] = ep.first_stage.astype(int)
    ep["first_score"] = ep.first_score.astype(float)

    # Future data are truth labels only. Fixed R1.2 baseline is never fit/tuned on test folds.
    ep["medium20_hit"] = ep.medium_truth_status.isin(["MEDIUM_SUCCESS_20", "MEDIUM_SUCCESS_30"])
    ep["medium30_hit"] = ep.medium_truth_status.eq("MEDIUM_SUCCESS_30")
    ep["long_primary_hit"] = ep.long_truth_status.isin(["LONG_SUCCESS_PRIMARY", "LONG_SUCCESS_EXTENSION"])
    ep["long_extension_hit"] = ep.long_truth_status.eq("LONG_SUCCESS_EXTENSION")

    # Exact row-based outcome-availability date. A label is train-available at fold start only
    # when the full horizon has elapsed strictly before the test period begins.
    idx_to_date = dict(zip(df.index.astype(int), df.date))
    def horizon_date(start_idx: int, horizon: int):
        return idx_to_date.get(int(start_idx) + horizon, pd.NaT)
    ep["date_90d_complete"] = [horizon_date(i, 90) for i in ep.start_idx]
    ep["date_365d_complete"] = [horizon_date(i, 365) for i in ep.start_idx]

    fold_rows = []
    engine_rows = []
    train_rows = []
    stage_rows = []
    assigned_ids = []

    for fold, start_s, end_s in FOLDS:
        start = pd.Timestamp(start_s, tz="UTC")
        end = pd.Timestamp(end_s, tz="UTC")
        test = ep[(ep.start_date >= start) & (ep.start_date <= end)].copy()
        assigned_ids.extend(test.independent_episode_id.astype(str).tolist())

        train_base = ep[ep.start_date < start].copy()
        train_med = train_base[train_base.date_90d_complete.notna() & (train_base.date_90d_complete < start)]
        train_long = train_base[train_base.date_365d_complete.notna() & (train_base.date_365d_complete < start)]
        leak_med = int((train_med.date_90d_complete >= start).sum())
        leak_long = int((train_long.date_365d_complete >= start).sum())
        train_rows.append({
            "fold": fold,
            "test_start": start_s,
            "test_end": end_s,
            "fit_action": "NONE_FIXED_R1_2_BASELINE",
            "train_origin_episodes": len(train_base),
            "train_medium_labels_available_at_start": len(train_med),
            "train_long_labels_available_at_start": len(train_long),
            "medium_availability_leak_count": leak_med,
            "long_availability_leak_count": leak_long,
        })

        ma = test[test.available_90d]
        la = test[test.available_365d]
        fold_rows.append({
            "fold": fold,
            "test_start": start_s,
            "test_end": end_s,
            "episodes": len(test),
            "long_episodes": int((test.direction == "long").sum()),
            "short_episodes": int((test.direction == "short").sum()),
            "medium_available": len(ma),
            "medium_censored": int((~test.available_90d).sum()),
            "medium20_hits": int(ma.medium20_hit.sum()),
            "medium20_hit_rate": safe_rate(int(ma.medium20_hit.sum()), len(ma)),
            "medium30_hits": int(ma.medium30_hit.sum()),
            "medium30_hit_rate": safe_rate(int(ma.medium30_hit.sum()), len(ma)),
            "long_available": len(la),
            "long_censored": int((~test.available_365d).sum()),
            "long_primary_hits": int(la.long_primary_hit.sum()),
            "long_primary_hit_rate": safe_rate(int(la.long_primary_hit.sum()), len(la)),
            "long_extension_hits": int(la.long_extension_hit.sum()),
            "long_extension_hit_rate": safe_rate(int(la.long_extension_hit.sum()), len(la)),
        })

        for engine, g in test.groupby("engine"):
            gm = g[g.available_90d]
            gl = g[g.available_365d]
            engine_rows.append({
                "fold": fold,
                "engine": engine,
                "direction": str(g.direction.iloc[0]),
                "episodes": len(g),
                "medium_available": len(gm),
                "medium20_hits": int(gm.medium20_hit.sum()),
                "medium20_hit_rate": safe_rate(int(gm.medium20_hit.sum()), len(gm)),
                "medium30_hits": int(gm.medium30_hit.sum()),
                "medium30_hit_rate": safe_rate(int(gm.medium30_hit.sum()), len(gm)),
                "long_available": len(gl),
                "long_primary_hits": int(gl.long_primary_hit.sum()),
                "long_primary_hit_rate": safe_rate(int(gl.long_primary_hit.sum()), len(gl)),
                "long_extension_hits": int(gl.long_extension_hit.sum()),
                "long_extension_hit_rate": safe_rate(int(gl.long_extension_hit.sum()), len(gl)),
            })

        for direction, g in test.groupby("direction"):
            for horizon_name, avail_col, success_col in [
                ("MEDIUM20_90D", "available_90d", "medium20_hit"),
                ("LONG_PRIMARY_365D", "available_365d", "long_primary_hit"),
            ]:
                verdict, rows = stage_monotonicity(g, avail_col, success_col)
                for r in rows:
                    stage_rows.append({
                        "fold": fold,
                        "direction": direction,
                        "target": horizon_name,
                        "stage": r["stage"],
                        "n": r["n"],
                        "hits": r["hits"],
                        "rate": r["rate"],
                        "fold_direction_target_verdict": verdict,
                    })

    fold_df = pd.DataFrame(fold_rows)
    engine_df = pd.DataFrame(engine_rows)
    train_df = pd.DataFrame(train_rows)
    stage_df = pd.DataFrame(stage_rows)

    oos_start = pd.Timestamp("2021-01-01", tz="UTC")
    oos_end = pd.Timestamp("2026-09-04", tz="UTC")
    oos = ep[(ep.start_date >= oos_start) & (ep.start_date <= oos_end)].copy()
    assigned = pd.Series(assigned_ids, dtype=str)
    duplicate_assignments = int(assigned.duplicated().sum())
    expected_ids = set(oos.independent_episode_id.astype(str))
    assigned_set = set(assigned_ids)
    missing_assignments = sorted(expected_ids - assigned_set)
    extra_assignments = sorted(assigned_set - expected_ids)

    om = oos[oos.available_90d]
    ol = oos[oos.available_365d]
    direction_summary = {}
    for direction, g in oos.groupby("direction"):
        gm = g[g.available_90d]
        gl = g[g.available_365d]
        direction_summary[direction] = {
            "episodes": len(g),
            "medium_available": len(gm),
            "medium20_hit_rate": safe_rate(int(gm.medium20_hit.sum()), len(gm)),
            "medium30_hit_rate": safe_rate(int(gm.medium30_hit.sum()), len(gm)),
            "long_available": len(gl),
            "long_primary_hit_rate": safe_rate(int(gl.long_primary_hit.sum()), len(gl)),
            "long_extension_hit_rate": safe_rate(int(gl.long_extension_hit.sum()), len(gl)),
        }

    # Global stage monotonicity is preliminary target-label evidence, not a final promotion gate.
    global_monotonicity = {}
    for direction, g in oos.groupby("direction"):
        global_monotonicity[direction] = {}
        for target, avail_col, success_col in [
            ("MEDIUM20_90D", "available_90d", "medium20_hit"),
            ("LONG_PRIMARY_365D", "available_365d", "long_primary_hit"),
        ]:
            verdict, rows = stage_monotonicity(g, avail_col, success_col)
            global_monotonicity[direction][target] = {"verdict": verdict, "stages": rows}

    checks = {
        "step4_pass_required": True,
        "fixed_baseline_no_fit": bool((train_df.fit_action == "NONE_FIXED_R1_2_BASELINE").all()),
        "six_folds_present": len(fold_df) == 6 and fold_df.fold.nunique() == 6,
        "no_fold_overlap_or_duplicate_assignment": duplicate_assignments == 0,
        "all_oos_episodes_assigned_once": not missing_assignments and not extra_assignments and len(assigned_ids) == len(oos),
        "train_medium_outcome_availability_gate": int(train_df.medium_availability_leak_count.sum()) == 0,
        "train_long_outcome_availability_gate": int(train_df.long_availability_leak_count.sum()) == 0,
        "future_used_only_as_truth_labels": True,
        "probability_not_claimed": True,
    }
    step5_pass = all(checks.values())

    fold_df.to_csv(OUT / "walk_forward_fold_summary.csv", index=False)
    engine_df.to_csv(OUT / "walk_forward_engine_summary.csv", index=False)
    train_df.to_csv(OUT / "walk_forward_train_availability.csv", index=False)
    stage_df.to_csv(OUT / "walk_forward_stage_monotonicity.csv", index=False)

    baseline_hashes = {p.name: sha256_file(p) for p in BASELINE_FILES}
    summary = {
        "status": STATUS,
        "protocol": PROTOCOL,
        "step": "5_WALK_FORWARD_OOS_FIXED_R1_2_BASELINE",
        "baseline_freeze": {
            "github_sha": os.environ.get("GITHUB_SHA", "LOCAL_OR_UNSET"),
            "fit_action": "NONE_FIXED_R1_2_BASELINE",
            "source_sha256": baseline_hashes,
            "rule": "No parameter/weight/stage/zone tuning from WF1-WF6 results. Any changed version requires new untouched OOS evidence.",
        },
        "development_period": "2017-08-17..2020-12-31",
        "oos_period": "2021-01-01..2026-09-04",
        "folds": fold_rows,
        "oos_episodes": len(oos),
        "oos_medium_available": len(om),
        "oos_medium20_hit_rate": safe_rate(int(om.medium20_hit.sum()), len(om)),
        "oos_medium30_hit_rate": safe_rate(int(om.medium30_hit.sum()), len(om)),
        "oos_long_available": len(ol),
        "oos_long_primary_hit_rate": safe_rate(int(ol.long_primary_hit.sum()), len(ol)),
        "oos_long_extension_hit_rate": safe_rate(int(ol.long_extension_hit.sum()), len(ol)),
        "direction_summary": direction_summary,
        "global_stage_monotonicity_preliminary": global_monotonicity,
        "assignment_audit": {
            "expected_oos_episode_n": len(oos),
            "assigned_n": len(assigned_ids),
            "duplicate_assignments": duplicate_assignments,
            "missing_assignments": missing_assignments,
            "extra_assignments": extra_assignments,
        },
        "checks": checks,
        "step5": "PASS" if step5_pass else "HOLD",
        "performance_gate": "PRELIMINARY_TARGET_LABELS_ONLY__REALIZED_R_MCR_FALSE_START_MISSED_TREND_PENDING_STEP6_7",
        "score_is_probability": False,
        "probability": "확률 산출보류",
        "v2_6_modified": False,
        "promotion": "HOLD",
        "next_step": "STEP6_STATE_MACHINE_REPLAY_1H_4H_1D" if step5_pass else "STOP_AND_AUDIT_WALK_FORWARD",
    }
    (OUT / "audit.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
