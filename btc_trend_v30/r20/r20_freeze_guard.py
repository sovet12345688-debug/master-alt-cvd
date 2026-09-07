from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MANIFEST = json.loads((HERE / "r20_freeze_manifest.json").read_text(encoding="utf-8"))
CANDIDATE = json.loads((HERE / "r20_candidate_config.json").read_text(encoding="utf-8"))
FROZEN = json.loads((HERE / "r20_frozen_config.json").read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_blob_sha1(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def main() -> dict:
    ids = MANIFEST["identities"]
    checks = {}
    checks["model"] = MANIFEST["model"] == CANDIDATE["model"] == FROZEN["model"] == "MASTER_BTC_TREND_V3_R2_0"
    checks["frozen_status"] = FROZEN["status"] == "FINAL_FROZEN_NO_REPLAY"
    checks["candidate_status"] = CANDIDATE["status"] == "PRE_FREEZE_CANDIDATE_NO_REPLAY"
    checks["candidate_sha"] = sha256_file(HERE / "r20_candidate_config.json") == ids["candidate_config_sha256"]
    checks["frozen_sha"] = sha256_file(HERE / "r20_frozen_config.json") == ids["frozen_config_sha256"]
    checks["engine_blob"] = git_blob_sha1(HERE / "r20_engine.py") == ids["engine_git_blob_sha1"]
    checks["contract_blob"] = git_blob_sha1(HERE / "r20_contract.py") == ids["contract_git_blob_sha1"]

    semantic_sections = ["evidence_firewall", "data", "features", "long", "short", "holding", "reset", "predeclared_candidate_gates", "forbidden"]
    checks["candidate_frozen_semantic_identity"] = all(CANDIDATE[k] == FROZEN[k] for k in semantic_sections)
    checks["no_replay_before_freeze"] = FROZEN["freeze_metadata"]["historical_replay_performed_before_freeze"] is False
    checks["historical_cannot_promote"] = FROZEN["evidence_firewall"]["historical_replay_can_promote"] is False
    checks["forward_start"] = FROZEN["evidence_firewall"]["untouched_forward_start"] == "2026-09-05T00:00:00Z"

    replay_candidates = [
        HERE / "output" / "historical_replay" / "audit.json",
        HERE / "output" / "full_replay" / "audit.json",
    ]
    checks["replay_output_absent"] = not any(p.exists() for p in replay_candidates)

    g = FROZEN["predeclared_candidate_gates"]
    locked = MANIFEST["locked_gates"]
    checks["gates_locked"] = (
        g["mcr_90d_mean_min"] == locked["mcr_90d_mean_min"] == 0.20
        and g["mcr_365d_mean_min"] == locked["mcr_365d_mean_min"] == 0.20
        and g["trend_recall_90d_min"] == locked["trend_recall_90d_min"] == 0.55
        and g["trend_recall_365d_min"] == locked["trend_recall_365d_min"] == 0.55
        and g["confirmed_false_start_rate_max"] == locked["confirmed_false_start_rate_max"] == 0.45
        and g["capture_to_loss_ratio_gt"] == locked["capture_to_loss_ratio_gt"] == 1.10
        and g["median_seed_lag_days_max"] == locked["median_seed_lag_days_max"] == 7.0
        and g["cycle_bucket_expectancy_floor_R"] == locked["cycle_bucket_expectancy_floor_R"] == -0.15
        and g["cycle_bucket_min_n"] == locked["cycle_bucket_min_n"] == 10
    )

    long_r = FROZEN["long"]["seed"]["risk_R"] + FROZEN["long"]["confirm"]["risk_add_R"] + FROZEN["long"]["core"]["risk_add_R"]
    short_r = FROZEN["short"]["seed"]["risk_R"] + FROZEN["short"]["confirm"]["risk_add_R"] + FROZEN["short"]["core"]["risk_add_R"]
    checks["risk_locked"] = abs(long_r - 1.0) < 1e-12 and abs(short_r - 0.85) < 1e-12
    checks["no_stop_widening"] = FROZEN["holding"]["stop_may_widen"] is False
    checks["no_fixed_full_tp"] = FROZEN["holding"]["fixed_profit_target"] is None
    checks["forbidden_policies"] = all(bool(v) for v in FROZEN["forbidden"].values())

    r14_cfg = ROOT / "btc_trend_v30" / "r14" / "r14_frozen_config.json"
    r14_engine = ROOT / "btc_trend_v30" / "r14" / "r14_engine.py"
    checks["r14_config_untouched"] = git_blob_sha1(r14_cfg) == ids["r14_frozen_config_git_blob_sha1"]
    checks["r14_engine_untouched"] = git_blob_sha1(r14_engine) == ids["r14_engine_git_blob_sha1"]

    passed = all(checks.values())
    audit = {
        "freeze_guard": "PASS" if passed else "FAIL",
        "model": MANIFEST["model"],
        "checks": checks,
        "frozen_config_sha256": sha256_file(HERE / "r20_frozen_config.json"),
        "engine_git_blob_sha1": git_blob_sha1(HERE / "r20_engine.py"),
        "contract_git_blob_sha1": git_blob_sha1(HERE / "r20_contract.py"),
        "historical_replay_performed_before_freeze": False,
    }
    print(json.dumps(audit, indent=2))
    if not passed:
        raise SystemExit(1)
    return audit


if __name__ == "__main__":
    main()
