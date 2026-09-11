#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "master_prompts/master_market_v1_2_current.md"
CONTRACT = ROOT / "state/master_market_v1_2_contract.json"
SCORE_CONTRACT = ROOT / "market_scoring/score_engine_contract.json"
SCORE_OUTPUT = ROOT / "market_scoring/output/latest_scores.json"
ACTIVE_WORKFLOW = ROOT / ".github/workflows/master_market_score_engine_active.yml"

MARKER = "## ACTIVE SCORE ENGINE LOCK — APPROVED 2026-09-11"
APPROVED_AT = "2026-09-11T10:04:10+09:00"
ENGINE_ID = "MASTER_MARKET_SCORE_ENGINE_V1"
SCORE_CONTRACT_REL = "market_scoring/score_engine_contract.json"
SCORE_OUTPUT_REL = "market_scoring/output/latest_scores.json"
ACTIVE_WORKFLOW_REL = ".github/workflows/master_market_score_engine_active.yml"

SECTION = f"""{MARKER}

User explicitly approved the previously exposed candidate formula **as-is** at `{APPROVED_AT}`. This approval activates the deterministic production score engine without historical back-solving or past-score copying.

- Authoritative formula/transform contract = `{SCORE_CONTRACT_REL}`.
- Current machine score output = `{SCORE_OUTPUT_REL}` with `engine={ENGINE_ID}`.
- Production refresh workflow = `{ACTIVE_WORKFLOW_REL}`; scheduled every hour at `:58` so a fresh score artifact is prepared before the next hourly MASTER decision cycle.
- Required current-run core scores = `liquidity_lead | crypto_money_inflow | alt_money_inflow | market_positive`.
- When `latest_scores.json` is fresh/schema-compatible and the required score is numeric, use that current-run value. Do **not** replace it with the last OFFICIAL score.
- The former structural failure reason `BLOCKED_MISSING_AUTHORITATIVE_FORMULA` is resolved and must not be used for these four scores after this activation.
- Core-score N/A is allowed only for a real current-run failure such as missing/incompatible/stale machine output, `score=null` because no confirmed component exists, or a freshness/validation guard failure. Explain the actual cause in `N/A 항목 안내`.
- Machine output freshness limit = **180 minutes**. Stale required input/output => fail-closed N/A; never reuse an old score as current.
- Confirmed component weights are renormalized. If confirmed coverage is below 70%, a numeric `PARTIAL` score may be shown only from confirmed components, Confidence is capped at C, and strong threshold alerts are forbidden.
- Existing BTC Liquidity Lead thresholds **55 / 65 / 75** and Market Positive bands remain unchanged.
- `state/master_market_official_history.csv` is comparison/history only. It may supply prior/1D/3D/7D context after valid OFFICIAL observations accumulate, but it is never a current-score fallback.
- Only confirmed OFFICIAL runs may persist official score history through the existing OFFICIAL persistence path. WATCH/manual non-OFFICIAL must not write or overwrite score history.
- No historical backfill for the structural-N/A gap. New history accumulates prospectively from valid OFFICIAL runs after activation.
- Forbidden fallbacks remain locked: `last-known score reuse | N/A=0 | historical score backsolve | invented equal weights | cross-MASTER score substitution`.

"""

OFFICIAL_OLD = "`🕒 MASTER MARKET V1.2 | 실행완료: YYYY-MM-DD HH:mm KST | 다음 정식 보고 시간: YYYY-MM-DD HH:mm KST`"
OFFICIAL_NEW = "`🕒 MASTER MARKET V1.2 | 실행완료: YYYY-MM-DD HH:mm KST | 롱/숏 | 다음 정식 보고 시간: YYYY-MM-DD HH:mm KST`"
WATCH_OLD = "`🕒 MASTER MARKET WATCH | 감지완료: YYYY-MM-DD HH:mm KST | 다음 정식 보고 시간: YYYY-MM-DD HH:mm KST`"
WATCH_NEW = "`🕒 MASTER MARKET WATCH | 감지완료: YYYY-MM-DD HH:mm KST | 롱/숏 | 다음 정식 보고 시간: YYYY-MM-DD HH:mm KST`"


def now_kst() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def patch_canonical(text: str) -> str:
    if MARKER not in text:
        anchor = "## BTC LIQUIDITY LEAD INDEX"
        if anchor not in text:
            raise SystemExit(f"canonical anchor missing: {anchor}")
        text = text.replace(anchor, SECTION + anchor, 1)
    text = text.replace(OFFICIAL_OLD, OFFICIAL_NEW)
    text = text.replace(WATCH_OLD, WATCH_NEW)
    return text


def patch_contract(doc: dict) -> dict:
    doc["schema_version"] = "1.12"
    doc["updated_kst"] = now_kst()
    doc.setdefault("schedule", {})["score_refresh_minute"] = 58
    doc["schedule"]["score_refresh_workflow"] = ACTIVE_WORKFLOW_REL

    doc["active_score_engine"] = {
        "enabled": True,
        "authorized_by_user": True,
        "approved_at_kst": APPROVED_AT,
        "engine_id": ENGINE_ID,
        "score_contract": SCORE_CONTRACT_REL,
        "current_output": SCORE_OUTPUT_REL,
        "workflow": ACTIVE_WORKFLOW_REL,
        "refresh_minute_each_hour": 58,
        "required_current_scores": [
            "liquidity_lead",
            "crypto_money_inflow",
            "alt_money_inflow",
            "market_positive",
        ],
        "current_value_rule": "Use fresh/schema-compatible latest_scores.json current-run numeric values. Never copy prior OFFICIAL/history as current.",
        "history_value_rule": "state/master_market_official_history.csv is prior/1D/3D/7D comparison history only and is never a current-score fallback.",
        "freshness_max_age_minutes": 180,
        "valid_output_status": ["ACTIVE_OK", "PARTIAL"],
        "structural_formula_na_resolved": True,
        "forbidden_old_reason": "BLOCKED_MISSING_AUTHORITATIVE_FORMULA",
        "coverage_policy": {
            "confirmed_weight_renormalization": True,
            "coverage_below_70": "numeric PARTIAL from confirmed components only; Confidence max C; no strong threshold alert",
            "no_confirmed_components": "N/A",
        },
        "fallback_forbidden": [
            "last-known score reuse",
            "N/A=0",
            "historical score backsolve",
            "invented equal weights",
            "cross-MASTER score substitution",
        ],
        "persistence": {
            "official_only": True,
            "watch_write_forbidden": True,
            "manual_non_official_write_forbidden": True,
            "historical_backfill_forbidden": True,
        },
    }

    source_policy = doc.setdefault("source_policy", {})
    source_policy["active_score_engine"] = (
        f"Read {SCORE_OUTPUT_REL} for the four current core scores only when engine={ENGINE_ID}, schema/status/freshness guards pass. "
        "Formula authority is market_scoring/score_engine_contract.json. Stale/incompatible/missing output fails closed to N/A; never use official history as current fallback."
    )

    required_scores = doc.setdefault("required_scores", {})
    required_scores["current_source"] = SCORE_OUTPUT_REL
    required_scores["formula_contract"] = SCORE_CONTRACT_REL
    required_scores["structural_na_due_to_missing_formula"] = "RESOLVED_2026-09-11"

    mobile = doc.setdefault("mobile_output_policy", {})
    mobile["engine_change"] = "ACTIVE_SCORE_ENGINE_V1_APPROVED_2026-09-11"

    na = doc.setdefault("na_explanation_policy", {})
    na["core_score_structural_na_rule"] = (
        "For liquidity_lead/crypto_money_inflow/alt_money_inflow/market_positive, BLOCKED_MISSING_AUTHORITATIVE_FORMULA is no longer valid after 2026-09-11 activation. "
        "If a core score is N/A, report the real current failure: missing/stale/incompatible latest_scores output, failed freshness/validation guard, or no confirmed component."
    )

    history = doc.setdefault("official_score_history", {})
    history["current_score_source"] = SCORE_OUTPUT_REL
    history["current_score_fallback_to_history_forbidden"] = True
    history["structural_na_gap_backfill_forbidden"] = True

    required_output = doc.setdefault("required_output", {})
    required_output["footer"] = "🕒 MASTER MARKET V1.2 | 실행완료: YYYY-MM-DD HH:mm KST | 롱/숏 | 다음 정식 보고 시간: YYYY-MM-DD HH:mm KST"
    required_output["watch_footer"] = "🕒 MASTER MARKET WATCH | 감지완료: YYYY-MM-DD HH:mm KST | 롱/숏 | 다음 정식 보고 시간: YYYY-MM-DD HH:mm KST"
    return doc


def patch_active_workflow(text: str) -> str:
    canonical_line = "      - 'master_prompts/master_market_v1_2_current.md'"
    contract_line = "      - 'state/master_market_v1_2_contract.json'"
    if text.count(canonical_line) >= 2 and text.count(contract_line) >= 2:
        return text
    needle = "      - 'market_scoring/validate_score_engine.py'\n      - '.github/workflows/master_market_score_engine_active.yml'"
    replacement = (
        "      - 'market_scoring/validate_score_engine.py'\n"
        + canonical_line + "\n"
        + contract_line + "\n"
        + "      - '.github/workflows/master_market_score_engine_active.yml'"
    )
    if text.count(needle) != 2:
        raise SystemExit(f"active workflow trigger anchor count unexpected: {text.count(needle)}")
    return text.replace(needle, replacement)


def validate() -> None:
    canonical = CANONICAL.read_text(encoding="utf-8")
    contract = load_json(CONTRACT)
    score_contract = load_json(SCORE_CONTRACT)
    score_output = load_json(SCORE_OUTPUT)
    workflow = ACTIVE_WORKFLOW.read_text(encoding="utf-8")

    assert canonical.count(MARKER) == 1
    assert OFFICIAL_NEW in canonical and WATCH_NEW in canonical
    assert "BLOCKED_MISSING_AUTHORITATIVE_FORMULA` is resolved" in canonical

    assert contract["schema_version"] == "1.12"
    active = contract["active_score_engine"]
    assert active["enabled"] is True and active["authorized_by_user"] is True
    assert active["engine_id"] == ENGINE_ID
    assert active["current_output"] == SCORE_OUTPUT_REL
    assert active["structural_formula_na_resolved"] is True
    assert contract["required_output"]["footer"].find("| 롱/숏 |") > 0
    assert contract["required_output"]["watch_footer"].find("| 롱/숏 |") > 0
    assert contract["schedule"]["score_refresh_minute"] == 58

    assert score_contract["status"] == "ACTIVE"
    assert score_contract["engine_id"] == ENGINE_ID
    assert score_output["engine"] == ENGINE_ID
    assert score_output["status"] in ["ACTIVE_OK", "PARTIAL"]
    assert score_output["official_persistence_eligible"] is True
    for key in active["required_current_scores"]:
        score = score_output["scores"][key]["score"]
        assert score is not None and 0 <= score <= 100, (key, score)

    assert workflow.count("master_prompts/master_market_v1_2_current.md") >= 2
    assert workflow.count("state/master_market_v1_2_contract.json") >= 2
    print("MASTER_MARKET_CANONICAL_SCORE_SYNC=PASS")


def apply() -> None:
    original_contract = load_json(CONTRACT)
    canonical = patch_canonical(CANONICAL.read_text(encoding="utf-8"))
    contract = patch_contract(original_contract)
    workflow = patch_active_workflow(ACTIVE_WORKFLOW.read_text(encoding="utf-8"))

    if not set(original_contract).issubset(contract):
        raise SystemExit("top-level contract key regression detected")

    CANONICAL.write_text(canonical, encoding="utf-8")
    CONTRACT.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ACTIVE_WORKFLOW.write_text(workflow, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not args.apply and not args.check:
        parser.error("choose --apply and/or --check")
    if args.apply:
        apply()
    if args.check:
        validate()
