"""Live DeepSeek runs side by side: which test failed, why, how long the calls took.

    uv run pytest -m live -q            # each run appends to data/live_runs.jsonl
    uv run python -m tests.live_report  # the last three runs as a table

The log has counters only (task, attempt, reason, latency, tokens) — no prompts or answers.
"""

from __future__ import annotations

import argparse
import codecs
import json
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

LIVE_LOG = Path(__file__).resolve().parent.parent / "data" / "live_runs.jsonl"
RUN_ID = datetime.now(UTC).isoformat(timespec="seconds")


def record_outcome(test: str, outcome: str, seconds: float) -> None:
    """Called from tests/conftest.py after each live test."""
    LIVE_LOG.parent.mkdir(parents=True, exist_ok=True)
    row = {"run": RUN_ID, "test": test, "kind": "outcome", "outcome": outcome,
           "seconds": round(seconds, 2)}  # fmt: skip
    with LIVE_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def readable(test: str, width: int = 70) -> str:
    """pytest escapes Cyrillic in ids («\\u043f…»); show the phrase itself."""
    name = codecs.decode(test, "unicode_escape") if "\\u" in test else test
    return name if len(name) <= width else name[: width - 1] + "…"


def load(path: Path = LIVE_LOG) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def report(rows: list[dict], runs: int = 3) -> str:
    order = list(dict.fromkeys(r["run"] for r in rows))[-runs:]
    rows = [r for r in rows if r["run"] in order]
    outcome: dict[tuple[str, str], str] = {}
    calls: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["kind"] == "outcome":
            outcome[(r["test"], r["run"])] = r["outcome"]
        else:
            calls[r["test"]].append(r)
    tests = list(dict.fromkeys(r["test"] for r in rows))
    head = " | ".join(f"прогон {i + 1}" for i in range(len(order)))
    lines = [
        f"| Тест | {head} | Вызовов | Сбои (причина × число) | Время, мс: медиана / макс |",
        "|---|" + "---|" * len(order) + "---|---|---|",
    ]
    for test in tests:
        marks = []
        for run in order:
            result = outcome.get((test, run), "—")
            marks.append({"passed": "ok", "failed": "FAIL", "skipped": "skip"}.get(result, result))
        own = calls.get(test, [])
        reasons: dict[str, int] = defaultdict(int)
        for c in own:
            if not c["ok"]:
                reasons[c.get("reason") or c.get("error") or "?"] += 1
        fails = ", ".join(f"{k} × {v}" for k, v in sorted(reasons.items())) or "—"
        times = [c["latency_ms"] for c in own]
        timing = f"{int(statistics.median(times))} / {max(times)}" if times else "—"
        row = f"| {readable(test)} | {' | '.join(marks)} | {len(own)} | {fails} | {timing} |"
        lines.append(row)
    every = [c for t in calls.values() for c in t]
    slow = sorted(c["latency_ms"] for c in every)
    if slow:
        p95 = slow[min(len(slow) - 1, int(len(slow) * 0.95))]
        lines.append("")
        lines.append(
            f"Всего вызовов: {len(every)}, сбоев: {sum(not c['ok'] for c in every)}; "
            f"время: медиана {int(statistics.median(slow))} мс, 95 % — {p95} мс, "
            f"максимум {slow[-1]} мс."
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--runs", type=int, default=3)
    print(report(load(), parser.parse_args().runs))


if __name__ == "__main__":
    main()
