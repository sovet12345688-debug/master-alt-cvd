from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
R24 = HERE.parent
SPEC = json.loads((HERE / "r24_state_monotonicity_spec.json").read_text(encoding="utf-8"))
POS = R24 / "output/historical_replay/r20_positions.csv"
OUT = HERE / "output"
OUT.mkdir(parents=True, exist_ok=True)

EXPECTED_BASELINE = {
    "episodes": 69,
    "long": 48,
    "short": 21,
    "long_mean_R": 0.8346097626774698,
    "short_mean_R": 0.16100864900887032,
}


def present(s: pd.Series) -> pd.Series:
    return s.notna() & (s.astype(str).str.len() > 0) & (s.astype(str).str.lower() != "nat")


def cohort_mask(df: pd.DataFrame, state: str) -> pd.Series:
    resolved = df["resolved"].astype(str).str.lower().isin(["true", "1"])
    if state == "SEED_REACHED":
        return resolved
    if state == "CONFIRMED_REACHED":
        return resolved & present(df["confirmed_time"])
    if state == "CORE_REACHED":
        return resolved & present(df["core_time"])
    raise KeyError(state)


def segment_mask(df: pd.DataFrame, segment: str) -> pd.Series:
    if segment == "ALL":
        return pd.Series(True, index=df.index)
    return df["direction"].astype(str).eq(segment)


def metrics(g: pd.DataFrame) -> dict:
    r = pd.to_numeric(g["marked_R_at_end"], errors="raise")
    return {
        "n": int(len(g)),
        "mean_terminal_R": float(r.mean()) if len(g) else None,
        "positive_rate": float((r > 0).mean()) if len(g) else None,
        "structural_stop_rate": float((g["exit_reason"].astype(str) == "STRUCTURAL_STOP").mean()) if len(g) else None,
    }


def ts(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce", format="mixed")


def invariant_checks(df: pd.DataFrame) -> dict:
    seed = ts(df["seed_time"])
    conf = ts(df["confirmed_time"])
    core = ts(df["core_time"])
    derisk = ts(df["derisk_time"])
    checks = {
        "no_core_without_confirm": bool((~core.notna() | conf.notna()).all()),
        "no_derisk_without_core": bool((~derisk.notna() | core.notna()).all()),
        "confirm_time_not_before_seed": bool((~conf.notna() | (conf >= seed)).all()),
        "core_time_not_before_confirm": bool((~core.notna() | ((conf.notna()) & (core >= conf))).all()),
        "derisk_time_not_before_core": bool((~derisk.notna() | ((core.notna()) & (derisk >= core))).all()),
    }
    checks["all_pass"] = all(checks.values())
    return checks


def main() -> None:
    df = pd.read_csv(POS)
    df["marked_R_at_end"] = pd.to_numeric(df["marked_R_at_end"], errors="raise")
    baseline = {
        "episodes": int(len(df)),
        "long": int((df.direction == "LONG").sum()),
        "short": int((df.direction == "SHORT").sum()),
        "long_mean_R": float(df.loc[(df.direction == "LONG") & df.resolved.astype(str).str.lower().isin(["true","1"]), "marked_R_at_end"].mean()),
        "short_mean_R": float(df.loc[(df.direction == "SHORT") & df.resolved.astype(str).str.lower().isin(["true","1"]), "marked_R_at_end"].mean()),
    }
    baseline_checks = {
        k: (abs(baseline[k] - v) < 1e-12 if isinstance(v, float) else baseline[k] == v)
        for k, v in EXPECTED_BASELINE.items()
    }

    states = SPEC["state_order"]
    segments = SPEC["cohort_definition"]["segments"]
    min_n = int(SPEC["primary_test"]["minimum_n_per_cohort"])
    table = []
    lookup = {}
    for seg in segments:
        for state in states:
            g = df[segment_mask(df, seg) & cohort_mask(df, state)].copy()
            m = metrics(g)
            row = {"segment": seg, "state": state, **m}
            table.append(row)
            lookup[(seg, state)] = row

    comparisons = []
    all_primary_eligible = True
    all_primary_pass = True
    every_eligible_pass = True
    for seg in segments:
        for a, b in zip(states[:-1], states[1:]):
            ra, rb = lookup[(seg, a)], lookup[(seg, b)]
            eligible = ra["n"] >= min_n and rb["n"] >= min_n
            delta = None if not eligible else float(rb["mean_terminal_R"] - ra["mean_terminal_R"])
            passed = None if not eligible else bool(delta >= 0.0)
            if seg == "ALL":
                all_primary_eligible &= eligible
                all_primary_pass &= bool(passed) if eligible else False
            if eligible:
                every_eligible_pass &= bool(passed)
            comparisons.append({
                "segment": seg,
                "from_state": a,
                "to_state": b,
                "eligible": eligible,
                "delta_mean_terminal_R": delta,
                "primary_pass": passed,
                "delta_positive_rate": None if not eligible else float(rb["positive_rate"] - ra["positive_rate"]),
                "delta_structural_stop_rate": None if not eligible else float(rb["structural_stop_rate"] - ra["structural_stop_rate"]),
            })

    # Exclusive max-state bins are secondary/report-only.
    resolved = df[df.resolved.astype(str).str.lower().isin(["true", "1"])].copy()
    conf = present(resolved["confirmed_time"])
    core = present(resolved["core_time"])
    resolved["exclusive_bin"] = np.where(~conf, "SEED_ONLY", np.where(~core, "CONFIRMED_NO_CORE", "CORE_REACHED"))
    exclusive = []
    for seg in segments:
        sg = resolved[segment_mask(resolved, seg)]
        for name in ["SEED_ONLY", "CONFIRMED_NO_CORE", "CORE_REACHED"]:
            exclusive.append({"segment": seg, "exclusive_bin": name, **metrics(sg[sg.exclusive_bin == name])})

    inv = invariant_checks(df)
    overall = "PASS" if (all(baseline_checks.values()) and inv["all_pass"] and all_primary_eligible and all_primary_pass and every_eligible_pass) else "FAIL"
    audit = {
        "status": "R24_STATE_MONOTONICITY_HISTORICAL_DIAGNOSTIC_ONLY",
        "model": "MASTER_BTC_TREND_V3_R2_4",
        "production_promotion_evidence": False,
        "spec_status": SPEC["status"],
        "baseline_identity": {"metrics": baseline, "checks": baseline_checks},
        "primary_test": {
            "minimum_n": min_n,
            "metric": "mean_terminal_R",
            "all_segment_required_comparisons_eligible": all_primary_eligible,
            "all_segment_required_comparisons_pass": all_primary_pass,
            "every_eligible_directional_comparison_pass": every_eligible_pass,
            "result": overall,
        },
        "reached_state_metrics": table,
        "adjacent_comparisons": comparisons,
        "exclusive_max_state_secondary": exclusive,
        "structural_invariants": inv,
        "evidence_firewall": SPEC["evidence_firewall"],
    }
    pd.DataFrame(table).to_csv(OUT / "r24_reached_state_metrics.csv", index=False)
    pd.DataFrame(comparisons).to_csv(OUT / "r24_state_adjacent_comparisons.csv", index=False)
    pd.DataFrame(exclusive).to_csv(OUT / "r24_exclusive_state_secondary.csv", index=False)
    (OUT / "r24_state_monotonicity_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()
