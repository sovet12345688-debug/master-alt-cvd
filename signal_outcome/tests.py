from __future__ import annotations

import datetime as dt
import pathlib
import tempfile

from signal_outcome import vault as v

UTC = dt.timezone.utc


def sample(ts: str) -> dict:
    return {
        "signal_id": "TEST-1",
        "observed_at_utc": ts,
        "source_master": "TEST_MASTER",
        "source_version": "0",
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "observed_price": 100.0,
        "signal_label": "TEST",
        "scores": {"score": 85, "missing": None},
        "evidence": {},
        "tags": ["A", "A"]
    }


def test_metrics() -> None:
    candles = [[0, "100", "110", "95", "105", "1", 1], [2, "105", "120", "90", "115", "1", 3]]
    a = v.outcome_metrics("LONG", 100.0, candles)
    assert a["direction_return_pct"] == 15.0
    assert a["mfe_pct"] == 20.0
    assert a["mae_pct"] == -10.0
    b = v.outcome_metrics("SHORT", 100.0, candles)
    assert b["direction_return_pct"] == -15.0
    assert b["mfe_pct"] == 10.0
    assert b["mae_pct"] == -20.0


def test_time_guards() -> None:
    now = dt.datetime(2026, 9, 4, 4, 0, tzinfo=UTC)
    try:
        v.validate_signal(sample("2026-09-04T05:00:00Z"), now=now)
        raise AssertionError("future timestamp accepted")
    except ValueError as e:
        assert "future" in str(e).lower()
    try:
        v.validate_signal(sample("2026-09-04T03:00:00Z"), now=now)
        raise AssertionError("historical backfill accepted")
    except ValueError as e:
        assert "backfill" in str(e).lower()
    ok = v.validate_signal(sample("2026-09-04T03:50:00Z"), now=now)
    assert ok["scores"]["missing"] is None
    assert ok["tags"] == ["A"]


def test_horizon_maturity_without_network() -> None:
    original = (v.SIGNALS, v.OUTCOMES, v.SUMMARY, v.VAULT_STATE)
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        v.SIGNALS = root / "signals.jsonl"
        v.OUTCOMES = root / "outcomes.jsonl"
        v.SUMMARY = root / "summary.json"
        v.VAULT_STATE = root / "state.json"
        sig = sample("2026-09-04T00:00:00Z")
        sig["ingested_at_utc"] = sig["observed_at_utc"]
        sig["payload_sha256"] = "x"
        v.write_jsonl(v.SIGNALS, [sig])
        candles = []
        for h in range(8):
            open_ms = int(dt.datetime(2026, 9, 4, h, tzinfo=UTC).timestamp() * 1000)
            close_ms = open_ms + 3600 * 1000 - 1
            candles.append([open_ms, "100", "102", "99", str(100 + h), "1", close_ms])
        fake = lambda symbol, start, end: candles
        r = v.evaluate(now=dt.datetime(2026, 9, 4, 5, 0, tzinfo=UTC), fetcher=fake)
        assert r["newly_evaluated_horizons"] == 1
        out = v.read_jsonl(v.OUTCOMES)[0]
        assert "4H" in out["horizons"]
        assert "24H" not in out["horizons"]
    v.SIGNALS, v.OUTCOMES, v.SUMMARY, v.VAULT_STATE = original


def test_deterministic_jsonl() -> None:
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "x.jsonl"
        rows = [{"signal_id": "b", "x": 1}, {"signal_id": "a", "x": None}]
        v.write_jsonl(p, rows)
        first = p.read_text()
        v.write_jsonl(p, rows)
        assert first == p.read_text()
        assert v.read_jsonl(p)[1]["x"] is None


def test_score_band() -> None:
    c = v.config()
    assert v.score_band(85, c) == "80-89"
    assert v.score_band(100, c) == "90-100"


if __name__ == "__main__":
    test_metrics()
    test_time_guards()
    test_horizon_maturity_without_network()
    test_deterministic_jsonl()
    test_score_band()
    print("SIGNAL OUTCOME VAULT V0.1 TESTS PASS")
