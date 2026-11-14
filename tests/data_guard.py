"""The files in data/ belong to the running bot: a test run must leave them alone.

``install()`` (tests/conftest.py, at the start of a pytest session) puts a hook on Python's
audit events. Opening a file in data/ for writing, connecting SQLite to one, removing,
renaming or creating one is refused on the spot — the test that tried fails and names the
path — and is remembered in ``SEEN``. ``test_the_run_left_data_alone`` (tests/test_data_guard.py,
always the last test of a run) fails if anything was remembered, so a refusal swallowed by
the code under test is not lost either.

Why not «compare data/ before and after the run»: the bot works from the same folder and
writes data/bot.db and data/llm_usage.jsonl while the tests go, so a comparison would blame
the tests for the bot's lines. The hook sees only what this process does, and it stops the
write instead of reporting it afterwards.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import pytest

from app.config import PROJECT_ROOT

DATA = os.path.realpath(PROJECT_ROOT / "data")
# The live tests' own journal (tests/live_report.py): only `uv run pytest -m live` writes it,
# and the bot never reads it.
ALLOWED = frozenset({os.path.join(DATA, "live_runs.jsonl")})
WRITING = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
# An audit event that changes files → where its paths are among the arguments. «open» is
# apart: it changes a file only with the flags of ``WRITING``.
PATHS: dict[str, tuple[int, ...]] = {
    "sqlite3.connect": (0,),
    "os.remove": (0,),
    "os.rename": (0, 1),
    "os.mkdir": (0,),
    "os.rmdir": (0,),
    "os.truncate": (0,),
    "os.utime": (0,),
    "os.chmod": (0,),
}


@dataclass(frozen=True)
class Seen:
    test: str  # «tests/test_x.py::test_y (call)», as pytest names the running test
    event: str
    path: str  # from the project root: «data/misses.jsonl»


SEEN: list[Seen] = []
_installed = False


def _real(raw: object) -> str | None:
    """The absolute path of an argument of an audit event; None for a descriptor or a URL."""
    if isinstance(raw, int):
        return None
    try:
        text = os.fsdecode(raw)  # type: ignore[arg-type]
    except TypeError:
        return None
    if text.startswith("file:"):  # an SQLite URI: «file:data/bot.db?mode=rw»
        text = text.removeprefix("file:").split("?", 1)[0]
    return os.path.realpath(text)


def changed(event: str, args: tuple[object, ...]) -> str | None:
    """The path inside data/ that this audit event is about to change, or None."""
    if event == "open":
        flags = args[2] if len(args) > 2 else None
        if not isinstance(flags, int) or not flags & WRITING:
            return None  # reading is fine
        raws = args[:1]
    elif event in PATHS:
        raws = tuple(args[i] for i in PATHS[event] if i < len(args))
    else:
        return None
    for raw in raws:
        path = _real(raw)
        if path is None or path in ALLOWED:
            continue
        if path != DATA and not path.startswith(DATA + os.sep):
            continue
        if event == "os.mkdir" and os.path.isdir(path):
            continue  # mkdir(exist_ok=True) on a folder that is there changes nothing
        return path
    return None


def _hook(event: str, args: tuple[object, ...]) -> None:
    if event != "open" and event not in PATHS:  # nearly every event: out at once
        return
    path = changed(event, args)
    if path is None:
        return
    seen = Seen(
        os.environ.get("PYTEST_CURRENT_TEST", "outside a test"),
        event,
        os.path.relpath(path, PROJECT_ROOT),
    )
    SEEN.append(seen)
    pytest.fail(
        f"a test must not change {seen.path} ({event}): the file belongs to the running bot. "
        "Give the code a path in tmp_path instead."
    )


def install() -> None:
    """Once for the process: an audit hook cannot be taken off."""
    global _installed
    if not _installed:
        sys.addaudithook(_hook)
        _installed = True


def report() -> str:
    return "\n".join(f"  {s.test}: {s.event} → {s.path}" for s in SEEN)
