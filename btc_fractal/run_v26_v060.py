from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import run_v26_v035 as v035
import run_v26_v036 as v036
import run_v26_v037 as v037

core = v035.core
v032 = v035.v032
D = v035.D
REPO = "sovet12345688-debug/master-alt-cvd"
PR_NUMBER = 7


def _safe_acc(z: pd.DataFrame) -> float | None:
    if z.empty or "correct" not in z:
        return None
    x = z["correct"].dropna()
    return None if x.empty else round(float(x.astype(bool).mean()) * 100.0, 2)


def confidence_calibration_audit(indep035: pd.DataFrame, cfg: dict) -> dict:
    """Audit the already-existing V0.3.5 confidence grades without retuning them.

    Acceptance criteria are the pre-existing global confidence_calibration_acceptance values in config.json.
    This audit does not change score formulas, grade cutoffs, or outcomes.
    """
    c = cfg.get("confidence_calibration_acceptance", {})
    z = indep035[indep035["actual"].isin(D) & indep035["pred"].isin(D)].copy()
    overall = _safe_acc(z)
    by_grade: dict[str, dict] = {}
    for grade in ["LOW", "MEDIUM", "HIGH"]:
        g = z[z["confidence_grade"] == grade].copy()
        stats = v032.stats(g, "pred") if not g.empty else {
            "rows": 0, "coverage_pct": 0.0, "accuracy": None, "balanced_accuracy": None,
            "class_recalls": {}, "prediction_mix": {},
        }
        by_grade[grade] = {
            "count": int(len(g)),
            "accuracy": _safe_acc(g),
            "share_of_directional_pct": 0.0 if z.empty else round(len(g) / len(z) * 100.0, 2),
            "balanced_accuracy": stats.get("balanced_accuracy"),
            "prediction_mix": stats.get("prediction_mix", {}),
        }

    h, m, l = by_grade["HIGH"], by_grade["MEDIUM"], by_grade["LOW"]
    high_gain = None if overall is None or h["accuracy"] is None else round(h["accuracy"] - overall, 2)
    tol = float(c.get("monotonic_tolerance_pp", 2.0))
    monotonic = bool(
        l["accuracy"] is not None and m["accuracy"] is not None and h["accuracy"] is not None
        and h["accuracy"] + tol >= m["accuracy"]
        and m["accuracy"] + tol >= l["accuracy"]
    )
    gates = {
        "high_case_gate": h["count"] >= int(c.get("min_high_cases", 10)),
        "medium_case_gate": m["count"] >= int(c.get("min_medium_cases", 10)),
        "high_gain_gate": high_gain is not None and high_gain >= float(c.get("min_high_accuracy_gain_pp", 5.0)),
        "high_coverage_gate": h["share_of_directional_pct"] <= float(c.get("max_high_coverage_pct", 50.0)),
        "monotonic_gate": monotonic,
    }
    passed = bool(all(gates.values()))
    return {
        "directional_rows": int(len(z)),
        "overall_accuracy": overall,
        "by_grade": by_grade,
        "high_accuracy_gain_pp": high_gain,
        "gates": gates,
        "pass": passed,
        "locked_acceptance": c,
        "display_rule": "If calibration fails, V0.3.5 confidence_grade/score cannot be presented as empirically validated confidence; expose regime evidence only or label it as uncalibrated evidence strength.",
    }


def recompute_v036(features: pd.DataFrame, events: pd.DataFrame, cfg: dict, out: Path) -> dict:
    core_wf = pd.read_csv(out / "walk_forward_v035.csv", parse_dates=["time"]).set_index("time")
    wf = v036.walk_forward_v036(features, events, cfg, core_wf)
    indep = v032.independent_rows(
        wf.rename(columns={"base_pred": "pred", "base_correct": "correct"})
    ).rename(columns={"pred": "base_pred", "correct": "base_correct"})
    summary = v036._summary(indep)
    acceptance = v036._accept(summary, cfg)
    return {"summary": summary, "acceptance": acceptance}


def recompute_v037(features: pd.DataFrame, events: pd.DataFrame, cfg: dict, out: Path) -> dict:
    anchor_wf = pd.read_csv(out / "walk_forward_price_core.csv", parse_dates=["time"]).set_index("time")
    labels = v037.turning_zone_labels(features, cfg)
    wf = v037.walk_forward_specialists(features, events, labels, cfg, anchor_wf)
    indep = v032.independent_rows(wf.assign(pred="ABSTAIN", actual="UP30_FIRST"))
    return v037.validate(indep, cfg)


def pr_isolation_audit() -> dict:
    url = f"https://api.github.com/repos/{REPO}/pulls/{PR_NUMBER}/files?per_page=100"
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "BTC-FRACTAL-PREINTEGRATION-AUDIT/0.6.0"})
        r.raise_for_status()
        files = r.json()
        names = [str(x.get("filename")) for x in files if x.get("filename")]
    except Exception as e:
        return {"pass": False, "status": "PR_FILE_AUDIT_UNAVAILABLE", "error": f"{type(e).__name__}: {str(e)[:220]}"}
    allowed = [n for n in names if n.startswith("btc_fractal/") or n == ".github/workflows/btc_fractal_v26_validation.yml"]
    unexpected = [n for n in names if n not in allowed]
    return {
        "pass": len(names) > 0 and not unexpected,
        "status": "ISOLATED_RESEARCH_ONLY" if len(names) > 0 and not unexpected else "UNEXPECTED_PATH_CHANGE",
        "changed_file_count": int(len(names)),
        "unexpected_paths": unexpected,
        "filenames": names,
        "rule": "Only btc_fractal/** and its dedicated validation workflow may change before explicit integration approval.",
    }


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main_v060() -> None:
    cfg, out, data = core.load_config(), core.OUT, core.DATA
    required = [
        out / "v035_validation_summary.json",
        out / "episode_independent_v035.csv",
        out / "v042_validation_summary.json",
        out / "v051_validation_summary.json",
        data / "historical_features_daily.csv",
        data / "event_registry.csv",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise RuntimeError("Pre-integration inputs missing: " + ", ".join(missing))

    features = pd.read_csv(data / "historical_features_daily.csv", parse_dates=["time"]).set_index("time")
    events = pd.read_csv(data / "event_registry.csv", parse_dates=["time"]).set_index("time")
    indep035 = pd.read_csv(out / "episode_independent_v035.csv", parse_dates=["time"]).set_index("time")
    s035 = _load_json(out / "v035_validation_summary.json")
    s042 = _load_json(out / "v042_validation_summary.json")
    s051 = _load_json(out / "v051_validation_summary.json")

    conf = confidence_calibration_audit(indep035, cfg)
    onmacro = recompute_v036(features, events, cfg, out)
    specialists = recompute_v037(features, events, cfg, out)
    isolation = pr_isolation_audit()

    v035_val = s035.get("validation", {})
    core_pass = bool(v035_val.get("v035_stage_gate") == "FULL_PASS" and v035_val.get("regime_core_pass") and v035_val.get("direction_pass"))
    onmacro_pass = bool(onmacro.get("acceptance", {}).get("utility_pass"))
    layer_b_status = s042.get("validation", {}).get("same_era_status")
    layer_c_status = s051.get("validation", {}).get("layer_c_status")
    bottom_pass = bool(specialists.get("bottom_pass"))
    top_pass = bool(specialists.get("top_pass"))

    # Integration contract is deliberately conservative. Only the validated V0.3.5 price-regime core can become
    # a future read-only evidence block. Nothing in this audit authorizes live MASTER modification or PR merge.
    candidate = bool(core_pass and isolation.get("pass"))
    if candidate and conf.get("pass"):
        recommendation = "READ_ONLY_V035_CORE_CANDIDATE_WITH_CALIBRATED_CONFIDENCE"
    elif candidate:
        recommendation = "READ_ONLY_V035_CORE_CANDIDATE_SUPPRESS_CONFIDENCE_GRADE"
    else:
        recommendation = "NO_INTEGRATION_CANDIDATE"

    final = {
        "engine": "BTC_HISTORICAL_REGIME_OUTCOME_V0_6_0_PREINTEGRATION_AUDIT",
        "schema_version": "0.6.0",
        "audit_scope": "Final pre-integration audit only; MASTER BTC TREND remains untouched",
        "v035_price_regime_core": {
            "full_pass": core_pass,
            "regime_core": v035_val.get("regime_core"),
            "direction_layer": v035_val.get("direction_layer"),
        },
        "v035_confidence_calibration": conf,
        "onchain_macro_confirmation": {
            "utility_pass": onmacro_pass,
            "validation": onmacro,
            "decision": "CONTEXT_ONLY" if not onmacro_pass else "VALIDATED_CONFIRMATION_CANDIDATE",
        },
        "bottom_top_specialists": {
            "bottom_pass": bottom_pass,
            "top_pass": top_pass,
            "validation": specialists,
            "decision": "EXPERIMENTAL_ONLY" if not (bottom_pass and top_pass) else "SPECIALIST_CANDIDATE",
        },
        "layer_b_derivatives": {
            "status": layer_b_status,
            "decision": "SHADOW_READ_ONLY" if layer_b_status != "VALIDATED_CONFIRMATION_LAYER" else "VALIDATED_CONFIRMATION_CANDIDATE",
            "validation": s042.get("validation"),
        },
        "layer_c_etf": {
            "status": layer_c_status,
            "current_state": s051.get("current_layer_c"),
            "decision": "CONTEXT_ONLY" if layer_c_status != "VALIDATED_CONFIRMATION_LAYER" else "VALIDATED_CONFIRMATION_CANDIDATE",
            "validation": s051.get("validation"),
        },
        "pr_isolation": isolation,
        "preintegration_recommendation": recommendation,
        "future_integration_contract": {
            "allowed_candidate": "V0.3.5 price-regime historical evidence block only, read-only",
            "forbidden_without_new_validation": [
                "on-chain/macro score injection",
                "bottom/top specialist score injection",
                "Layer B derivatives score injection",
                "Layer C ETF score injection",
            ],
            "never_changed_by_fractal_block": [
                "MASTER top-level /100 scores",
                "TodayBuyScore",
                "1H/4H execution gates",
                "Reaction",
                "Safety",
                "Entry/SL/targets/live plan",
                "schedule",
            ],
            "disagreement_rule": "Fractal disagreement is counter-evidence/warning only; it cannot flip MASTER action by itself.",
            "abstention_rule": "Insufficient evidence/coverage/source freshness => hold judgement, never synthesize missing data.",
            "confidence_rule": "Only show '프렉탈 신뢰도' as validated confidence if the confidence-calibration audit passes; otherwise show uncalibrated evidence strength or omit it.",
        },
        "master_integration": "FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL",
        "pr_merge": "FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL",
        "final_audit_status": "READY_FOR_USER_INTEGRATION_DECISION" if candidate else "NOT_READY",
    }
    (out / "v060_preintegration_audit.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(final, ensure_ascii=False))


if __name__ == "__main__":
    main_v060()
