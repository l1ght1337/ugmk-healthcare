"""Token usage per DeepSeek call, for the clinic's cost estimate.

Only counters are recorded — never prompt or answer text (personal data rule).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("app.llm.usage")


@dataclass(frozen=True)
class Usage:
    task: str  # route / extract / answer
    model: str
    attempt: int
    ok: bool
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit_tokens: int = 0  # DeepSeek context cache: billed at the cheaper rate
    cache_miss_tokens: int = 0
    latency_ms: int = 0
    error: str | None = None  # short detail: exception class, JSON position, field — no texts
    # Why the attempt failed: timeout / network / http_429 / api_error / invalid_json /
    # invalid_answer (JSON, but not the format of the task). None — success.
    reason: str | None = None
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))


UsageRecorder = Callable[[Usage], None]


def log_usage(usage: Usage) -> None:
    log.info(json.dumps(asdict(usage), ensure_ascii=False))


class JsonlUsageLog:
    """Append one JSON line per call (data/llm_usage.jsonl, not committed)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def __call__(self, usage: Usage) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(usage), ensure_ascii=False) + "\n")
