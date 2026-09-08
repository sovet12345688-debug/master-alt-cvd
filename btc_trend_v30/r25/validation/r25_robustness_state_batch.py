from __future__ import annotations

import contextlib
import copy
import io
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
BTC_ROOT = HERE.parents[1]
sys.path.insert(0, str(BTC_ROOT))

from r20 import r20_historical_diagnostic_replay as base
from r24 import r24_historical_diagnostic_replay_runner as r24run
from r25 import r25_historical_diagnostic_replay_runner as r25run

SPEC_PATH = HERE / "R25_ROBUSTNESS_STATE_BATCH_SPEC.json"
R20_FROZEN = BTC_ROOT / "r20/r20_frozen_config_rev2.json"
R25_FROZEN = BTC_ROOT / "r25/r25_frozen_config.json"
OUT_ROOT = HERE / "output"
VARIANT_ROOT = OUT_ROOT / "variants"
AUDIT_PATH = OUT_ROOT / "r25_robustness_state_batch_audit.json"
VARIANT_CSV = OUT_ROOT / "r25_robustness_variant_summary.csv"
STATE_CSV = OUT_ROOT / "r25_reached_state_metrics.csv"
STATE_COMPARE_CSV = OUT_ROOT / "r25_state_adjacent_comparisons.csv"
EXCLUSIVE_CSV = OUT_ROOT / "r25_exclusive_state_secondary.csv"


def apply_changes(cfg: dict, changes: dict) -> dict:
    x = copy.deepcopy(cfg)
    if "volume_thresholds_multiplier" in changes:
        m = float(changes["volume_thresholds_multiplier"])
        x["long"]["seed"]["breakout"]["volume_ratio_min"] *= m
        x["long"]["seed"]["reclaim"]["daily_volume_ratio_min"] *= m
        x["short"]["seed"]["breakdown"]["volume_ratio_min"] *= m
        x["short"]["seed"]["failed_retest"]["volume_ratio_min"] *= m
    if "watch_distance_atr_multiplier" in changes:
        m = float(changes["watch_distance_atr_multiplier"])
        x["long"]["watch"]["distance_to_20d_high_atr_max"] *= m
        x["short"]["watch"]["distance_to_20d_low_atr_max"] *= m
    if "seed_stop_atr_multiplier" in changes:
        m = float(changes["seed_stop_atr_multiplier"])
        x["long"]["seed"]["stop"]["atr_multiple"] *= m
        x["short"]["seed"]["stop"]["atr_multiple"] *= m
    if "chandelier_atr_multiplier" in changes:
        m = float(changes["chandelier_atr_multiplier"])
        x["features"]["chandelier_atr_multiple"] *= m
        x["holding"]["long"]["chandelier_atr_multiple"] *= m
        x["holding"]["short"]["chandelier_atr_multiple"] *= m
    if "long_confirm_window_days" in changes:
        x["long"]["confirm"]["window_days"] = int(changes["long_confirm_window_days"])
    if "short_confirm_window_days" in changes:
        x["short"]["confirm"]["window_days"] = int(changes["short_confirm_window_days"])
    return x


def compact_metrics(audit: dict) -> dict:
    s = audit["summary"]
    c = audit.get("counts", {})
    executed = int(c.get("executed_r20_episodes", c.get("executed_r25_episodes", 0)))
    return {
        "executed_episodes": executed,
        "mcr90_mean": float(s["mcr90_mean"]),
        "mcr365_mean": float(s["mcr365_mean"]),
        "long_expectancy_mean_R": float(s["long_expectancy"]["mean_R"]),
        "short_expectancy_mean_R": float(s["short_expectancy"]["mean_R"]),
        "confirmed_false_start_rate": float(s["confirmed_false_start_rate"]),
        "capture_to_loss_ratio": float(s["capture_to_loss_ratio"]),
        "median_seed_lag_days": float(s["median_seed_lag_days_90d_truth"]),
        "episode_order_mdd_R": float(s["episode_order_mdd_R"]),
    }


def edge_preserved(m: dict, checks: dict) -> bool:
    return bool(
        m["mcr90_mean"] >= float(checks["mcr90_ge"])
        and m["mcr365_mean"] >= float(checks["mcr365_ge"])
        and m["long_expectancy_mean_R"] > float(checks["long_expectancy_gt"])
        and m["short_expectancy_mean_R"] > float(checks["short_expectancy_gt"])
        and m["confirmed_false_start_rate"] <= float(checks["confirmed_false_start_rate_le"])
        and m["capture_to_loss_ratio"] > float(checks["capture_to_loss_gt"])
    )


def close_enough(a: float, b: float, tol: float = 1e-12) -> bool:
    return bool(abs(float(a) - float(b)) <= tol)


def run_variant(variant_id: str, cfg: dict) -> tuple[dict, Path]:
    out = VARIANT_ROOT / variant_id
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    cfg_path = out / "variant_config.json"
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    base.FREEZE = cfg_path
    base.OUT = out
    base.ohlcv_tf = r25run.ohlcv_tf_mixed_iso
    base.R20Engine = r25run.ReplayEngine
    base.simulate_episode = r25run.simulate_episode_r25

    with contextlib.redirect_stdout(io.StringIO()):
        base.main()
    audit = json.loads((out / "audit.json").read_text(encoding="utf-8"))
    return compact_metrics(audit), out


def bear_short_bucket(out: Path, cfg: dict, min_n: int, floor_r: float) -> dict:
    p = pd.read_csv(out / "r20_positions.csv")
    cycle = r24run.cycle_recheck(p, cfg)
    b = cycle.get("bear_short_bucket") or {}
    n = int(b.get("n", 0))
    mean_r = b.get("mean_R")
    eligible = bool(n >= min_n)
    passed = bool(float(mean_r) >= floor_r) if eligible and mean_r is not None else None
    return {"n": n, "mean_R": mean_r, "eligible": eligible, "floor_R": floor_r, "pass_floor": passed}


def present(s: pd.Series) -> pd.Series:
    return s.notna() & (s.astype(str).str.len() > 0) & (s.astype(str).str.lower() != "nat")


def resolved_mask(df: pd.DataFrame) -> pd.Series:
    return df["resolved"].astype(str).str.lower().isin(["true", "1"])


def cohort_mask(df: pd.DataFrame, state: str) -> pd.Series:
    resolved = resolved_mask(df)
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


def state_metrics(g: pd.DataFrame) -> dict:
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
        "core_time_not_before_confirm": bool((~core.notna() | (conf.notna() & (core >= conf))).all()),
        "derisk_time_not_before_core": bool((~derisk.notna() | (core.notna() & (derisk >= core))).all()),
    }
    checks["all_pass"] = all(checks.values())
    return checks


def state_monotonicity(df: pd.DataFrame, spec: dict, baseline_expected: dict) -> dict:
    resolved = resolved_mask(df)
    baseline = {
        "episodes": int(len(df)),
        "long": int((df.direction == "LONG").sum()),
        "short": int((df.direction == "SHORT").sum()),
        "long_mean_R": float(df.loc[(df.direction == "LONG") & resolved, "marked_R_at_end"].mean()),
        "short_mean_R": float(df.loc[(df.direction == "SHORT") & resolved, "marked_R_at_end"].mean()),
    }
    baseline_checks = {
        "episodes": baseline["episodes"] == int(baseline_expected["executed_episodes"]),
        "long": baseline["long"] == 48,
        "short": baseline["short"] == 21,
        "long_mean_R": close_enough(baseline["long_mean_R"], baseline_expected["long_expectancy_mean_R"]),
        "short_mean_R": close_enough(baseline["short_mean_R"], baseline_expected["short_expectancy_mean_R"]),
    }

    states = spec["state_order"]
    segments = spec["segments"]
    min_n = int(spec["minimum_n_per_cohort"])
    table, lookup = [], {}
    for seg in segments:
        for state in states:
            g = df[segment_mask(df, seg) & cohort_mask(df, state)].copy()
            row = {"segment": seg, "state": state, **state_metrics(g)}
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

    rdf = df[resolved].copy()
    conf = present(rdf["confirmed_time"])
    core = present(rdf["core_time"])
    rdf["exclusive_bin"] = np.where(~conf, "SEED_ONLY", np.where(~core, "CONFIRMED_NO_CORE", "CORE_REACHED"))
    exclusive = []
    for seg in segments:
        sg = rdf[segment_mask(rdf, seg)]
        for name in ["SEED_ONLY", "CONFIRMED_NO_CORE", "CORE_REACHED"]:
            exclusive.append({"segment": seg, "exclusive_bin": name, **state_metrics(sg[sg.exclusive_bin == name])})

    inv = invariant_checks(df)
    overall = "PASS" if (all(baseline_checks.values()) and inv["all_pass"] and all_primary_eligible and all_primary_pass and every_eligible_pass) else "FAIL"
    pd.DataFrame(table).to_csv(STATE_CSV, index=False)
    pd.DataFrame(comparisons).to_csv(STATE_COMPARE_CSV, index=False)
    pd.DataFrame(exclusive).to_csv(EXCLUSIVE_CSV, index=False)
    return {
        "result": overall,
        "minimum_n": min_n,
        "baseline_identity": {"metrics": baseline, "checks": baseline_checks},
        "reached_state_metrics": table,
        "adjacent_comparisons": comparisons,
        "exclusive_max_state_secondary": exclusive,
        "structural_invariants": inv,
        "all_segment_required_comparisons_eligible": all_primary_eligible,
        "all_segment_required_comparisons_pass": all_primary_pass,
        "every_eligible_directional_comparison_pass": every_eligible_pass,
    }


def main() -> None:
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    r20_cfg = json.loads(R20_FROZEN.read_text(encoding="utf-8"))
    r25_cfg = json.loads(R25_FROZEN.read_text(encoding="utf-8"))
    if r25_cfg.get("status") != "FINAL_FROZEN_NO_REPLAY":
        raise SystemExit("R25_PARENT_NOT_FROZEN")

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    VARIANT_ROOT.mkdir(parents=True, exist_ok=True)

    expected = spec["baseline_expected"]
    baseline_metrics, baseline_out = run_variant("BASELINE_IDENTITY", r20_cfg)
    identity_checks = {
        k: (baseline_metrics[k] == v if isinstance(v, int) else close_enough(baseline_metrics[k], v))
        for k, v in expected.items()
    }
    if not all(identity_checks.values()):
        raise SystemExit(f"R25_BASELINE_IDENTITY_FAIL:{identity_checks}:{baseline_metrics}")

    baseline_positions = pd.read_csv(baseline_out / "r20_positions.csv")
    baseline_positions["marked_R_at_end"] = pd.to_numeric(baseline_positions["marked_R_at_end"], errors="raise")
    state_result = state_monotonicity(baseline_positions, spec["state_monotonicity"], expected)

    rob = spec["robustness"]
    checks = rob["edge_preservation_checks"]
    min_n = int(rob["secondary_audit"]["minimum_bucket_n"])
    floor_r = float(rob["secondary_audit"]["bear_short_floor_R"])
    rows = []
    preserved = 0
    bear_short_passed = 0
    for v in rob["variants"]:
        vid = str(v["id"])
        cfg = apply_changes(r20_cfg, v["changes"])
        metrics, out = run_variant(vid, cfg)
        ok = edge_preserved(metrics, checks)
        bs = bear_short_bucket(out, cfg, min_n, floor_r)
        preserved += int(ok)
        bear_short_passed += int(bs["pass_floor"] is True)
        row = {
            "variant_id": vid,
            "edge_preserved": ok,
            **metrics,
            "bear_short_n": bs["n"],
            "bear_short_mean_R": bs["mean_R"],
            "bear_short_pass_floor": bs["pass_floor"],
            "changes_json": json.dumps(v["changes"], sort_keys=True),
        }
        rows.append(row)
        print(json.dumps(row))

    variant_df = pd.DataFrame(rows)
    variant_df.to_csv(VARIANT_CSV, index=False)
    robust_class = "STRONG" if preserved >= 8 else "MODERATE" if preserved >= 6 else "FRAGILE"

    audit = {
        "status": "R25_ROBUSTNESS_AND_STATE_HISTORICAL_DIAGNOSTIC_ONLY",
        "model": "MASTER_BTC_TREND_V3_R2_5",
        "production_promotion_evidence": False,
        "frozen_r25_unchanged": True,
        "baseline_identity": {"checks": identity_checks, "metrics": baseline_metrics},
        "robustness": {
            "variants_total": len(rows),
            "variants_preserving_all_six_checks": preserved,
            "preservation_rate": preserved / len(rows),
            "classification": robust_class,
            "failed_variants": variant_df.loc[~variant_df.edge_preserved, "variant_id"].tolist(),
            "bear_short_floor_R": floor_r,
            "variants_with_bear_short_bucket_passing_floor": bear_short_passed,
            "bear_short_failed_variants": variant_df.loc[variant_df.bear_short_pass_floor == False, "variant_id"].tolist(),
            "seed_lag_not_used_for_classification": True,
        },
        "state_monotonicity": state_result,
        "evidence_firewall": spec["evidence_firewall"],
        "next_promotion_blocker": "FORWARD_UNTOUCHED_OOS",
    }
    AUDIT_PATH.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()
