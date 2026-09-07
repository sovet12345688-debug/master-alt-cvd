from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def stats(df: pd.DataFrame) -> dict:
    if len(df) == 0:
        return {"n": 0, "wins": 0, "losses": 0, "sum_R": 0.0, "mean_R": None, "win_rate": None}
    r = pd.to_numeric(df["realized_unit_R"], errors="coerce").dropna()
    return {
        "n": int(len(df)),
        "wins": int((r > 0).sum()),
        "losses": int((r < 0).sum()),
        "sum_R": float(r.sum()),
        "mean_R": float(r.mean()) if len(r) else None,
        "win_rate": float((r > 0).mean()) if len(r) else None,
    }


def group_stats(df: pd.DataFrame, cols: str | list[str]) -> list[dict]:
    if isinstance(cols, str):
        cols = [cols]
    out = []
    for keys, g in df.groupby(cols, dropna=False, observed=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = {c: ("N/A" if pd.isna(v) else v) for c, v in zip(cols, keys)}
        row.update(stats(g))
        out.append(row)
    return out


def bool_mask(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s
    return s.astype(str).str.lower().eq("true")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot-root", required=True)
    ap.add_argument("--mode", choices=["all", "add", "sc", "route", "false_start"], default="all")
    ap.add_argument("--output")
    args = ap.parse_args()

    root = Path(args.snapshot_root) / "r13/output/historical_diagnostic"
    legs = pd.read_csv(root / "r13_trade_legs.csv")
    ep = pd.read_csv(root / "r13_episode_metrics.csv")

    for c in ["confirm_time", "resolved_time"]:
        legs[c] = pd.to_datetime(legs[c], utc=True, format="mixed", errors="coerce")
    for c in ["first_confirm_time", "first_resolved_time", "start_date"]:
        ep[c] = pd.to_datetime(ep[c], utc=True, format="mixed", errors="coerce")

    first = legs[legs["leg"].eq("1H_ENTRY")].copy()
    add = legs[legs["leg"].eq("4H_ADD")].copy()
    ids = set(ep["episode_id"].astype(str))
    first = first[first["episode_id"].astype(str).isin(ids)]
    add = add[add["episode_id"].astype(str).isin(ids)]

    first_small = first[
        ["episode_id", "route", "risk_state", "confirm_time", "resolved_time", "realized_unit_R", "outcome"]
    ].rename(
        columns={
            "route": "first_route_leg",
            "risk_state": "first_risk_leg",
            "confirm_time": "first_confirm_leg",
            "resolved_time": "first_resolved_leg",
            "realized_unit_R": "first_R",
            "outcome": "first_outcome_leg",
        }
    )
    x = add.merge(first_small, on="episode_id", how="left")
    x["add_delay_h"] = (x["confirm_time"] - x["first_confirm_leg"]).dt.total_seconds() / 3600
    x["add_after_first_resolved"] = x["confirm_time"] > x["first_resolved_leg"]
    x["route_same"] = x["route"].eq(x["first_route_leg"])
    x["first_success"] = pd.to_numeric(x["first_R"], errors="coerce") > 0
    x["delay_bucket"] = pd.cut(
        x["add_delay_h"], [-1, 24, 72, 168, float("inf")], labels=["<=24H", "24-72H", "72-168H", ">168H"]
    )
    year_map = ep[["episode_id", "start_date"]].copy()
    year_map["episode_year"] = year_map["start_date"].dt.year
    x = x.merge(year_map[["episode_id", "episode_year"]], on="episode_id", how="left")

    first_stats = stats(first)
    add_stats = stats(add)
    all_stats = stats(pd.concat([first, add], ignore_index=True))
    edge = {
        "first_sum_R": first_stats["sum_R"],
        "add_sum_R": add_stats["sum_R"],
        "combined_sum_R": all_stats["sum_R"],
        "edge_retained_ratio": all_stats["sum_R"] / first_stats["sum_R"] if first_stats["sum_R"] else None,
        "edge_given_back_ratio": (-add_stats["sum_R"]) / first_stats["sum_R"]
        if first_stats["sum_R"] and add_stats["sum_R"] < 0
        else 0.0,
        "mean_R_first": first_stats["mean_R"],
        "mean_R_add": add_stats["mean_R"],
        "mean_R_all_legs": all_stats["mean_R"],
    }

    out = {
        "status": "FAST_DIAGNOSTIC_ONLY_NOT_PROMOTION_EVIDENCE",
        "mode": args.mode,
        "snapshot_rows": {"episodes": int(len(ep)), "first_1h": int(len(first)), "adds_4h": int(len(add))},
        "edge_bridge": edge,
    }

    if args.mode in {"all", "add"}:
        out["add"] = {
            "overall": add_stats,
            "by_direction": group_stats(x, "direction"),
            "by_engine": group_stats(x, "engine"),
            "by_route": group_stats(x, "route"),
            "by_risk_state": group_stats(x, "risk_state"),
            "by_first_resolved_state": group_stats(x, "add_after_first_resolved"),
            "by_route_consistency": group_stats(x, "route_same"),
            "by_first_trade_success": group_stats(x, "first_success"),
            "by_delay_bucket": group_stats(x, "delay_bucket"),
            "by_episode_year": group_stats(x, "episode_year"),
            "by_direction_engine": group_stats(x, ["direction", "engine"]),
            "by_route_transition": group_stats(x, ["first_route_leg", "route"]),
        }

    if args.mode in {"all", "sc"}:
        sc = x[x["engine"].eq("SC")]
        out["sc_add"] = {
            "overall": stats(sc),
            "by_year": group_stats(sc, "episode_year"),
            "by_after_first_resolved": group_stats(sc, "add_after_first_resolved"),
            "by_route_transition": group_stats(sc, ["first_route_leg", "route"]),
            "by_risk_state": group_stats(sc, "risk_state"),
        }

    if args.mode in {"all", "route"}:
        out["route_transition"] = group_stats(x, ["first_route_leg", "route"])

    if args.mode in {"all", "false_start"}:
        complete = ep[bool_mask(ep["available_90d"])]
        executed = complete[bool_mask(complete["entry_1h"])]
        false_starts = executed[bool_mask(executed["confirmed_false_start_90d"])]
        out["false_start"] = {
            "executed_complete_90d": int(len(executed)),
            "confirmed_false_start_n": int(len(false_starts)),
            "rate": float(len(false_starts) / len(executed)) if len(executed) else None,
            "by_engine_first_entry": group_stats(first[first["episode_id"].isin(false_starts["episode_id"])], "engine"),
        }

    text = json.dumps(out, ensure_ascii=False, indent=2, default=str)
    print(text)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
