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
from r21 import r21_historical_diagnostic_replay_runner as r21run

SPEC_PATH = HERE / "R21_ROBUSTNESS_CYCLE_SPEC_V1.json"
R20_FROZEN = BTC_ROOT / "r20/r20_frozen_config_rev2.json"
R21_FROZEN = BTC_ROOT / "r21/r21_frozen_config.json"
OUT_ROOT = HERE / "output"
VARIANT_ROOT = OUT_ROOT / "variants"
SUMMARY_PATH = OUT_ROOT / "r21_robustness_cycle_audit.json"
VARIANT_CSV = OUT_ROOT / "robustness_variant_summary.csv"
CYCLE_CSV = OUT_ROOT / "cycle_bucket_summary.csv"

BASELINE_EXPECTED = {
    "executed_episodes": 69,
    "mcr90_mean": 0.2363081812721231,
    "mcr365_mean": 0.3656408642162458,
    "long_expectancy_mean_R": 0.8346097626774698,
    "short_expectancy_mean_R": 0.03469379995835538,
    "confirmed_false_start_rate": 0.0,
    "capture_to_loss_ratio": 2.300161761460656,
    "median_seed_lag_days": 10.166666666666668,
}


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
    counts = audit.get("counts", {})
    executed = int(counts.get("executed_r20_episodes", counts.get("executed_r21_episodes", 0)))
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


def run_variant(variant_id: str, cfg: dict) -> tuple[dict, Path]:
    out = VARIANT_ROOT / variant_id
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    cfg_path = out / "variant_config.json"
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    base.FREEZE = cfg_path
    base.OUT = out
    base.ohlcv_tf = r21run.ohlcv_tf_mixed_iso
    base.R20Engine = r21run.ReplayEngine
    base.simulate_episode = r21run.simulate_episode_r21

    with contextlib.redirect_stdout(io.StringIO()):
        base.main()
    audit = json.loads((out / "audit.json").read_text(encoding="utf-8"))
    return compact_metrics(audit), out


def close_enough(a: float, b: float, tol: float = 1e-12) -> bool:
    return bool(abs(float(a) - float(b)) <= tol)


def daily_with_regime() -> pd.DataFrame:
    raw = pd.read_csv(base.DATA1D)
    d = base.ohlcv_daily(raw)
    ema200 = d["close"].ewm(span=200, adjust=False, min_periods=200).mean()
    slope20 = ema200 - ema200.shift(20)
    d = d.copy()
    d["ema200"] = ema200
    d["ema200_slope20"] = slope20
    return d


def regime_at(d: pd.DataFrame, seed_time: pd.Timestamp, cfg: dict) -> str:
    i = base.daily_source_index_at(d, pd.Timestamp(seed_time), cfg)
    if i < 0:
        return "TRANSITION"
    r = d.iloc[i]
    vals = [r.get("close"), r.get("ema200"), r.get("ema200_slope20")]
    if not all(pd.notna(v) and np.isfinite(float(v)) for v in vals):
        return "TRANSITION"
    if float(r.close) > float(r.ema200) and float(r.ema200_slope20) > 0:
        return "BULL"
    if float(r.close) < float(r.ema200) and float(r.ema200_slope20) < 0:
        return "BEAR"
    return "TRANSITION"


def cycle_tables(baseline_out: Path, cfg: dict, spec: dict) -> tuple[pd.DataFrame, bool, bool]:
    p = pd.read_csv(baseline_out / "r20_positions.csv")
    p["seed_time"] = pd.to_datetime(p["seed_time"], utc=True, format="mixed")
    p["marked_R_at_end"] = pd.to_numeric(p["marked_R_at_end"], errors="raise")
    d = daily_with_regime()
    p["regime"] = [regime_at(d, t, cfg) for t in p["seed_time"]]
    p["year"] = p["seed_time"].dt.year.astype(int)

    floor = float(spec["cycle"]["expectancy_floor_R"])
    min_n = int(spec["cycle"]["minimum_bucket_n"])
    rows = []
    primary_ok = True
    secondary_ok = True

    for bucket_type, col in [("REGIME", "regime"), ("YEAR", "year")]:
        for (direction, bucket), q in p.groupby(["direction", col], dropna=False):
            n = int(len(q))
            mean_r = float(q["marked_R_at_end"].mean())
            eligible = n >= min_n
            passed = (mean_r >= floor) if eligible else None
            if bucket_type == "REGIME" and eligible and not passed:
                primary_ok = False
            if bucket_type == "YEAR" and eligible and not passed:
                secondary_ok = False
            rows.append({
                "bucket_type": bucket_type,
                "direction": str(direction),
                "bucket": str(bucket),
                "n": n,
                "mean_R": mean_r,
                "eligible_n_ge_10": eligible,
                "floor_R": floor,
                "pass_floor": passed,
            })
    return pd.DataFrame(rows), primary_ok, secondary_ok


def main() -> None:
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    frozen_cfg = json.loads(R20_FROZEN.read_text(encoding="utf-8"))
    r21_cfg = json.loads(R21_FROZEN.read_text(encoding="utf-8"))
    if r21_cfg.get("status") != "FINAL_FROZEN_NO_REPLAY":
        raise SystemExit("R21_PARENT_NOT_FROZEN")
    if frozen_cfg.get("status") != "FINAL_FROZEN_NO_REPLAY_REV2":
        raise SystemExit("R20_REV2_PARENT_NOT_FROZEN")

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    VARIANT_ROOT.mkdir(parents=True, exist_ok=True)

    baseline_metrics, baseline_out = run_variant("BASELINE_IDENTITY", frozen_cfg)
    identity_checks = {
        k: (baseline_metrics[k] == v if isinstance(v, int) else close_enough(baseline_metrics[k], v))
        for k, v in BASELINE_EXPECTED.items()
    }
    if not all(identity_checks.values()):
        raise SystemExit(f"R21_BASELINE_IDENTITY_FAIL:{identity_checks}:{baseline_metrics}")

    checks = spec["robustness"]["edge_preservation_checks"]
    rows = []
    preserved = 0
    for v in spec["robustness"]["variants"]:
        vid = str(v["id"])
        cfg = apply_changes(frozen_cfg, v["changes"])
        metrics, _ = run_variant(vid, cfg)
        ok = edge_preserved(metrics, checks)
        preserved += int(ok)
        rows.append({"variant_id": vid, "edge_preserved": ok, **metrics, "changes_json": json.dumps(v["changes"], sort_keys=True)})
        print(json.dumps({"variant": vid, "edge_preserved": ok, **metrics}))

    if preserved >= 8:
        robust_class = "STRONG"
    elif preserved >= 6:
        robust_class = "MODERATE"
    else:
        robust_class = "FRAGILE"

    variant_df = pd.DataFrame(rows)
    variant_df.to_csv(VARIANT_CSV, index=False)

    cycle_df, cycle_primary_pass, cycle_secondary_pass = cycle_tables(baseline_out, frozen_cfg, spec)
    cycle_df.to_csv(CYCLE_CSV, index=False)

    audit = {
        "status": "R21_ROBUSTNESS_CYCLE_HISTORICAL_DIAGNOSTIC_ONLY",
        "model": "MASTER_BTC_TREND_V3_R2_1",
        "execution_engine": "FROZEN_R2_1_UNCHANGED",
        "baseline_identity": {"checks": identity_checks, "metrics": baseline_metrics},
        "robustness": {
            "variants_total": len(rows),
            "variants_preserving_all_six_checks": preserved,
            "preservation_rate": preserved / len(rows),
            "classification": robust_class,
            "classification_is_diagnostic_not_production_gate": True,
            "failed_variants": variant_df.loc[~variant_df["edge_preserved"], "variant_id"].tolist(),
        },
        "cycle": {
            "primary_direction_x_causal_regime_gate": "PASS" if cycle_primary_pass else "FAIL",
            "secondary_direction_x_year_audit": "PASS" if cycle_secondary_pass else "FAIL",
            "minimum_n": int(spec["cycle"]["minimum_bucket_n"]),
            "floor_R": float(spec["cycle"]["expectancy_floor_R"]),
            "eligible_regime_buckets": int(((cycle_df.bucket_type == "REGIME") & cycle_df.eligible_n_ge_10).sum()),
            "eligible_year_buckets": int(((cycle_df.bucket_type == "YEAR") & cycle_df.eligible_n_ge_10).sum()),
            "failed_eligible_buckets": cycle_df.loc[(cycle_df.eligible_n_ge_10) & (cycle_df.pass_floor == False), ["bucket_type","direction","bucket","n","mean_R"]].to_dict("records"),
        },
        "evidence_firewall": [
            "Historical 2021-2026 robustness/cycle results are diagnostic only.",
            "No variant can replace or alter frozen R2.1.",
            "No best historical variant may be adopted.",
            "Forward untouched OOS and full Risk Governor remain required for production promotion."
        ],
    }
    SUMMARY_PATH.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()
