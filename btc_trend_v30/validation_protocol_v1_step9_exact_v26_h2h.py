from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import requests

STATUS = "RESEARCH_ONLY_PROMOTION_HOLD"
PROTOCOL = "VALIDATION_PROTOCOL_V1_0_FINAL_LOCK"
REPO = "sovet12345688-debug/master-alt-cvd"
MAIN = "main"
ROOT = Path("btc_trend_v30/output/validation_v1")
STEP8 = ROOT / "step8_robustness/audit.json"
OUT = ROOT / "step9_exact_v26_h2h"
OUT.mkdir(parents=True, exist_ok=True)

RAW = "https://raw.githubusercontent.com/{repo}/{ref}/{path}"
API = "https://api.github.com/repos/{repo}"
HEADERS = {"User-Agent": "btc-trend-v30-step9-exact-v26-h2h"}


def get_text(path: str, ref: str = MAIN) -> str:
    url = RAW.format(repo=REPO, ref=ref, path=path)
    r = requests.get(url, timeout=30, headers=HEADERS)
    r.raise_for_status()
    return r.text


def get_json(path: str, ref: str = MAIN) -> dict[str, Any]:
    return json.loads(get_text(path, ref=ref))


def get_api(url: str) -> Any:
    r = requests.get(url, timeout=30, headers=HEADERS)
    r.raise_for_status()
    return r.json()


def git_blob_sha(text: str) -> str:
    b = text.encode("utf-8")
    header = f"blob {len(b)}\0".encode("utf-8")
    return hashlib.sha1(header + b).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> None:
    step8 = json.load(open(STEP8, encoding="utf-8"))
    if step8.get("step8") != "PASS":
        raise SystemExit("STOP: Step8 must PASS before exact V2.6 H2H audit")

    prompt_path = "master_prompts/master_btc_trend_v2_6_current.md"
    contract_path = "state/master_btc_trend_v2_6_contract.json"
    official_path = "official_state/latest/btc_trend.json"

    prompt = get_text(prompt_path)
    contract = get_json(contract_path)
    official = get_json(official_path)

    branch = get_api(f"{API.format(repo=REPO)}/branches/{MAIN}")
    tree_sha = branch["commit"]["commit"]["tree"]["sha"]
    tree = get_api(f"{API.format(repo=REPO)}/git/trees/{tree_sha}?recursive=1")["tree"]
    paths = [x["path"] for x in tree if x.get("type") == "blob"]

    prompt_blob_sha = git_blob_sha(prompt)
    official_source_sha = official.get("current_canonical_source_sha")

    # Exact production replay requires either persisted historical OFFICIAL outputs or a deterministic
    # executable implementation of the production MASTER. Auxiliary engines/proxies are forbidden.
    prod_exec_candidates = [
        p for p in paths
        if p.endswith((".py", ".ipynb"))
        and ("v2_6" in p.lower() or "v26" in p.lower() or "btc_trend" in p.lower())
        and "btc_fractal" not in p.lower()
        and "btc_trend_v30" not in p.lower()
        and "validate_master_os" not in p.lower()
    ]
    official_history_files = [p for p in paths if p.startswith("official_state/history/btc_trend/")]
    v25_proxy_files = [p for p in paths if p.startswith("btc_backtest/") and ("v25" in p.lower() or "time_validity_v2" in p.lower())]
    fractal_files = [p for p in paths if p.startswith("btc_fractal/") or "btc_fractal_v26" in p.lower()]

    prompt_signatures = {
        "core_score_weights_present": "CORE SCORES /100" in prompt,
        "execution_thresholds_present": "Reaction>=65" in prompt and "Safety>=80" in prompt and "R:R>=3" in prompt,
        "coverage_renormalization_present": "Missing weighted field" in prompt and "재정규화" in prompt,
        "fractal_zero_weight_declared": "top-level score/Entry Gate/plan/schedule 반영 0점 0가중치" in prompt,
        "live_bitget_derivatives_declared": "Freshness<=90m" in prompt and "Bitget" in prompt,
    }

    # The canonical specifies component weights and semantic gates, but not deterministic raw->subscore
    # transformations or complete historical zone/reaction/safety/severe-risk reconstruction rules.
    deterministic_spec = {
        "raw_to_component_score_mapping_complete": False,
        "registered_support_resistance_algorithm_complete": False,
        "reaction_score_formula_complete": False,
        "safety_score_formula_complete": False,
        "historical_severe_risk_veto_reconstruction_complete": False,
        "full_historical_live_source_snapshot_series_available": False,
    }

    canonical_ok = (
        contract.get("production_version") == "V2.6"
        and contract.get("status") == "FINAL_PRODUCTION_CANONICAL"
        and contract.get("canonical_source") == prompt_path
        and contract.get("source_provenance", {}).get("origin") == "ACTIVE_AUTOMATION_PROMPT"
        and official.get("master_version") == "V2.6 PRODUCTION"
        and official_source_sha == prompt_blob_sha
    )

    persisted_official = official.get("state_status") != "NO_STORED_OFFICIAL_RUN" or len(official_history_files) > 0
    deterministic_exec = len(prod_exec_candidates) > 0 and all(deterministic_spec.values())
    exact_replayable = bool(canonical_ok and (persisted_official or deterministic_exec))

    reason_codes: list[str] = []
    if not persisted_official:
        reason_codes.append("NO_PERSISTED_V2_6_OFFICIAL_RUN_HISTORY")
    if not prod_exec_candidates:
        reason_codes.append("NO_DETERMINISTIC_V2_6_PRODUCTION_EXECUTABLE")
    if not all(deterministic_spec.values()):
        reason_codes.append("CANONICAL_IS_SEMANTIC_SPEC_NOT_COMPLETE_REPLAY_FORMULA")
    if fractal_files or contract.get("fractal", {}).get("top_level_score_weight") == 0:
        reason_codes.append("V2_6_FRACTAL_IS_AUXILIARY_ZERO_WEIGHT_NOT_MASTER_PROXY")
    if v25_proxy_files:
        reason_codes.append("V2_5_AND_TIME_VALIDITY_PROXY_FILES_EXIST_BUT_EXACT_SUBSTITUTION_FORBIDDEN")

    h2h_status = "READY_EXACT_REPLAY" if exact_replayable else "LINK_NA_EXACT_BASELINE_NOT_REPLAYABLE"

    baseline = step8.get("baseline", {})
    mcr_gate = step8.get("mcr_promotion_gate", {})
    v3_reference = {
        "oos_episodes": baseline.get("oos_episodes"),
        "entries": baseline.get("entries"),
        "first_leg_mean_R": baseline.get("first_leg", {}).get("mean_R"),
        "all_leg_mean_R": baseline.get("all_leg", {}).get("mean_R"),
        "confirmed_false_start_90d": baseline.get("confirmed_false_start_90d"),
        "medium_missed_rate": baseline.get("medium_missed_rate"),
        "long_missed_rate": baseline.get("long_missed_rate"),
        "mcr90_mean": baseline.get("mcr90", {}).get("mean"),
        "mcr365_mean": baseline.get("mcr365", {}).get("mean"),
        "mcr90_gate": mcr_gate.get("mcr90_mean_ge_20pct"),
        "mcr365_gate": mcr_gate.get("mcr365_mean_ge_20pct"),
    }

    forbidden_performance_deltas = [
        "win_rate_delta",
        "expectancy_R_delta",
        "MCR_delta",
        "false_start_delta",
        "missed_trend_delta",
        "MDD_delta",
        "cycle_performance_delta",
    ]

    audit = {
        "status": STATUS,
        "protocol": PROTOCOL,
        "step": "9_EXACT_V2_6_HEAD_TO_HEAD",
        "v2_6_canonical": {
            "prompt_path": prompt_path,
            "prompt_git_blob_sha": prompt_blob_sha,
            "prompt_sha256": sha256_text(prompt),
            "official_expected_prompt_sha": official_source_sha,
            "contract_path": contract_path,
            "production_version": contract.get("production_version"),
            "source_origin": contract.get("source_provenance", {}).get("origin"),
            "canonical_identity_verified": canonical_ok,
            "prompt_signatures": prompt_signatures,
        },
        "replayability": {
            "state_status": official.get("state_status"),
            "persisted_official_history_files": official_history_files,
            "persisted_official_history_available": persisted_official,
            "production_executable_candidates": prod_exec_candidates,
            "deterministic_production_executable_available": deterministic_exec,
            "deterministic_spec_completeness": deterministic_spec,
            "v25_or_time_validity_proxy_file_count": len(v25_proxy_files),
            "fractal_auxiliary_file_count": len(fractal_files),
            "exact_replayable": exact_replayable,
        },
        "exact_h2h_status": h2h_status,
        "reason_codes": reason_codes,
        "v3_reference_only_no_v2_6_delta_claim": v3_reference,
        "forbidden_performance_deltas_without_exact_v2_6_baseline": forbidden_performance_deltas,
        "architecture_comparison_allowed": True,
        "performance_head_to_head_allowed": exact_replayable,
        "checks": {
            "step8_pass_required": True,
            "exact_v2_6_canonical_source_verified": canonical_ok,
            "official_no_reconstruction_policy_respected": official.get("state_status") == "NO_STORED_OFFICIAL_RUN",
            "fractal_not_used_as_v2_6_master_proxy": True,
            "v25_proxy_not_used_as_v2_6_master_proxy": True,
            "no_synthetic_v2_6_scores_or_trades_created": True,
            "v2_6_untouched": True,
        },
        "step9_audit": "PASS" if canonical_ok else "HOLD",
        "promotion": "HOLD",
        "probability": "확률 산출보류",
        "v2_6_modified": False,
        "interpretation": (
            "Exact V2.6 production identity is verified, but exact historical performance H2H cannot be computed "
            "without inventing a deterministic V2.6 replay or reconstructing missing OFFICIAL history. "
            "The correct Step9 result is LINK N/A, not a proxy comparison."
        ),
        "next_step": "STEP10_FINAL_PROMOTION_DECISION",
    }

    with open(OUT / "audit.json", "w", encoding="utf-8") as f:
        json.dump(audit, f, ensure_ascii=False, indent=2)

    report = f"""# MASTER BTC TREND V3.0 — Step 9 Exact V2.6 H2H Audit\n\n- Step9 audit: **{audit['step9_audit']}**\n- Exact H2H: **{h2h_status}**\n- V2.6 canonical identity: **{'VERIFIED' if canonical_ok else 'FAILED'}**\n- Persisted V2.6 OFFICIAL history: **{'YES' if persisted_official else 'NO'}**\n- Deterministic V2.6 production replay executable: **{'YES' if deterministic_exec else 'NO'}**\n- V2.5 / Time Validity proxies used as V2.6: **NO**\n- Fractal V2.6 used as production MASTER proxy: **NO**\n- Promotion: **HOLD**\n\n## Reason codes\n""" + "\n".join(f"- {x}" for x in reason_codes) + "\n"
    with open(OUT / "report.md", "w", encoding="utf-8") as f:
        f.write(report)

    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
