"""A test run leaves data/ alone: the files there belong to the running bot (tests/data_guard.py).

Three lines of defence. The default paths of the settings point to a temporary folder for
the whole run; a write into data/ is refused on the spot; and the last test of the run fails
if any test tried.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.config import PROJECT_ROOT, Settings
from tests import data_guard as guard

DATA = Path(guard.DATA)
APPEND = os.O_WRONLY | os.O_APPEND | os.O_CREAT


def test_data_is_the_folder_of_the_project() -> None:
    assert (PROJECT_ROOT / "data").resolve() == DATA


@pytest.mark.parametrize(
    ("event", "args"),
    [
        ("open", (str(DATA / "misses.jsonl"), "a", APPEND)),
        ("open", (DATA / "llm_usage.jsonl", "a", APPEND)),  # a Path, as Path.open gives it
        ("open", (os.fsencode(DATA / "schedule.csv"), "w", os.O_WRONLY | os.O_TRUNC)),
        ("open", ("data/media_ids.json", None, os.O_RDWR)),  # relative to the project root
        ("sqlite3.connect", (str(DATA / "bot.db"),)),
        ("sqlite3.connect", (f"file:{DATA / 'bot.db'}?mode=rw",)),
        ("os.remove", (str(DATA / "misses.jsonl"), -1)),
        ("os.rename", ("/tmp/new.jsonl", str(DATA / "misses.jsonl"), -1, -1)),
        ("os.rename", (str(DATA / "bot.db"), "/tmp/bot.db", -1, -1)),
        ("os.mkdir", (str(DATA / "new-folder"), 0o777, -1)),
        ("os.truncate", (str(DATA / "misses.jsonl"), 0)),
        ("os.utime", (str(DATA / "bot.db"), None, None, -1)),
    ],
)
def test_what_counts_as_a_change_in_data(event: str, args: tuple, monkeypatch) -> None:
    monkeypatch.chdir(PROJECT_ROOT)
    found = guard.changed(event, args)
    assert found is not None and Path(found).parent == DATA, (event, args)


@pytest.mark.parametrize(
    ("event", "args"),
    [
        ("open", (str(DATA / "bot.db"), "rb", os.O_RDONLY)),  # reading
        ("open", (str(PROJECT_ROOT / "tests" / "data" / "x.yaml"), "w", APPEND)),  # tests/data
        ("open", (str(PROJECT_ROOT / "data-old" / "x.jsonl"), "a", APPEND)),  # a look-alike name
        ("open", (7, "w", APPEND)),  # a descriptor
        ("open", (str(DATA / "live_runs.jsonl"), "a", APPEND)),  # the live tests' own journal
        ("sqlite3.connect", (":memory:",)),
        ("sqlite3.connect", ("/tmp/pytest/bot.db",)),
        ("os.mkdir", (str(DATA), 0o777, -1)),  # mkdir(exist_ok=True) on the folder itself
        ("os.listdir", (str(DATA),)),
        ("import", ("json", None, None, None, None)),
    ],
)
def test_what_does_not_count(event: str, args: tuple) -> None:
    assert guard.changed(event, args) is None, (event, args)


def test_a_write_into_data_is_refused_on_the_spot() -> None:
    """The hook is on for the run. Without it the call below fails as «no such file», and
    nothing appears in data/ either way: there is no O_CREAT."""
    before = len(guard.SEEN)
    with pytest.raises(pytest.fail.Exception, match="must not change data/no-such-file"):
        os.open(DATA / "no-such-file", os.O_WRONLY)
    (seen,) = guard.SEEN[before:]
    del guard.SEEN[before:]  # the attempt was this test's own
    assert (seen.event, seen.path) == ("open", os.path.join("data", "no-such-file"))
    assert "test_a_write_into_data_is_refused_on_the_spot" in seen.test


def test_reading_data_is_not_refused() -> None:
    assert (DATA / ".gitkeep").read_bytes() == b""


def test_default_paths_lead_to_a_temporary_folder(scratch_data: Path) -> None:
    """Settings built without a path — as a test that forgot one would build them."""
    settings = Settings(_env_file=None)
    assert settings.misses_log == scratch_data / "misses.jsonl"
    assert settings.llm_usage_log == scratch_data / "llm_usage.jsonl"
    assert settings.database_url == f"sqlite+aiosqlite:///{scratch_data / 'bot.db'}"
    assert DATA not in scratch_data.resolve().parents and scratch_data.resolve() != DATA


def test_the_run_left_data_alone() -> None:
    """Always the last test of a run (``pytest_collection_modifyitems`` in tests/conftest.py)."""
    assert not guard.SEEN, "tests tried to change the bot's files in data/:\n" + guard.report()
