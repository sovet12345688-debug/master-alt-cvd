#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPT = ROOT / "master_prompts/master_market_v1_2_current.md"
CONTRACT = ROOT / "state/master_market_v1_2_contract.json"
SCORE_ENGINE = ROOT / "market_scoring/run_score_engine.py"
PATCH_UPDATED_KST = "2026-09-12T21:26:00+09:00"
MARKER = "## SCORE HISTORY SEPARATION + DXY/OIL RECOVERY — APPROVED 2026-09-12"

OLD_HISTORY_LINE = "- `state/master_market_official_history.csv` is comparison/history only. It may supply prior/1D/3D/7D context after valid OFFICIAL observations accumulate, but it is never a current-score fallback."
OLD_PERSIST_LINE = "- Only confirmed OFFICIAL runs may persist official score history through the existing OFFICIAL persistence path. WATCH/manual non-OFFICIAL must not write or overwrite score history."

NEW_HISTORY_BLOCK = """- `state/master_market_score_history.csv` is the authoritative comparison/history source for the four core scores (`market_positive | liquidity_lead | crypto_money_inflow | alt_money_inflow`). It may supply prior/1D/3D/7D context after valid scheduled OFFICIAL score snapshots accumulate, but it is never a current-score fallback.
- `state/master_market_official_history.csv` remains the legacy/final OFFICIAL decision-history file used by the separate OFFICIAL State Bridge. It contains direction/Risk Veto context and is not the new core-score delta source after the 2026-09-12 separation."""

NEW_PERSIST_LINE = "- Core-score history persistence is separated from final decision persistence: the :58 production scorer may append only the four numeric core scores to `state/master_market_score_history.csv` when its snapshot maps to a locked OFFICIAL hour. WATCH/manual non-OFFICIAL snapshots never write score history. Final LONG/SHORT and Risk Veto remain owned by the separate OFFICIAL State Bridge and are never invented by the score-history writer."

RECOVERY_BLOCK = """

## SCORE HISTORY SEPARATION + DXY/OIL RECOVERY — APPROVED 2026-09-12

- User approved split persistence: core score history and final OFFICIAL decision state are separate responsibilities.
- Core score comparison source = `state/master_market_score_history.csv`; fields are run_id/scheduled_kst/score_generated_at_utc/market_positive/liquidity_lead/crypto_money_inflow/alt_money_inflow/overall_coverage_pct/status.
- Final decision state remains separate through `official_state/process_market_inbox.py` / `official_state/latest/market.json`; no score-history process may invent LONG/SHORT or Risk Veto.
- DXY recovery output = `market_vault/output/latest_dxy.json`, produced by `market_vault/dxy_adapter.py` using actual Yahoo Finance `DX-Y.NYB` observations only. Current/1D/3D/7D use same-instrument actual observations; no broad-dollar proxy substitution or interpolation.
- Oil recovery output = `market_vault/output/latest_oil.json`, produced by `market_vault/oil_adapter.py` using actual Yahoo Finance `CL=F` (WTI front-month futures) and `BZ=F` (Brent front-month futures) observations only. Current/1D/3D/7D are same-instrument actual observations; label futures explicitly and do not present them as physical spot assessments.
- Oil adapter output feeds the existing Oil Hard Importance axis only. It does not add a new score weight and oil rise alone still does not automatically equal Risk-Off.
- DXY/Oil adapters run synchronously inside `.github/workflows/master_market_score_engine_active.yml` immediately before the production score calculation, preventing schedule-race mismatches.
- `market_vault/output/latest_summary.json` remains owned by the existing Vault workflow and is not committed by the DXY/Oil recovery path; this avoids cross-workflow write races. User-visible DXY/Oil reads use their dedicated outputs.
"""


def patch_prompt() -> bool:
    text = PROMPT.read_text(encoding="utf-8")
    changed = False
    if NEW_HISTORY_BLOCK not in text:
        if OLD_HISTORY_LINE not in text:
            raise SystemExit("CANONICAL_PATCH_BLOCKED_HISTORY_TARGET_MISSING")
        text = text.replace(OLD_HISTORY_LINE, NEW_HISTORY_BLOCK, 1)
        changed = True
    if NEW_PERSIST_LINE not in text:
        if OLD_PERSIST_LINE not in text:
            raise SystemExit("CANONICAL_PATCH_BLOCKED_PERSIST_TARGET_MISSING")
        text = text.replace(OLD_PERSIST_LINE, NEW_PERSIST_LINE, 1)
        changed = True
    if MARKER not in text:
        anchor = "## BTC LIQUIDITY LEAD INDEX"
        if anchor not in text:
            raise SystemExit("CANONICAL_PATCH_BLOCKED_ANCHOR_MISSING")
        text = text.replace(anchor, RECOVERY_BLOCK.strip() + "\n\n" + anchor, 1)
        changed = True
    if changed:
        PROMPT.write_text(text, encoding="utf-8")
    return changed


def patch_contract() -> bool:
    c = json.loads(CONTRACT.read_text(encoding="utf-8"))
    before = json.dumps(c, ensure_ascii=False, sort_keys=True)

    schedule = c.setdefault("schedule", {})
    schedule["score_history_file"] = "state/master_market_score_history.csv"
    schedule["final_official_state_separate"] = True

    sp = c.setdefault("source_policy", {})
    sp["score_history"] = "Read state/master_market_score_history.csv for prior/1D/3D/7D context of the four core scores only. Current scores always come from market_scoring/output/latest_scores.json. Score history contains no invented direction/Risk Veto and WATCH/manual non-OFFICIAL cannot write it."
    sp["dxy_adapter"] = "Read market_vault/output/latest_dxy.json; produced by market_vault/dxy_adapter.py from actual Yahoo Finance DX-Y.NYB observations only; current/1D/3D/7D same-instrument comparisons; no broad-dollar proxy substitution/interpolation. Runs synchronously before active score calculation."
    sp["oil_adapter"] = "Read market_vault/output/latest_oil.json; produced by market_vault/oil_adapter.py from actual Yahoo Finance CL=F WTI front-month futures and BZ=F Brent front-month futures; current/1D/3D/7D same-instrument comparisons; label futures explicitly; no interpolation/cross-instrument substitution. Runs synchronously before active score calculation."

    hist = c.setdefault("official_score_history", {})
    hist["enabled"] = True
    hist["file"] = "state/master_market_score_history.csv"
    hist["no_backfill"] = True
    hist["append_only"] = True
    hist["fields"] = [
        "run_id",
        "scheduled_kst",
        "score_generated_at_utc",
        "market_positive",
        "liquidity_lead",
        "crypto_money_inflow",
        "alt_money_inflow",
        "overall_coverage_pct",
        "status",
    ]
    hist["rule"] = "Only scheduled score snapshots mapped to locked OFFICIAL KST hours are appended. WATCH/manual non-OFFICIAL snapshots never write. This file stores core scores only and never invents LONG/SHORT or Risk Veto."
    hist["current_score_source"] = "market_scoring/output/latest_scores.json"
    hist["current_score_fallback_to_history_forbidden"] = True
    hist["structural_na_gap_backfill_forbidden"] = True
    hist["final_official_state_separate"] = True
    hist["final_official_state_bridge"] = "official_state/process_market_inbox.py"
    hist["legacy_final_decision_history"] = "state/master_market_official_history.csv"

    after_without_timestamp = json.dumps(c, ensure_ascii=False, sort_keys=True)
    if before != after_without_timestamp:
        c["updated_kst"] = PATCH_UPDATED_KST
        CONTRACT.write_text(json.dumps(c, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True
    return False


def patch_score_engine() -> bool:
    text = SCORE_ENGINE.read_text(encoding="utf-8")
    old = '"score_history_source": "state/master_market_official_history.csv"'
    new = '"score_history_source": "state/master_market_score_history.csv"'
    if old not in text:
        if new in text:
            return False
        raise SystemExit("SCORE_ENGINE_PATCH_BLOCKED_HISTORY_SOURCE_MISSING")
    text = text.replace(old, new)
    SCORE_ENGINE.write_text(text, encoding="utf-8")
    return True


def main() -> int:
    p = patch_prompt()
    c = patch_contract()
    s = patch_score_engine()
    print(f"MASTER_MARKET_NA_CONTRACT_PATCH=PASS prompt_changed={p} contract_changed={c} scorer_changed={s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
