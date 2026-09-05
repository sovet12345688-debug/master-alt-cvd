from __future__ import annotations

import datetime as dt

from signal_outcome import vault as v

UTC = dt.timezone.utc


def test_full_7d_three_page_pagination() -> None:
    interval_ms = 5 * 60 * 1000
    calls = []

    class Resp:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self.data

    class Session:
        def get(self, url, params, timeout):
            calls.append(dict(params))
            start = int(params["startTime"])
            end_time = int(params["endTime"])
            max_rows = min(1000, max(0, ((end_time - start) // interval_ms) + 1))
            data = []
            for i in range(max_rows):
                op = start + i * interval_ms
                cp = op + interval_ms - 1
                if cp > end_time:
                    break
                data.append([op, "100", "101", "99", "100", "1", cp])
            return Resp(data)

    start = dt.datetime(2026, 9, 4, 0, 0, tzinfo=UTC)
    end = start + dt.timedelta(days=7)
    rows = v.fetch_klines("BTCUSDT", start, end, session_factory=Session)

    assert len(rows) == 2016
    assert len(calls) == 3
    assert len(rows[:1000]) == 1000
    assert calls[1]["startTime"] == rows[999][0] + interval_ms
    assert calls[2]["startTime"] == rows[1999][0] + interval_ms
    assert rows[-1][6] < int(end.timestamp() * 1000)


if __name__ == "__main__":
    test_full_7d_three_page_pagination()
    print("SIGNAL OUTCOME VAULT 7D PAGINATION TEST PASS")
