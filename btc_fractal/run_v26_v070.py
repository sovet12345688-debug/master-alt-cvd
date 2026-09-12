from __future__ import annotations

import json
import math
import numpy as np
import pandas as pd

import run_v26_v035 as v035

v034 = v035.v034
v032 = v035.v032
core = v035.core
D = v035.D
BULLISH_REGIMES = v035.BULLISH_REGIMES
BEARISH_REGIMES = v035.BEARISH_REGIMES


def _cfg(cfg: dict) -> dict:
    d = {
        "edge_temperature": 8.0,
        "strength_weights": {"regime": 0.50, "outcome": 0.30, "edge": 0.10, "support": 0.10},
        "grade_a": {
            "min_regime_score": 72.0,
            "min_strength": 70.0,
            "min_first_passage_share_pct": 70.0,
            "min_directional_analog_cases": 4,
            "min_distinct_analog_years": 3,
            "min_side_edge": 2.0
        },
        "grade_b": {
            "min_regime_score": 64.0,
            "min_strength": 62.0,
            "min_first_passage_share_pct": 60.0,
            "min_directional_analog_cases": 4,
            "min_distinct_analog_years": 3,
            "min_side_edge": -2.0
        },
        "acceptance": {
            "min_independent_rows": 30,
            "min_a_cases_each_side": 5,
            "min_a_precision_pct": 65.0,
            "min_a_gain_vs_base_pp": 5.0,
            "min_combined_a_cases": 10,
            "min_combined_a_precision_pct": 68.0,
            "min_b_cases_each_side": 8,
            "min_b_precision_pct": 58.0
        }
    }
    u = cfg.get("v070", {})
    for k, v in u.items():
        if isinstance(v, dict) and isinstance(d.get(k), dict):
            d[k].update(v)
        else:
            d[k] = v
    return d


def _best_regime(regime_scores: dict, regimes: list[str] | tuple[str, ...]) -> dict | None:
    vals = []
    for r in regimes:
        x = regime_scores.get(r, {})
        if x.get("score") is not None:
            vals.append(x)
    if not vals:
        return None
    return sorted(vals, key=lambda x: float(x.get("score", -1)), reverse=True)[0]


def _first_passage_side(outcomes: dict, side: str) -> tuple[int, int, int, float | None]:
    fp = outcomes.get("first_passage", {}) if isinstance(outcomes, dict) else {}
    up = int(fp.get("up30_first") or 0)
    dn = int(fp.get("dn20_first") or 0)
    n = int(fp.get("directional_cases") or (up + dn))
    hit = up if side == "LONG" else dn
    share = None if n <= 0 else 100.0 * hit / n
    return up, dn, n, share


def _support_score(analogs: list[dict]) -> tuple[float, int, int]:
    n = len(analogs)
    years = len({str(x.get("date", ""))[:4] for x in analogs if x.get("date")})
    # Five diverse analogs is the V0.3.5 target; support remains descriptive, never probability.
    s = 100.0 * (0.55 * min(1.0, n / 5.0) + 0.45 * min(1.0, years / 4.0))
    return float(s), n, years


def _grade(e: dict, c: dict) -> str:
    def ok(g: dict) -> bool:
        return (
            e.get("regime_score") is not None
            and float(e["regime_score"]) >= float(g["min_regime_score"])
            and e.get("signal_strength") is not None
            and float(e["signal_strength"]) >= float(g["min_strength"])
            and e.get("first_passage_share_pct") is not None
            and float(e["first_passage_share_pct"]) >= float(g["min_first_passage_share_pct"])
            and int(e.get("directional_analog_cases") or 0) >= int(g["min_directional_analog_cases"])
            and int(e.get("distinct_analog_years") or 0) >= int(g["min_distinct_analog_years"])
            and float(e.get("side_edge") or -999) >= float(g["min_side_edge"])
        )
    if ok(c["grade_a"]):
        return "A"
    if ok(c["grade_b"]):
        return "B"
    return "CONTEXT"


def side_evidence(p: dict, events: pd.DataFrame, cfg: dict, side: str) -> dict:
    c = _cfg(cfg)
    side_regs = BULLISH_REGIMES if side == "LONG" else BEARISH_REGIMES
    opp_regs = BEARISH_REGIMES if side == "LONG" else BULLISH_REGIMES
    best = _best_regime(p.get("regime_scores", {}), side_regs)
    opp = _best_regime(p.get("regime_scores", {}), opp_regs)
    if best is None:
        return {"side": side, "grade": "CONTEXT", "status": "NO_SIDE_REGIME"}

    analogs = list(best.get("analogs", []))
    analog_dates = [x.get("date") for x in analogs if x.get("date")]
    outcomes = v034._outcome_summary(events, analog_dates, cfg)
    up, dn, directional_cases, side_share = _first_passage_side(outcomes, side)
    support_score, analog_count, distinct_years = _support_score(analogs)

    regime_score = float(best.get("score"))
    opposite_score = None if opp is None else float(opp.get("score"))
    side_edge = regime_score - (opposite_score if opposite_score is not None else 0.0)
    edge_temp = max(float(c.get("edge_temperature", 8.0)), 1e-6)
    edge_component = 50.0 + 50.0 * math.tanh(side_edge / edge_temp)
    outcome_component = 50.0 if side_share is None else float(side_share)
    w = c["strength_weights"]
    strength = (
        float(w["regime"]) * regime_score
        + float(w["outcome"]) * outcome_component
        + float(w["edge"]) * edge_component
        + float(w["support"]) * support_score
    )
    strength = round(float(np.clip(strength, 0, 100)), 1)

    e = {
        "side": side,
        "status": "OK",
        "regime": best.get("regime"),
        "regime_ko": best.get("label_ko"),
        "regime_score": round(regime_score, 1),
        "opposite_best_score": None if opposite_score is None else round(opposite_score, 1),
        "side_edge": round(side_edge, 1),
        "signal_strength": strength,
        "first_passage_share_pct": None if side_share is None else round(side_share, 1),
        "up30_first": up,
        "dn20_first": dn,
        "directional_analog_cases": directional_cases,
        "analog_count": analog_count,
        "distinct_analog_years": distinct_years,
        "analogs": analogs,
        "historical_outcomes": outcomes,
        "warning": "Signal strength and historical hit shares are evidence measures, not calibrated future probabilities."
    }
    e["grade"] = _grade(e, c)
    return e


def asymmetric_predict(features: pd.DataFrame, events: pd.DataFrame, cfg: dict, qt: pd.Timestamp) -> dict:
    p = v035.regime_predict_v035(features, events, cfg, qt)
    long_e = side_evidence(p, events, cfg, "LONG")
    short_e = side_evidence(p, events, cfg, "SHORT")
    ls = float(long_e.get("signal_strength") or 0.0)
    ss = float(short_e.get("signal_strength") or 0.0)
    if long_e.get("grade") == "A" and short_e.get("grade") != "A":
        dominant = "LONG"
    elif short_e.get("grade") == "A" and long_e.get("grade") != "A":
        dominant = "SHORT"
    elif abs(ls - ss) >= 5.0:
        dominant = "LONG" if ls > ss else "SHORT"
    else:
        dominant = "NEUTRAL"
    return {
        "engine": "BTC_FRACTAL_V0_7_0_ASYMMETRIC_LONG_SHORT",
        "schema_version": "0.7.0",
        "query_date": qt.date().isoformat(),
        "base_v035_pred": p.get("pred"),
        "base_dominant_regime": p.get("dominant_regime"),
        "base_dominant_regime_score": p.get("dominant_regime_score"),
        "long": long_e,
        "short": short_e,
        "dominant_side": dominant,
        "design": {
            "asymmetric": "LONG and SHORT are scored and graded independently; they are not complements.",
            "selective_use": "A/B grades are fixed-rule selective signals. CONTEXT remains read-only background evidence.",
            "multi_horizon": "Each side retains 30/90/180/365d historical outcome profiles from its own analog set.",
            "no_master_effect": "No MASTER BTC TREND score, execution gate, plan or schedule is modified."
        }
    }


def walk_forward(features: pd.DataFrame, events: pd.DataFrame, cfg: dict, anchor_wf: pd.DataFrame) -> pd.DataFrame:
    eps = v035.v02.episode_ids(features["price"].dropna(), float(cfg.get("episode_reversal_pct", 0.20)), int(cfg.get("episode_max_days", 365)))
    rows = []
    for qt, base in anchor_wf.iterrows():
        actual = base.get("actual")
        try:
            p = asymmetric_predict(features, events, cfg, qt)
            le, se = p["long"], p["short"]
            rows.append({
                "time": qt,
                "pred": base.get("pred"),
                "actual": actual,
                "test_episode_id": int(eps.loc[qt]) if qt in eps.index and pd.notna(eps.loc[qt]) else np.nan,
                "long_grade": le.get("grade"),
                "long_strength": le.get("signal_strength"),
                "long_regime": le.get("regime"),
                "long_regime_score": le.get("regime_score"),
                "long_edge": le.get("side_edge"),
                "long_fp_share": le.get("first_passage_share_pct"),
                "long_fp_cases": le.get("directional_analog_cases"),
                "short_grade": se.get("grade"),
                "short_strength": se.get("signal_strength"),
                "short_regime": se.get("regime"),
                "short_regime_score": se.get("regime_score"),
                "short_edge": se.get("side_edge"),
                "short_fp_share": se.get("first_passage_share_pct"),
                "short_fp_cases": se.get("directional_analog_cases"),
                "dominant_side": p.get("dominant_side")
            })
        except Exception as e:
            rows.append({"time": qt, "pred": base.get("pred"), "actual": actual, "error": str(e)})
    return pd.DataFrame(rows).set_index("time") if rows else pd.DataFrame()


def _precision(z: pd.DataFrame, side: str, grades: tuple[str, ...]) -> dict:
    col = "long_grade" if side == "LONG" else "short_grade"
    target = D[0] if side == "LONG" else D[1]
    x = z[z[col].isin(grades) & z["actual"].isin(D)].copy()
    if x.empty:
        return {"count": 0, "precision_pct": None, "actual_target_count": 0}
    hit = x["actual"].eq(target)
    return {
        "count": int(len(x)),
        "precision_pct": round(float(hit.mean()) * 100, 2),
        "actual_target_count": int(hit.sum())
    }


def _base_side_precision(indep_v035: pd.DataFrame, side: str) -> dict:
    pred = D[0] if side == "LONG" else D[1]
    z = indep_v035[indep_v035["pred"] == pred].copy()
    if z.empty:
        return {"count": 0, "precision_pct": None}
    return {"count": int(len(z)), "precision_pct": round(float(z["actual"].eq(pred).mean()) * 100, 2)}


def validate(indep: pd.DataFrame, indep_v035: pd.DataFrame, cfg: dict) -> dict:
    c = _cfg(cfg)["acceptance"]
    long_base = _base_side_precision(indep_v035, "LONG")
    short_base = _base_side_precision(indep_v035, "SHORT")
    long_a = _precision(indep, "LONG", ("A",))
    short_a = _precision(indep, "SHORT", ("A",))
    long_ab = _precision(indep, "LONG", ("A", "B"))
    short_ab = _precision(indep, "SHORT", ("A", "B"))

    both_a = []
    for side, col, target in [("LONG", "long_grade", D[0]), ("SHORT", "short_grade", D[1])]:
        x = indep[indep[col].eq("A") & indep["actual"].isin(D)].copy()
        for _, r in x.iterrows():
            both_a.append(bool(r["actual"] == target))
    combined_a = {"count": len(both_a), "precision_pct": None if not both_a else round(float(np.mean(both_a)) * 100, 2)}

    long_gain = None if long_a["precision_pct"] is None or long_base["precision_pct"] is None else round(long_a["precision_pct"] - long_base["precision_pct"], 2)
    short_gain = None if short_a["precision_pct"] is None or short_base["precision_pct"] is None else round(short_a["precision_pct"] - short_base["precision_pct"], 2)

    gates = {
        "independent_rows_gate": len(indep) >= int(c["min_independent_rows"]),
        "long_a_case_gate": long_a["count"] >= int(c["min_a_cases_each_side"]),
        "long_a_precision_gate": long_a["precision_pct"] is not None and long_a["precision_pct"] >= float(c["min_a_precision_pct"]),
        "long_a_gain_gate": long_gain is not None and long_gain >= float(c["min_a_gain_vs_base_pp"]),
        "short_a_case_gate": short_a["count"] >= int(c["min_a_cases_each_side"]),
        "short_a_precision_gate": short_a["precision_pct"] is not None and short_a["precision_pct"] >= float(c["min_a_precision_pct"]),
        "short_a_gain_gate": short_gain is not None and short_gain >= float(c["min_a_gain_vs_base_pp"]),
        "combined_a_case_gate": combined_a["count"] >= int(c["min_combined_a_cases"]),
        "combined_a_precision_gate": combined_a["precision_pct"] is not None and combined_a["precision_pct"] >= float(c["min_combined_a_precision_pct"]),
        "long_ab_case_gate": long_ab["count"] >= int(c["min_b_cases_each_side"]),
        "long_ab_precision_gate": long_ab["precision_pct"] is not None and long_ab["precision_pct"] >= float(c["min_b_precision_pct"]),
        "short_ab_case_gate": short_ab["count"] >= int(c["min_b_cases_each_side"]),
        "short_ab_precision_gate": short_ab["precision_pct"] is not None and short_ab["precision_pct"] >= float(c["min_b_precision_pct"])
    }
    long_a_pass = gates["long_a_case_gate"] and gates["long_a_precision_gate"] and gates["long_a_gain_gate"]
    short_a_pass = gates["short_a_case_gate"] and gates["short_a_precision_gate"] and gates["short_a_gain_gate"]
    combined_pass = gates["combined_a_case_gate"] and gates["combined_a_precision_gate"]
    return {
        "independent_episode_rows": int(len(indep)),
        "v035_base_precision": {"LONG": long_base, "SHORT": short_base},
        "LONG": {"A": long_a, "A_or_B": long_ab, "A_gain_vs_v035_pp": long_gain, "A_validated": bool(long_a_pass)},
        "SHORT": {"A": short_a, "A_or_B": short_ab, "A_gain_vs_v035_pp": short_gain, "A_validated": bool(short_a_pass)},
        "combined_A": combined_a,
        "gates": gates,
        "asymmetric_stage": "FULL_PASS" if long_a_pass and short_a_pass and combined_pass else ("PARTIAL_PASS" if long_a_pass or short_a_pass or combined_pass else "FAIL"),
        "integration_policy": {
            "validated_side_A": "May be exposed as a validated selective historical signal only after manual review and explicit approval.",
            "failed_or_sparse_side": "Keep as context/read-only; do not hide it and do not promote its hit rate.",
            "MASTER_effect": "0% until explicit user approval; even after approval this layer cannot bypass execution gates."
        },
        "locked_acceptance": c
    }


def main_v070() -> None:
    # Rebuild frozen V0.3.5 core first. V0.7.0 is an additional read-only research layer.
    v035.main_v035()
    cfg, out, data = core.load_config(), core.OUT, core.DATA
    features = pd.read_csv(data / "historical_features_daily.csv", parse_dates=["time"]).set_index("time")
    events = pd.read_csv(data / "event_registry.csv", parse_dates=["time"]).set_index("time")
    anchor_wf = pd.read_csv(out / "walk_forward_v035.csv", parse_dates=["time"]).set_index("time")
    indep_v035 = pd.read_csv(out / "episode_independent_v035.csv", parse_dates=["time"]).set_index("time")

    wf = walk_forward(features, events, cfg, anchor_wf)
    wf.to_csv(out / "walk_forward_v070_asymmetric.csv")
    indep = v032.independent_rows(wf)
    indep.to_csv(out / "episode_independent_v070_asymmetric.csv")

    current_qt = features.dropna(subset=["price"]).index.max()
    current = asymmetric_predict(features, events, cfg, current_qt)
    validation = validate(indep, indep_v035, cfg)
    current["validation_snapshot"] = validation
    current["master_integration"] = "FORBIDDEN_PENDING_MANUAL_REVIEW_AND_EXPLICIT_USER_APPROVAL"
    current["pr_merge"] = "FORBIDDEN_PENDING_MANUAL_REVIEW_AND_EXPLICIT_USER_APPROVAL"

    (out / "current_v070_asymmetric.json").write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "v070_validation_summary.json").write_text(json.dumps({
        "engine": "BTC_FRACTAL_V0_7_0_ASYMMETRIC_LONG_SHORT",
        "schema_version": "0.7.0",
        "architecture": "SEPARATE_LONG_SHORT + SELECTIVE_A_B_GRADES + MULTI_HORIZON_ANALOG_OUTCOMES",
        "validation": validation,
        "anti_overfit": "Grades and acceptance gates are pre-declared in code/config; no gate lowering from this run. Same point-in-time independent episode basis as V0.3.5.",
        "next_step": "If only one side validates, promote only that side after manual approval and redesign the other side independently. If neither validates, build nested walk-forward reliability calibration rather than threshold fitting on the full OOS sample.",
        "master_integration": "FORBIDDEN_PENDING_MANUAL_REVIEW_AND_EXPLICIT_USER_APPROVAL"
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(current, ensure_ascii=False))
    print(json.dumps(validation, ensure_ascii=False))


if __name__ == "__main__":
    main_v070()
