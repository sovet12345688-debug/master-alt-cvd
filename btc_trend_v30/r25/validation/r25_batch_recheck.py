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
R25 = HERE.parent
BTC_ROOT = R25.parent
sys.path.insert(0, str(BTC_ROOT))

from r20 import r20_historical_diagnostic_replay as base
from r24 import r24_historical_diagnostic_replay_runner as r24run
from r25 import r25_historical_diagnostic_replay_runner as r25run

SPEC = json.loads((HERE / "R25_BATCH_RECHECK_SPEC.json").read_text(encoding="utf-8"))
R20_FROZEN = BTC_ROOT / "r20/r20_frozen_config_rev2.json"
OUT = HERE / "output"
VARIANTS = OUT / "variants"
OUT.mkdir(parents=True, exist_ok=True)
VARIANTS.mkdir(parents=True, exist_ok=True)


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


def compact(audit: dict) -> dict:
    s = audit["summary"]
    c = audit["counts"]
    return {
        "executed_episodes": int(c.get("executed_r20_episodes", 0)),
        "mcr90_mean": float(s["mcr90_mean"]),
        "mcr365_mean": float(s["mcr365_mean"]),
        "long_expectancy_mean_R": float(s["long_expectancy"]["mean_R"]),
        "short_expectancy_mean_R": float(s["short_expectancy"]["mean_R"]),
        "confirmed_false_start_rate": float(s["confirmed_false_start_rate"]),
        "capture_to_loss_ratio": float(s["capture_to_loss_ratio"]),
        "median_seed_lag_days": float(s["median_seed_lag_days_90d_truth"]),
        "episode_order_mdd_R": float(s["episode_order_mdd_R"]),
    }


def run_variant(vid: str, cfg: dict) -> tuple[dict, Path]:
    out = VARIANTS / vid
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    cfg_path = out / "variant_config.json"
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    base.FREEZE = cfg_path
    base.OUT = out
    base.ohlcv_tf = r25run.ohlcv_tf_mixed_iso
    base.R20Engine = r25run.ReplayEngine
    base.simulate_episode = r25run.simulate_episode_r25
    with contextlib.redirect_stdout(io.StringIO()):
        base.main()
    a = json.loads((out / "audit.json").read_text(encoding="utf-8"))
    return compact(a), out


def edge_ok(m: dict, c: dict) -> bool:
    return bool(
        m["mcr90_mean"] >= c["mcr90_ge"] and
        m["mcr365_mean"] >= c["mcr365_ge"] and
        m["long_expectancy_mean_R"] > c["long_expectancy_gt"] and
        m["short_expectancy_mean_R"] > c["short_expectancy_gt"] and
        m["confirmed_false_start_rate"] <= c["confirmed_false_start_rate_le"] and
        m["capture_to_loss_ratio"] > c["capture_to_loss_gt"]
    )


def close(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) <= 1e-12


def state_present(s: pd.Series) -> pd.Series:
    z = pd.to_datetime(s, utc=True, errors="coerce", format="mixed")
    return z.notna()


def state_metrics(g: pd.DataFrame) -> dict:
    r = pd.to_numeric(g["marked_R_at_end"], errors="raise")
    return {
        "n": int(len(g)),
        "mean_terminal_R": float(r.mean()) if len(g) else None,
        "positive_rate": float((r > 0).mean()) if len(g) else None,
        "structural_stop_rate": float((g["exit_reason"].astype(str) == "STRUCTURAL_STOP").mean()) if len(g) else None,
    }


def monotonicity(pos_path: Path) -> dict:
    df = pd.read_csv(pos_path)
    df["marked_R_at_end"] = pd.to_numeric(df["marked_R_at_end"], errors="raise")
    resolved = df["resolved"].astype(str).str.lower().isin(["true", "1"])
    conf = state_present(df["confirmed_time"])
    core = state_present(df["core_time"])
    derisk = state_present(df["derisk_time"])
    seed_ts = pd.to_datetime(df["seed_time"], utc=True, errors="coerce", format="mixed")
    conf_ts = pd.to_datetime(df["confirmed_time"], utc=True, errors="coerce", format="mixed")
    core_ts = pd.to_datetime(df["core_time"], utc=True, errors="coerce", format="mixed")
    derisk_ts = pd.to_datetime(df["derisk_time"], utc=True, errors="coerce", format="mixed")
    inv = {
        "no_core_without_confirm": bool((~core | conf).all()),
        "no_derisk_without_core": bool((~derisk | core).all()),
        "confirm_time_not_before_seed": bool((~conf | (conf_ts >= seed_ts)).all()),
        "core_time_not_before_confirm": bool((~core | (conf & (core_ts >= conf_ts))).all()),
        "derisk_time_not_before_core": bool((~derisk | (core & (derisk_ts >= core_ts))).all()),
    }
    inv["all_pass"] = all(inv.values())

    states = SPEC["state_monotonicity"]["states"]
    segs = SPEC["state_monotonicity"]["segments"]
    min_n = int(SPEC["state_monotonicity"]["minimum_n"])
    table, lookup = [], {}
    for seg in segs:
        sm = pd.Series(True, index=df.index) if seg == "ALL" else df["direction"].astype(str).eq(seg)
        masks = {
            "SEED_REACHED": resolved,
            "CONFIRMED_REACHED": resolved & conf,
            "CORE_REACHED": resolved & core,
        }
        for st in states:
            row = {"segment": seg, "state": st, **state_metrics(df[sm & masks[st]])}
            table.append(row); lookup[(seg, st)] = row

    comps = []
    all_required = True
    every_eligible = True
    for seg in segs:
        for a, b in zip(states[:-1], states[1:]):
            ra, rb = lookup[(seg, a)], lookup[(seg, b)]
            eligible = ra["n"] >= min_n and rb["n"] >= min_n
            delta = None if not eligible else rb["mean_terminal_R"] - ra["mean_terminal_R"]
            passed = None if not eligible else bool(delta >= 0.0)
            if seg == "ALL": all_required &= bool(eligible and passed)
            if eligible: every_eligible &= bool(passed)
            comps.append({"segment":seg,"from_state":a,"to_state":b,"eligible":eligible,"delta_mean_terminal_R":delta,"pass":passed})

    rdf = df[resolved].copy()
    rconf = state_present(rdf["confirmed_time"]); rcore = state_present(rdf["core_time"])
    rdf["exclusive_bin"] = np.where(~rconf, "SEED_ONLY", np.where(~rcore, "CONFIRMED_NO_CORE", "CORE_REACHED"))
    exclusive=[]
    for seg in segs:
        sg = rdf if seg == "ALL" else rdf[rdf.direction.astype(str).eq(seg)]
        for name in SPEC["state_monotonicity"]["secondary_exclusive_bins"]:
            exclusive.append({"segment":seg,"exclusive_bin":name,**state_metrics(sg[sg.exclusive_bin.eq(name)])})
    result = "PASS" if inv["all_pass"] and all_required and every_eligible else "FAIL"
    return {"result":result,"minimum_n":min_n,"reached_state_metrics":table,"adjacent_comparisons":comps,"exclusive_bins":exclusive,"invariants":inv}


def main() -> None:
    r20_cfg = json.loads(R20_FROZEN.read_text(encoding="utf-8"))
    expected = SPEC["baseline_expected"]
    baseline, bout = run_variant("BASELINE_IDENTITY", r20_cfg)
    identity = {k: (baseline[k] == v if isinstance(v, int) else close(baseline[k], v)) for k,v in expected.items()}
    if not all(identity.values()):
        raise SystemExit(f"R25_BASELINE_IDENTITY_FAIL:{identity}:{baseline}")

    rob = SPEC["robustness"]
    rows=[]
    for v in rob["variants"]:
        cfg=apply_changes(r20_cfg, v["changes"])
        m,out=run_variant(v["id"],cfg)
        ok=edge_ok(m,rob["checks"])
        p=pd.read_csv(out/"r20_positions.csv")
        cy=r24run.cycle_recheck(p,cfg)
        bs=cy.get("bear_short_bucket") or {}
        rows.append({"variant_id":v["id"],"edge_preserved":ok,**m,"bear_short_n":bs.get("n"),"bear_short_mean_R":bs.get("mean_R"),"bear_short_pass_floor":bs.get("pass_floor"),"changes":v["changes"]})
        print(json.dumps(rows[-1], default=str))
    n_pass=sum(int(r["edge_preserved"]) for r in rows)
    cls="STRONG" if n_pass>=rob["classification"]["STRONG_min_pass"] else "MODERATE" if n_pass>=rob["classification"]["MODERATE_min_pass"] else "FRAGILE"
    mono=monotonicity(bout/"r20_positions.csv")
    audit={
        "status":"R25_ROBUSTNESS_AND_STATE_HISTORICAL_RECHECK_ONLY",
        "model":SPEC["model"],
        "production_promotion_evidence":False,
        "baseline_identity":{"checks":identity,"metrics":baseline},
        "robustness":{"classification":cls,"variants_total":10,"variants_preserving_all_six_checks":n_pass,"preservation_rate":n_pass/10,"failed_variants":[r["variant_id"] for r in rows if not r["edge_preserved"]],"variants_with_bear_short_floor_pass":sum(int(r["bear_short_pass_floor"] is True) for r in rows),"variant_results":rows},
        "state_monotonicity":mono,
        "evidence_firewall":SPEC["evidence_firewall"],
    }
    pd.DataFrame(rows).to_csv(OUT/"r25_robustness_variant_summary.csv",index=False)
    pd.DataFrame(mono["reached_state_metrics"]).to_csv(OUT/"r25_state_reached_metrics.csv",index=False)
    pd.DataFrame(mono["adjacent_comparisons"]).to_csv(OUT/"r25_state_adjacent_comparisons.csv",index=False)
    pd.DataFrame(mono["exclusive_bins"]).to_csv(OUT/"r25_state_exclusive_bins.csv",index=False)
    (OUT/"r25_batch_recheck_audit.json").write_text(json.dumps(audit,indent=2,default=str),encoding="utf-8")
    print(json.dumps(audit,indent=2,default=str))

if __name__ == "__main__":
    main()
