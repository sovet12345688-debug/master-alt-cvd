#!/usr/bin/env python3
"""Fail-closed bootstrap validator for one isolated MONEY OS Work V3 worker."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
V3 = ROOT / "money_os_work_v3"
REGISTRY = V3 / "registry/SYSTEM_REGISTRY.json"
ISOLATION = V3 / "registry/ISOLATION_POLICY.json"
EXPECTED_SYSTEMS = {
    "alt_top100",
    "alt_final20",
    "market",
    "btc_trend",
    "youtuber_view",
    "trading",
}


class ShadowValidationError(RuntimeError):
    """Raised when a worker cannot fail-closed bootstrap."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - message is the useful result
        raise ShadowValidationError(f"cannot load JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ShadowValidationError(f"JSON root must be an object: {path}")
    return value


def repo_path(relative: str) -> Path:
    if not relative or Path(relative).is_absolute():
        raise ShadowValidationError(f"invalid repository-relative path: {relative!r}")
    resolved = (ROOT / relative).resolve()
    if not resolved.is_relative_to(ROOT.resolve()):
        raise ShadowValidationError(f"path escapes repository: {relative}")
    return resolved


def git_blob_oid(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()  # noqa: S324 - Git object ID


def git_show_main(relative: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "show", f"origin/main:{relative}"],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise ShadowValidationError(
            f"cannot resolve current main canonical {relative}: {detail}"
        ) from exc


def current_main_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "origin/main"],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout.strip()
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip()
        raise ShadowValidationError(f"cannot resolve origin/main: {detail}") from exc


def _canonical_paths(registry_entry: dict[str, Any]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for role, key in (
        ("canonical", "canonical_source"),
        ("machine_contract", "machine_contract"),
        ("ui_contract", "ui_contract"),
    ):
        value = registry_entry.get(key)
        if value:
            result.append((role, value))
    return result


def _expected_blob(
    system_id: str, worker: dict[str, Any], role: str, canonical_mode: str
) -> str:
    if system_id == "market":
        prefix = "main" if canonical_mode == "main" else "shadow_candidate"
        key = f"{prefix}_{'canonical' if role == 'canonical' else role}_blob"
    elif system_id == "youtuber_view":
        if canonical_mode != "candidate":
            raise ShadowValidationError("youtuber_view has no prior main canonical")
        key = "canonical_blob"
    else:
        if canonical_mode != "main":
            raise ShadowValidationError(
                f"{system_id}: candidate canonical mode is not authorized"
            )
        key = {
            "canonical": "canonical_blob",
            "machine_contract": "machine_contract_blob",
            "ui_contract": "ui_contract_blob",
        }[role]
    expected = worker.get(key)
    if not isinstance(expected, str) or len(expected) != 40:
        raise ShadowValidationError(f"{system_id}: missing expected blob {key}")
    return expected


def _materialize_smoke_runtime(
    destination: Path, contract: dict[str, Any], receipt: dict[str, Any]
) -> None:
    destination = destination.resolve()
    if destination == ROOT.resolve() or ROOT.resolve().is_relative_to(destination):
        raise ShadowValidationError("smoke runtime destination is too broad")
    destination.mkdir(parents=True, exist_ok=True)
    for store in contract.get("stores", []):
        target = (destination / store).resolve()
        if not target.is_relative_to(destination):
            raise ShadowValidationError(f"runtime store escapes destination: {store}")
        target.mkdir(parents=True, exist_ok=True)
    (destination / "shadow_bootstrap_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def validate_worker(
    system_id: str,
    canonical_mode: str | None = None,
    smoke_runtime_root: Path | None = None,
) -> dict[str, Any]:
    if system_id not in EXPECTED_SYSTEMS:
        raise ShadowValidationError(f"unknown system_id: {system_id}")

    registry = load_json(REGISTRY)
    isolation = load_json(ISOLATION)
    registry_systems = registry.get("systems", {})
    isolation_systems = isolation.get("systems", {})
    if set(registry_systems) != EXPECTED_SYSTEMS:
        raise ShadowValidationError("registry system set is not exactly the approved six")
    if set(isolation_systems) != EXPECTED_SYSTEMS:
        raise ShadowValidationError("isolation system set is not exactly the approved six")

    entry = registry_systems[system_id]
    manifest_path = repo_path(f"money_os_work_v3/systems/{system_id}/manifest.json")
    manifest = load_json(manifest_path)
    worker = manifest.get("worker_shadow", {})
    if manifest.get("system_id") != system_id:
        raise ShadowValidationError(f"{system_id}: manifest identity mismatch")

    prompt_relative = worker.get("prompt")
    runtime_contract_relative = worker.get("runtime_contract")
    if not isinstance(prompt_relative, str) or not isinstance(
        runtime_contract_relative, str
    ):
        raise ShadowValidationError(f"{system_id}: worker prompt/contract missing")
    own_root = f"money_os_work_v3/systems/{system_id}/"
    if not prompt_relative.startswith(own_root) or not runtime_contract_relative.startswith(
        own_root
    ):
        raise ShadowValidationError(f"{system_id}: worker files escape own namespace")

    prompt_payload = repo_path(prompt_relative).read_bytes()
    contract_path = repo_path(runtime_contract_relative)
    contract_payload = contract_path.read_bytes()
    contract = load_json(contract_path)
    if git_blob_oid(prompt_payload) != worker.get("prompt_blob"):
        raise ShadowValidationError(f"{system_id}: worker prompt blob mismatch")
    if git_blob_oid(contract_payload) != worker.get("runtime_contract_blob"):
        raise ShadowValidationError(f"{system_id}: runtime contract blob mismatch")
    if contract.get("system_id") != system_id:
        raise ShadowValidationError(f"{system_id}: runtime contract identity mismatch")
    if contract.get("runtime_root") != entry.get("runtime_root"):
        raise ShadowValidationError(f"{system_id}: runtime root mismatch")
    if set(contract.get("stores", [])) != set(manifest.get("local_stores", [])):
        raise ShadowValidationError(f"{system_id}: runtime store manifest mismatch")

    shadow = contract.get("shadow", {})
    if shadow.get("enabled") is not True:
        raise ShadowValidationError(f"{system_id}: Shadow mode must be enabled")
    for key in (
        "notifications_enabled",
        "production_writes_enabled",
        "schedule_activation_enabled",
        "cutover_enabled",
    ):
        if shadow.get(key) is not False:
            raise ShadowValidationError(f"{system_id}: {key} must remain false")
    if worker.get("notifications_enabled") is not False:
        raise ShadowValidationError(f"{system_id}: manifest notifications must be false")
    if worker.get("production_cutover") is not False:
        raise ShadowValidationError(f"{system_id}: manifest cutover must be false")

    policy = isolation_systems[system_id]
    expected_write = f"money_os_work_v3/systems/{system_id}/runtime/**"
    if policy.get("allowed_runtime_write") != [expected_write]:
        raise ShadowValidationError(f"{system_id}: write allowlist mismatch")
    allowed_canonicals = set(policy.get("allowed_canonical_read", []))
    canonical_paths = _canonical_paths(entry)
    if {path for _, path in canonical_paths} != allowed_canonicals:
        raise ShadowValidationError(f"{system_id}: canonical read allowlist mismatch")

    if not isinstance(worker.get("source_chat"), str) or not worker[
        "source_chat"
    ].startswith("https://chatgpt.com/share/"):
        raise ShadowValidationError(f"{system_id}: source chat provenance missing")
    if len(str(worker.get("source_chat_sha256", ""))) != 64:
        raise ShadowValidationError(f"{system_id}: source chat digest missing")
    if not repo_path(str(worker.get("parity_audit"))).is_file():
        raise ShadowValidationError(f"{system_id}: parity audit is missing")

    if canonical_mode is None:
        canonical_mode = "candidate" if system_id == "youtuber_view" else "main"
    if canonical_mode not in {"main", "candidate"}:
        raise ShadowValidationError(f"invalid canonical mode: {canonical_mode}")

    canonical_receipts: list[dict[str, str]] = []
    for role, relative in canonical_paths:
        payload = (
            git_show_main(relative)
            if canonical_mode == "main"
            else repo_path(relative).read_bytes()
        )
        actual = git_blob_oid(payload)
        expected = _expected_blob(system_id, worker, role, canonical_mode)
        if actual != expected:
            raise ShadowValidationError(
                f"{system_id}: {canonical_mode} {role} blob mismatch "
                f"(expected {expected}, got {actual})"
            )
        canonical_receipts.append(
            {"role": role, "path": relative, "blob": actual, "source": canonical_mode}
        )

    status = "PASS"
    hold_reason = None
    if system_id == "market" and canonical_mode == "main":
        status = "HOLD_CURRENT_MAIN_SOURCE_CHAT_CONFLICT"
        hold_reason = "easy-Korean SCREEN4 five-row labels exist only in Shadow candidate"

    receipt: dict[str, Any] = {
        "schema_version": "1.0",
        "system_id": system_id,
        "worker_instance": contract.get("worker_instance"),
        "status": status,
        "canonical_mode": canonical_mode,
        "origin_main_head": current_main_head(),
        "canonical_receipts": canonical_receipts,
        "source_chat_sha256": worker.get("source_chat_sha256"),
        "runtime_root": contract.get("runtime_root"),
        "stores": contract.get("stores"),
        "cross_system_reads": 0,
        "cross_system_writes": 0,
        "notifications_enabled": False,
        "production_writes_enabled": False,
        "schedule_activation_enabled": False,
        "production_cutover": False,
    }
    if hold_reason:
        receipt["hold_reason"] = hold_reason
    if smoke_runtime_root is not None:
        _materialize_smoke_runtime(smoke_runtime_root, contract, receipt)
        receipt["smoke_runtime_materialized"] = str(smoke_runtime_root.resolve())
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("system_id", choices=sorted(EXPECTED_SYSTEMS))
    parser.add_argument("--canonical-mode", choices=("main", "candidate"))
    parser.add_argument("--smoke-runtime-root", type=Path)
    args = parser.parse_args()
    try:
        receipt = validate_worker(
            args.system_id, args.canonical_mode, args.smoke_runtime_root
        )
    except ShadowValidationError as exc:
        print(json.dumps({"system_id": args.system_id, "status": "FAIL", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 2 if receipt["status"].startswith("HOLD_") else 0


if __name__ == "__main__":
    sys.exit(main())
