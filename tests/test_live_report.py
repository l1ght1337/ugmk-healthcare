"""The live-run report reads data/live_runs.jsonl and shows reasons and timing per test."""

from tests.live_report import readable, report


def test_report_shows_reasons_and_timing() -> None:
    call = {"kind": "call", "task": "route", "attempt": 1, "ok": True, "reason": None,
            "latency_ms": 1500}  # fmt: skip
    rows = [
        {"run": "r1", "test": "test_a", **call},
        {"run": "r1", "test": "test_a", "kind": "outcome", "outcome": "passed"},
        {
            "run": "r2",
            "test": "test_a",
            **call,
            "ok": False,
            "reason": "timeout",
            "latency_ms": 10000,
        },
        {"run": "r2", "test": "test_a", "kind": "outcome", "outcome": "failed"},
    ]
    table = report(rows)
    assert "| test_a | ok | FAIL | 2 | timeout × 1 | 5750 / 10000 |" in table
    assert "сбоев: 1" in table


def test_test_ids_are_readable() -> None:
    assert readable("test_route[\\u0431\\u043e\\u043b\\u0438\\u0442]") == "test_route[болит]"
