from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from btc_trend_v30.r23.r23_detector import R23Engine

HERE = Path(__file__).resolve().parent
OUT = HERE / "output/detection_lead_diagnostic"
OUT.mkdir(parents=True, exist_ok=True)

EVAL_START = pd.Timestamp("2021-01-01T00:00:00Z")
EVAL_END = pd.Timestamp("2026-09-05T00:00:00Z")
CADENCE = pd.Timedelta(hours=4)


def _find_one(root: Path, suffix: str) -> Path:
    hits = [p for p in root.rglob(suffix) if p.is_file()]
    if len(hits) != 1:
        raise SystemExit(f"EXPECTED_ONE_{suffix}:{[str(x) for x in hits]}")
    return hits[0]


def _ohlcv_daily(path: Path) -> pd.DataFrame:
    x = pd.read_csv(path)
    x["time"] = pd.to_datetime(x["date"], utc=True, format="mixed")
    x = x.sort_values("time").drop_duplicates("time").set_index("time")
    return x[["open", "high", "low", "close", "volume"]].astype(float)


def _ohlcv_h4(path: Path) -> pd.DataFrame:
    x = pd.read_csv(path)
    x["time"] = pd.to_datetime(x["time"], utc=True, format="mixed")
    x = x.sort_values("time").drop_duplicates("time").set_index("time")
    return x[["open", "high", "low", "close", "volume"]].astype(float)


def _parse_times(df: pd.DataFrame, cols: Iterable[str]) -> pd.DataFrame:
    x = df.copy()
    for c in cols:
        if c in x.columns:
            x[c] = pd.to_datetime(x[c], utc=True, errors="coerce", format="mixed")
    return x


def _seed_fp(df: pd.DataFrame, ts_col: str) -> list[str]:
    z = _parse_times(df, [ts_col])
    out = []
    for _, r in z.iterrows():
        out.append(f"{r['direction']}|{r['route']}|{pd.Timestamp(r[ts_col]).isoformat()}|{float(r['reference']):.8f}")
    return sorted(out)


def _stats_days(s: pd.Series) -> dict:
    x = pd.to_numeric(s, errors="coerce").dropna().astype(float)
    if x.empty:
        return {"n": 0, "mean": None, "median": None, "p25": None, "p75": None, "pct_ge_1d": None, "pct_ge_3d": None, "pct_ge_7d": None}
    return {
        "n": int(len(x)),
        "mean": float(x.mean()),
        "median": float(x.median()),
        "p25": float(x.quantile(0.25)),
        "p75": float(x.quantile(0.75)),
        "pct_ge_1d": float((x >= 1.0).mean()),
        "pct_ge_3d": float((x >= 3.0).mean()),
        "pct_ge_7d": float((x >= 7.0).mean()),
    }


def _build_chains(states: pd.DataFrame, executed_keys: set[tuple[str, pd.Timestamp]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    z = states.copy().sort_values(["direction", "timestamp"]).reset_index(drop=True)
    chain_ids = []
    chain_id = 0
    prev_dir = None
    prev_ts = None
    for _, r in z.iterrows():
        d = str(r["direction"])
        t = pd.Timestamp(r["timestamp"])
        if prev_dir != d or prev_ts is None or (t - prev_ts) != CADENCE:
            chain_id += 1
        chain_ids.append(chain_id)
        prev_dir, prev_ts = d, t
    z["chain_id"] = chain_ids
    z["executed_entry_here"] = [(str(r.direction), pd.Timestamp(r.timestamp)) in executed_keys for r in z.itertuples()]

    chains = []
    for cid, q in z.groupby("chain_id", sort=True):
        q = q.sort_values("timestamp")
        start = pd.Timestamp(q["timestamp"].iloc[0])
        end = pd.Timestamp(q["timestamp"].iloc[-1])
        priority = q[q["priority_extreme_near"].astype(bool)]
        rawseed = q[q["r21_seed_present"].astype(bool)]
        executed = q[q["executed_entry_here"].astype(bool)]
        chains.append({
            "chain_id": int(cid),
            "direction": str(q["direction"].iloc[0]),
            "start_time": start,
            "end_time": end,
            "bars": int(len(q)),
            "duration_days": float((end - start + CADENCE) / pd.Timedelta(days=1)),
            "had_early_detect_state": bool((q["state"] == "EARLY_DETECT").any()),
            "had_priority": bool(len(priority)),
            "first_priority_time": priority["timestamp"].iloc[0] if len(priority) else pd.NaT,
            "had_raw_seed": bool(len(rawseed)),
            "first_raw_seed_time": rawseed["timestamp"].iloc[0] if len(rawseed) else pd.NaT,
            "raw_seed_count": int(len(rawseed)),
            "had_executed_entry": bool(len(executed)),
            "first_executed_entry_time": executed["timestamp"].iloc[0] if len(executed) else pd.NaT,
            "executed_entry_count": int(len(executed)),
        })
    return z, pd.DataFrame(chains)


def _entry_leads(states_with_chain: pd.DataFrame, chains: pd.DataFrame, positions: pd.DataFrame) -> pd.DataFrame:
    chain_lookup = states_with_chain.set_index(["direction", "timestamp"])["chain_id"].to_dict()
    chain_meta = chains.set_index("chain_id").to_dict("index")
    rows = []
    for _, p in positions.sort_values("seed_time").iterrows():
        key = (str(p["direction"]), pd.Timestamp(p["seed_time"]))
        if key not in chain_lookup:
            raise RuntimeError(f"EXECUTED_ENTRY_WITHOUT_R23_STATE:{key}")
        cid = int(chain_lookup[key])
        c = chain_meta[cid]
        seed = pd.Timestamp(p["seed_time"])
        start = pd.Timestamp(c["start_time"])
        prio = pd.Timestamp(c["first_priority_time"]) if pd.notna(c["first_priority_time"]) else pd.NaT
        q = states_with_chain[states_with_chain["chain_id"] == cid].sort_values("timestamp")
        early_only = q[(q["state"] == "EARLY_DETECT") & (q["timestamp"] <= seed)]
        first_early_only = pd.Timestamp(early_only["timestamp"].iloc[0]) if len(early_only) else pd.NaT
        rows.append({
            "episode_id": p.get("episode_id"),
            "direction": str(p["direction"]),
            "route": str(p["route"]),
            "seed_time": seed,
            "chain_id": cid,
            "awareness_start_time": start,
            "awareness_lead_days": float((seed - start) / pd.Timedelta(days=1)),
            "first_early_detect_time": first_early_only,
            "early_detect_lead_days": float((seed - first_early_only) / pd.Timedelta(days=1)) if pd.notna(first_early_only) else np.nan,
            "first_priority_time": prio,
            "priority_lead_days": float((seed - prio) / pd.Timedelta(days=1)) if pd.notna(prio) else np.nan,
            "had_explicit_early_detect": bool(pd.notna(first_early_only)),
        })
    return pd.DataFrame(rows)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("USAGE: script.py CANONICAL_ARTIFACT_ROOT R21_ARTIFACT_ROOT")
    canonical_root = Path(sys.argv[1])
    r21_root = Path(sys.argv[2])

    daily_path = _find_one(canonical_root, "btc_usdt_1d_matrix.csv")
    h4_path = _find_one(canonical_root, "btc_usdt_complete_4h_matrix.csv")
    pos_path = _find_one(r21_root, "r21_positions.csv")
    seed_path = _find_one(r21_root, "r21_seed_candidates.csv")
    audit_hits = [p for p in r21_root.rglob("audit.json") if "/r21/output/historical_replay/" in p.as_posix()]
    if len(audit_hits) != 1:
        raise SystemExit(f"EXPECTED_ONE_R21_AUDIT:{[str(x) for x in audit_hits]}")
    parent_audit = json.loads(audit_hits[0].read_text(encoding="utf-8"))

    daily = _ohlcv_daily(daily_path)
    h4 = _ohlcv_h4(h4_path)
    parent_positions = _parse_times(pd.read_csv(pos_path), ["seed_time", "exit_time"])
    parent_seeds = _parse_times(pd.read_csv(seed_path), ["timestamp"])

    engine = R23Engine()
    regenerated = engine.scan_seed_candidates(daily, h4)
    parent_fp = _seed_fp(parent_seeds, "timestamp")
    regen_fp = _seed_fp(regenerated, "timestamp")
    if parent_fp != regen_fp:
        only_parent = len(set(parent_fp) - set(regen_fp))
        only_regen = len(set(regen_fp) - set(parent_fp))
        raise RuntimeError(f"R21_SEED_IDENTITY_MISMATCH:parent={len(parent_fp)} regen={len(regen_fp)} only_parent={only_parent} only_regen={only_regen}")

    states = engine.detect_states(daily, h4)
    states = _parse_times(states, ["timestamp"])
    states = states[(states["timestamp"] >= EVAL_START) & (states["timestamp"] < EVAL_END)].copy()
    positions = parent_positions[(parent_positions["seed_time"] >= EVAL_START) & (parent_positions["seed_time"] < EVAL_END)].copy()
    exec_keys = {(str(r.direction), pd.Timestamp(r.seed_time)) for r in positions.itertuples()}

    states2, chains = _build_chains(states, exec_keys)
    leads = _entry_leads(states2, chains, positions)

    by_direction = {}
    for direction in ["LONG", "SHORT"]:
        q = leads[leads["direction"] == direction]
        c = chains[chains["direction"] == direction]
        by_direction[direction] = {
            "executed_entries": int(len(q)),
            "awareness_lead_days": _stats_days(q["awareness_lead_days"]),
            "explicit_early_detect_lead_days": _stats_days(q["early_detect_lead_days"]),
            "priority_lead_days": _stats_days(q["priority_lead_days"]),
            "awareness_chains": int(len(c)),
            "chains_reaching_priority": int(c["had_priority"].sum()),
            "chains_reaching_raw_seed": int(c["had_raw_seed"].sum()),
            "chains_reaching_executed_entry": int(c["had_executed_entry"].sum()),
            "early_to_raw_seed_conversion": float(c["had_raw_seed"].mean()) if len(c) else None,
            "early_to_executed_entry_conversion": float(c["had_executed_entry"].mean()) if len(c) else None,
            "priority_to_raw_seed_conversion": float(c.loc[c["had_priority"], "had_raw_seed"].mean()) if c["had_priority"].any() else None,
            "noise_chain_count_no_raw_seed": int((~c["had_raw_seed"]).sum()),
            "noise_duration_days_no_raw_seed": _stats_days(c.loc[~c["had_raw_seed"], "duration_days"]),
        }

    overall = {
        "executed_entries": int(len(leads)),
        "awareness_lead_days": _stats_days(leads["awareness_lead_days"]),
        "explicit_early_detect_lead_days": _stats_days(leads["early_detect_lead_days"]),
        "priority_lead_days": _stats_days(leads["priority_lead_days"]),
        "executed_entries_with_explicit_early_detect": int(leads["had_explicit_early_detect"].sum()),
        "awareness_chains": int(len(chains)),
        "chains_reaching_priority": int(chains["had_priority"].sum()),
        "chains_reaching_raw_seed": int(chains["had_raw_seed"].sum()),
        "chains_reaching_executed_entry": int(chains["had_executed_entry"].sum()),
        "early_to_raw_seed_conversion": float(chains["had_raw_seed"].mean()) if len(chains) else None,
        "early_to_executed_entry_conversion": float(chains["had_executed_entry"].mean()) if len(chains) else None,
        "priority_to_raw_seed_conversion": float(chains.loc[chains["had_priority"], "had_raw_seed"].mean()) if chains["had_priority"].any() else None,
        "noise_chain_count_no_raw_seed": int((~chains["had_raw_seed"]).sum()),
        "noise_duration_days_no_raw_seed": _stats_days(chains.loc[~chains["had_raw_seed"], "duration_days"]),
    }

    audit = {
        "status": "R23_DETECTION_LEAD_HISTORICAL_DIAGNOSTIC_ONLY",
        "model": "MASTER_BTC_TREND_V3_R2_3",
        "evaluation_window": {"start": str(EVAL_START), "end_exclusive": str(EVAL_END)},
        "source_identity": {
            "canonical_run_id": 34084074525,
            "canonical_artifact_id": 10004727447,
            "canonical_artifact_sha256": "11362981be0938ec9c5f4d2aea00d1db5be5fc86ec41db7964f6fb83dc7bc8a3",
            "r21_run_id": 34116514316,
            "r21_artifact_id": 10016474113,
            "r21_artifact_sha256": "4bf0ea6a8fca5b887664a6dacad4f2d015330b06627709a464ec1b1ee41d5044",
            "r21_seed_identity_count": int(len(parent_fp)),
            "r21_seed_regeneration_exact": True,
            "r21_executed_positions": int(len(positions)),
            "parent_model": parent_audit.get("model"),
        },
        "methodology": {
            "awareness_chain": "same direction detection states contiguous at exact 4H cadence; any missing 4H detection row breaks the chain",
            "awareness_lead": "executed R2.1 seed time minus first state timestamp in its contiguous R2.3 awareness chain",
            "explicit_early_detect_lead": "executed R2.1 seed time minus first EARLY_DETECT state in same chain; N/A if chain began at PRIORITY_WATCH",
            "priority_lead": "executed R2.1 seed time minus first priority_extreme_near=true state in same chain",
            "conversion": "chain-level descriptive conversion; no promotion gate predeclared",
            "noise": "awareness chain that never reaches any raw R2.1 seed candidate",
            "execution": "R2.3 detector has zero order authority; actual execution remains frozen R2.1",
        },
        "overall": overall,
        "by_direction": by_direction,
        "performance_identity_note": {
            "r23_execution_engine": "FROZEN_R2_1_UNCHANGED",
            "r21_historical_metrics_are_not_recomputed": True,
            "r21_long_expectancy_mean_R": parent_audit["summary"]["long_expectancy"]["mean_R"],
            "r21_short_expectancy_mean_R": parent_audit["summary"]["short_expectancy"]["mean_R"],
            "r21_mcr90_mean": parent_audit["summary"]["mcr90_mean"],
            "r21_mcr365_mean": parent_audit["summary"]["mcr365_mean"],
        },
        "evidence_firewall": [
            "This is post-freeze historical diagnostic evidence only.",
            "No detector threshold or R2.1 execution rule may be changed from these results inside frozen R2.3.",
            "No hard PASS/FAIL promotion threshold is invented after observing detection-lead results.",
            "Forward untouched OOS remains required for production promotion.",
        ],
    }

    states2.to_csv(OUT / "r23_detection_states.csv", index=False)
    chains.to_csv(OUT / "r23_awareness_chains.csv", index=False)
    leads.to_csv(OUT / "r23_executed_entry_leads.csv", index=False)
    (OUT / "r23_detection_lead_audit.json").write_text(json.dumps(audit, indent=2, default=str), encoding="utf-8")
    print(json.dumps(audit, indent=2, default=str))


if __name__ == "__main__":
    main()
