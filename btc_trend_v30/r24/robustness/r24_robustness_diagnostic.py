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

SPEC_PATH = HERE / "R24_ROBUSTNESS_SPEC_V1.json"
R20_FROZEN = BTC_ROOT / "r20/r20_frozen_config_rev2.json"
R24_FROZEN = BTC_ROOT / "r24/r24_frozen_config.json"
OUT_ROOT = HERE / "output"
VARIANT_ROOT = OUT_ROOT / "variants"
SUMMARY_PATH = OUT_ROOT / "r24_robustness_audit.json"
VARIANT_CSV = OUT_ROOT / "r24_robustness_variant_summary.csv"


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
    executed = int(c.get("executed_r20_episodes", c.get("executed_r24_episodes", 0)))
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
    base.ohlcv_tf = r24run.ohlcv_tf_mixed_iso
    base.R20Engine = r24run.ReplayEngine
    base.simulate_episode = r24run.simulate_episode_r24

    with contextlib.redirect_stdout(io.StringIO()):
        base.main()
    audit = json.loads((out / "audit.json").read_text(encoding="utf-8"))
    return compact_metrics(audit), out


def daily_with_regime() -> pd.DataFrame:
    raw = pd.read_csv(base.DATA1D)
    d = base.ohlcv_daily(raw).copy()
    d["ema200"] = d["close"].ewm(span=200, adjust=False, min_periods=200).mean()
    d["ema200_slope20"] = d["ema200"] - d["ema200"].shift(20)
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


def bear_short_bucket(out: Path, cfg: dict, min_n: int, floor_r: float) -> dict:
    p = pd.read_csv(out / "r20_positions.csv")
    p["seed_time"] = pd.to_datetime(p["seed_time"], utc=True, format="mixed")
    p["marked_R_at_end"] = pd.to_numeric(p["marked_R_at_end"], errors="raise")
    d = daily_with_regime()
    p["regime"] = [regime_at(d, t, cfg) for t in p["seed_time"]]
    q = p[(p.direction == "SHORT") & (p.regime == "BEAR")]
    n = int(len(q))
    mean_r = float(q.marked_R_at_end.mean()) if n else None
    eligible = n >= min_n
    passed = bool(mean_r >= floor_r) if eligible and mean_r is not None else None
    return {"n": n, "mean_R": mean_r, "eligible": eligible, "floor_R": floor_r, "pass_floor": passed}


def main() -> None:
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    r20_cfg = json.loads(R20_FROZEN.read_text(encoding="utf-8"))
    r24_cfg = json.loads(R24_FROZEN.read_text(encoding="utf-8"))
    if r24_cfg.get("status") != "FINAL_FROZEN_NO_REPLAY":
        raise SystemExit("R24_PARENT_NOT_FROZEN")

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    VARIANT_ROOT.mkdir(parents=True, exist_ok=True)

    expected = spec["baseline_expected"]
    baseline_metrics, baseline_out = run_variant("BASELINE_IDENTITY", r20_cfg)
    identity_checks = {
        k: (baseline_metrics[k] == v if isinstance(v, int) else close_enough(baseline_metrics[k], v))
        for k, v in expected.items()
    }
    if not all(identity_checks.values()):
        raise SystemExit(f"R24_BASELINE_IDENTITY_FAIL:{identity_checks}:{baseline_metrics}")

    checks = spec["edge_preservation_checks"]
    min_n = int(spec["secondary_audit"]["minimum_bucket_n"])
    floor_r = float(spec["secondary_audit"]["bear_short_floor_R"])
    rows = []
    preserved = 0
    bear_short_passed = 0

    for v in spec["variants"]:
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

    robust_class = "STRONG" if preserved >= 8 else "MODERATE" if preserved >= 6 else "FRAGILE"
    variant_df = pd.DataFrame(rows)
    variant_df.to_csv(VARIANT_CSV, index=False)

    audit = {
        "status": "R24_ROBUSTNESS_HISTORICAL_DIAGNOSTIC_ONLY",
        "model": "MASTER_BTC_TREND_V3_R2_4",
        "execution_engine": "FROZEN_R2_4_RISK_OVERLAY_UNCHANGED",
        "baseline_identity": {"checks": identity_checks, "metrics": baseline_metrics},
        "robustness": {
            "variants_total": len(rows),
            "variants_preserving_all_six_checks": preserved,
            "preservation_rate": preserved / len(rows),
            "classification": robust_class,
            "failed_variants": variant_df.loc[~variant_df.edge_preserved, "variant_id"].tolist(),
        },
        "secondary_audit": {
            "bear_short_floor_R": floor_r,
            "minimum_bucket_n": min_n,
            "variants_with_bear_short_bucket_passing_floor": bear_short_passed,
            "bear_short_failed_variants": variant_df.loc[variant_df.bear_short_pass_floor == False, "variant_id"].tolist(),
            "seed_lag_not_used_for_robustness_classification": True,
        },
        "evidence_firewall": [
            "Historical 2021-2026 robustness results are diagnostic only.",
            "No variant can replace or alter frozen R2.4.",
            "No best historical variant may be adopted.",
            "Forward untouched OOS and full Risk Governor remain required for production promotion."
        ],
    }
    SUMMARY_PATH.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()
