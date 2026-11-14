"""Shared fixtures: the real knowledge base and config, a fixed clinic clock, a mock schedule."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.bot.conversation import Conversation
from app.config import ClinicData, load_clinic_data
from app.db.session import Database
from app.flow.engine import AskPatient, AskQuestion, DialogState, Done, Engine
from app.flow.loader import KnowledgeBase, load_kb
from app.flow.schema import Audience
from app.schedule.generate import generate
from app.schedule.mock import MockSchedule
from tests import data_guard

TZ = ZoneInfo("Asia/Yekaterinburg")
FIRST_DAY = date(2026, 11, 12)  # Thursday
NOW = datetime(2026, 11, 12, 12, 0, tzinfo=TZ)


# --- data/ belongs to the running bot (tests/data_guard.py) -------------------------------------


def pytest_sessionstart(session: pytest.Session) -> None:
    """From here on a write into data/ is refused; importing this module does not turn it on,
    so `python -m tests.simulate` still saves data/simulation.jsonl."""
    data_guard.install()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """The check that the run left data/ alone goes last: it speaks for every test before it."""
    last = [item for item in items if item.name == "test_the_run_left_data_alone"]
    items[:] = [item for item in items if item not in last] + last


@pytest.fixture(scope="session", autouse=True)
def scratch_data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The journals and the database of the settings' defaults — in a temporary folder.

    ``Settings()`` built by a test without ``misses_log``, ``llm_usage_log`` or
    ``database_url`` would otherwise point at data/misses.jsonl, data/llm_usage.jsonl and
    data/bot.db of the running bot.
    """
    from app.config import get_settings

    folder = tmp_path_factory.mktemp("data")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("MISSES_LOG", str(folder / "misses.jsonl"))
        patch.setenv("LLM_USAGE_LOG", str(folder / "llm_usage.jsonl"))
        patch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{folder / 'bot.db'}")
        get_settings.cache_clear()
        yield folder
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def kb() -> KnowledgeBase:
    return load_kb()


@pytest.fixture(scope="session")
def clinic() -> ClinicData:
    return load_clinic_data()


@pytest.fixture
def engine(kb: KnowledgeBase, clinic: ClinicData) -> Engine:
    return Engine(kb, clinic)


@pytest.fixture
def schedule(clinic: ClinicData) -> MockSchedule:
    rows = generate(FIRST_DAY, clinic.schedule.demo.days_ahead, clinic.schedule)
    return MockSchedule(rows, tz=TZ, slot_minutes=clinic.schedule.demo.slot_minutes)


class Walk:
    """Result of a scripted dialog: the final step, the state and the questions asked."""

    def __init__(self, step: Done, state: DialogState, asked: list[str]) -> None:
        self.step = step
        self.state = state
        self.asked = asked


def walk(
    engine: Engine,
    scenario_id: str,
    answers: Mapping[str, object],
    *,
    audience: Audience | None = "adult",
    age_months: int | None = None,
    upfront: Mapping[str, object] | None = None,
) -> Walk:
    """Play a patient who answers each question with the matching ``answers`` fields.

    ``upfront`` are facts already taken from the complaint (before any question).
    """
    state = DialogState()
    asked: list[str] = []
    step = engine.start(state, scenario_id, upfront or {})
    for _ in range(30):
        if isinstance(step, Done):
            return Walk(step, state, asked)
        if isinstance(step, AskPatient):
            assert audience is not None, "the engine asked who the visit is for"
            nxt = engine.set_patient(state, audience, age_months)
            assert nxt is not None
            step = nxt
        else:
            assert isinstance(step, AskQuestion)
            asked.append(step.question.id)
            own = {f.name: answers[f.name] for f in step.question.fields if f.name in answers}
            step = engine.answer(state, own)
    raise AssertionError("dialog did not finish")


@pytest.fixture
def run(engine: Engine):
    def _run(scenario_id: str, answers: Mapping[str, object], **kwargs: object) -> Walk:
        return walk(engine, scenario_id, answers, **kwargs)  # type: ignore[arg-type]

    return _run


# --- a patient chatting with Conversation (no Telegram) ----------------------------------


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}")
    await database.init()
    yield database
    await database.close()


@pytest.fixture
def clock():
    from tests.chat_harness import Clock

    return Clock(NOW)


@pytest.fixture
def make_chat(kb: KnowledgeBase, clinic: ClinicData, db: Database, schedule: MockSchedule, clock):
    from tests.chat_harness import Chat, make_settings

    def _make(assistant=None, **overrides: object):
        conversation = Conversation(
            kb=kb,
            clinic=clinic,
            settings=make_settings(**overrides),
            db=db,
            schedule=schedule,
            assistant=assistant,
            clock=clock,
        )
        return Chat(conversation)

    return _make


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):  # type: ignore[no-untyped-def]
    """Live DeepSeek tests: the outcome of each goes to data/live_runs.jsonl (tests/live_report)."""
    outcome = yield
    report = outcome.get_result()
    if item.get_closest_marker("live") is None:
        return
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        from tests.live_report import record_outcome

        record_outcome(item.name, report.outcome, report.duration)
