from __future__ import annotations

from ledger_integrity_guard import build_map, sha256_obj, verify_prior_chain, verify_subset


def expect_fail(fn, label: str) -> None:
    try:
        fn()
    except RuntimeError:
        return
    raise AssertionError(f"EXPECTED_FAIL_NOT_RAISED:{label}")


def main() -> None:
    # Append is allowed; prior immutable row remains unchanged.
    r = verify_subset({"a": "h1"}, {"a": "h1", "b": "h2"}, "append")
    assert r["new"] == 1 and r["pass"] is True

    # Deletion and mutation must fail.
    expect_fail(lambda: verify_subset({"a": "h1"}, {}, "delete"), "delete")
    expect_fail(lambda: verify_subset({"a": "h1"}, {"a": "DIFFERENT"}, "mutate"), "mutate")

    # Open position dynamic fields may evolve, frozen identity fields may not.
    keys = ["episode_id"]
    frozen = ["episode_id", "direction", "route", "reference", "seed_time", "seed_entry", "stop", "phase"]
    base = {
        "episode_id": "R26-LONG-0001", "direction": "LONG", "route": "A", "reference": "100",
        "seed_time": "2026-09-08 04:00:00+00:00", "seed_entry": "101", "stop": "95",
        "phase": "STRICT_FORWARD", "marked_R_at_end": "0.1", "resolved": "False"
    }
    dynamic = dict(base, marked_R_at_end="0.8")
    changed_identity = dict(base, stop="96")
    h0 = build_map([base], keys, "pos", frozen)
    h_dynamic = build_map([dynamic], keys, "pos", frozen)
    h_changed = build_map([changed_identity], keys, "pos", frozen)
    assert h0 == h_dynamic
    assert h0 != h_changed

    # Hash-chain verification must accept a valid chain and reject a changed state.
    core_state = {
        "version": 1, "spec": "S", "spec_blob": "B", "strict_forward_start": "T",
        "generation": 1, "initialized_at_utc": "A", "last_verified_at_utc": "A",
        "protected": {}, "counts": {}
    }
    state_hash = sha256_obj(core_state)
    rec_core = {"generation": 1, "asof_utc": "A", "prev_chain_sha256": "", "state_sha256": state_hash, "counts": {}}
    chain = sha256_obj(rec_core)
    state = dict(core_state, last_chain_sha256=chain)
    record = dict(rec_core, chain_sha256=chain)
    verify_prior_chain(state, [record])
    bad_state = dict(state, generation=2)
    expect_fail(lambda: verify_prior_chain(bad_state, [record]), "chain_state_change")

    print("R26_FORWARD_LEDGER_INTEGRITY_SELFTEST_PASS")


if __name__ == "__main__":
    main()
