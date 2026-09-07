from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def find_one(root: Path, name: str) -> Path:
    hits = list(root.rglob(name))
    if len(hits) != 1:
        raise SystemExit(f"EXPECTED_ONE_{name}:{[str(x) for x in hits]}")
    return hits[0]


def parse_time_cols(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    for c in ["seed_time", "confirmed_time", "core_time", "derisk_time", "exit_time", "timestamp"]:
        if c in x.columns:
            x[c] = pd.to_datetime(x[c], utc=True, errors="coerce", format="mixed")
    return x


def fp_row(r: pd.Series) -> str:
    ref = float(r["reference"])
    ts = pd.Timestamp(r["seed_time"]).isoformat()
    return f"{str(r['direction'])}|{str(r['route'])}|{ts}|{ref:.8f}"


def seed_fp_row(r: pd.Series) -> str:
    ref = float(r["reference"])
    ts = pd.Timestamp(r["timestamp"]).isoformat()
    return f"{str(r['direction'])}|{str(r['route'])}|{ts}|{ref:.8f}"


def bool_col(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s
    return s.astype(str).str.lower().eq("true")


def summarize(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0, "resolved": 0, "wins": 0, "losses": 0, "sum_R": 0.0, "mean_R": None}
    r = pd.to_numeric(df.loc[bool_col(df["resolved"]), "realized_R"], errors="coerce").dropna()
    return {
        "n": int(len(df)),
        "resolved": int(len(r)),
        "wins": int((r > 0).sum()),
        "losses": int((r < 0).sum()),
        "sum_R": float(r.sum()) if len(r) else 0.0,
        "mean_R": float(r.mean()) if len(r) else None,
    }


def value_counts(df: pd.DataFrame, col: str) -> dict:
    if df.empty or col not in df.columns:
        return {}
    return {str(k): int(v) for k, v in df[col].fillna("N/A").astype(str).value_counts().items()}


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("USAGE: script.py R21_ARTIFACT_ROOT R22_ARTIFACT_ROOT OUT_DIR")
    r21root, r22root, out = map(Path, sys.argv[1:])
    out.mkdir(parents=True, exist_ok=True)

    ppos_path = find_one(r21root, "r21_positions.csv")
    cpos_path = find_one(r22root, "r22_positions.csv")
    cseed_path = find_one(r22root, "r22_seed_candidates.csv")
    paudit_path = find_one(r21root, "audit.json")
    # R2.2 artifact contains parent audit too, so choose audit under r22/output explicitly.
    r22audits = [p for p in r22root.rglob("audit.json") if "/r22/output/historical_replay/" in p.as_posix()]
    if len(r22audits) != 1:
        raise SystemExit(f"EXPECTED_ONE_R22_AUDIT:{[str(x) for x in r22audits]}")
    caudit_path = r22audits[0]

    p = parse_time_cols(pd.read_csv(ppos_path))
    c = parse_time_cols(pd.read_csv(cpos_path))
    seeds = parse_time_cols(pd.read_csv(cseed_path))
    pa = json.loads(paudit_path.read_text(encoding="utf-8"))
    ca = json.loads(caudit_path.read_text(encoding="utf-8"))

    # Guard exact parent/child diagnostic identities before any forensic calculation.
    guards = {
        "r21_model": pa.get("model") == "MASTER_BTC_TREND_V3_R2_1",
        "r21_episodes": int(pa["counts"]["executed_r21_episodes"]) == 69,
        "r21_short_exp": abs(float(pa["summary"]["short_expectancy"]["mean_R"]) - 0.03469379995835538) < 1e-12,
        "r22_model": ca.get("model") == "MASTER_BTC_TREND_V3_R2_2",
        "r22_episodes": int(ca["counts"]["executed_r22_episodes"]) == 86,
        "r22_short_exp": abs(float(ca["summary"]["short_expectancy"]["mean_R"]) - (-0.14243325768003964)) < 1e-12,
        "single_change": ca["freeze_identity"]["single_change_id"] == "DAILY_20D_EXTREME_DISTANCE_HARD_GATE_TO_PRIORITY_ONLY",
    }
    if not all(guards.values()):
        raise SystemExit(f"SOURCE_IDENTITY_GUARD_FAIL:{guards}")

    p["fingerprint"] = p.apply(fp_row, axis=1)
    c["fingerprint"] = c.apply(fp_row, axis=1)
    if p["fingerprint"].duplicated().any() or c["fingerprint"].duplicated().any():
        raise SystemExit("POSITION_FINGERPRINT_NOT_UNIQUE")

    pset, cset = set(p.fingerprint), set(c.fingerprint)
    shared_keys = pset & cset
    new_keys = cset - pset
    dropped_keys = pset - cset

    shared_p = p[p.fingerprint.isin(shared_keys)].copy()
    shared_c = c[c.fingerprint.isin(shared_keys)].copy()
    new = c[c.fingerprint.isin(new_keys)].copy()
    dropped = p[p.fingerprint.isin(dropped_keys)].copy()

    # Join exact child raw-seed metadata. Multiple identical raw fingerprints are not allowed.
    seeds["fingerprint"] = seeds.apply(seed_fp_row, axis=1)
    sm = seeds.drop_duplicates("fingerprint", keep="first").set_index("fingerprint")
    new["priority_extreme_near"] = new["fingerprint"].map(sm.get("priority_extreme_near", pd.Series(dtype=object)))
    new["priority_extreme_near"] = bool_col(new["priority_extreme_near"])
    new["stop_distance_pct"] = (pd.to_numeric(new["seed_entry"]) - pd.to_numeric(new["stop"])).abs() / pd.to_numeric(new["seed_entry"])
    new["confirm_delay_days"] = (new["confirmed_time"] - new["seed_time"]).dt.total_seconds() / 86400.0
    new["core_delay_days"] = (new["core_time"] - new["seed_time"]).dt.total_seconds() / 86400.0

    # Directly admitted = exact new executed seed that fails old extreme-near hard gate.
    direct = new[~new["priority_extreme_near"]].copy()
    sequence = new[new["priority_extreme_near"]].copy()

    # Shared exact-seed outcome identity check. Same exact seed should replay identically.
    smrg = shared_p[["fingerprint", "realized_R", "resolved"]].merge(
        shared_c[["fingerprint", "realized_R", "resolved"]], on="fingerprint", suffixes=("_r21", "_r22")
    )
    smrg["delta_R"] = pd.to_numeric(smrg.realized_R_r22) - pd.to_numeric(smrg.realized_R_r21)
    shared_max_abs_delta = float(smrg.delta_R.abs().max()) if len(smrg) else 0.0

    by_dir = {}
    for direction in ["LONG", "SHORT"]:
        q = new[new.direction == direction]
        qd = direct[direct.direction == direction]
        qs = sequence[sequence.direction == direction]
        by_dir[direction] = {
            "all_new": summarize(q),
            "direct_priority_false": summarize(qd),
            "sequence_priority_true": summarize(qs),
            "exit_reasons": value_counts(q, "exit_reason"),
            "routes": value_counts(q, "route"),
            "confirmed": int(q.confirmed_time.notna().sum()),
            "core": int(q.core_time.notna().sum()),
            "median_stop_distance_pct": float(q.stop_distance_pct.median()) if len(q) else None,
            "median_confirm_delay_days": float(q.confirm_delay_days.dropna().median()) if q.confirm_delay_days.notna().any() else None,
            "median_core_delay_days": float(q.core_delay_days.dropna().median()) if q.core_delay_days.notna().any() else None,
        }

    # Replacement economics: child-only minus parent-only realized R, direction by direction.
    replacement = {}
    for direction in ["LONG", "SHORT", "ALL"]:
        qn = new if direction == "ALL" else new[new.direction == direction]
        qd = dropped if direction == "ALL" else dropped[dropped.direction == direction]
        sn, sd = summarize(qn), summarize(qd)
        replacement[direction] = {
            "new": sn,
            "dropped": sd,
            "net_count": int(len(qn) - len(qd)),
            "net_realized_R_new_minus_dropped": float(sn["sum_R"] - sd["sum_R"]),
        }

    forensic = {
        "status": "R22_INCREMENTAL_EPISODE_FAST_FORENSIC_DIAGNOSTIC_ONLY",
        "source": {
            "r21_run_id": 34116514316,
            "r21_artifact_id": 10016474113,
            "r22_run_id": 34124855102,
            "r22_artifact_id": 10019667568,
            "source_identity_guards": guards,
        },
        "comparison_definition": "exact fingerprint = direction|route|seed_time|reference_8dp",
        "counts": {
            "r21_positions": int(len(p)),
            "r22_positions": int(len(c)),
            "net_position_increase": int(len(c) - len(p)),
            "shared_exact": int(len(shared_keys)),
            "r22_only_new": int(len(new_keys)),
            "r21_only_dropped": int(len(dropped_keys)),
            "new_minus_dropped": int(len(new_keys) - len(dropped_keys)),
            "r22_only_direct_priority_false": int(len(direct)),
            "r22_only_sequence_priority_true": int(len(sequence)),
        },
        "shared_exact_seed_outcome_max_abs_delta_R": shared_max_abs_delta,
        "new_episode_summary_by_direction": by_dir,
        "replacement_economics": replacement,
        "interpretation_firewall": [
            "This is post-replay forensic evidence and cannot modify frozen R2.2.",
            "No threshold is selected from these outcomes.",
            "R2.2-only episodes include both directly admitted priority-false seeds and sequence effects caused by changed earlier executions.",
            "Net +17 executed positions does not imply exactly 17 unique new episodes; exact set comparison is reported separately.",
        ],
    }

    new.sort_values(["direction", "seed_time"]).to_csv(out / "r22_only_new_positions.csv", index=False)
    dropped.sort_values(["direction", "seed_time"]).to_csv(out / "r21_only_dropped_positions.csv", index=False)
    smrg.sort_values("fingerprint").to_csv(out / "shared_exact_outcome_check.csv", index=False)
    (out / "r22_incremental_episode_forensic.json").write_text(json.dumps(forensic, indent=2, default=str), encoding="utf-8")
    print(json.dumps(forensic, indent=2, default=str))


if __name__ == "__main__":
    main()
