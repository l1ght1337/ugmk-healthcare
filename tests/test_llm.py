"""DeepSeek roles offline: validation, retry, time budget, token log, stable prompts."""

from __future__ import annotations

import time
from dataclasses import asdict

import pytest

from app.config import Settings
from app.flow.loader import KnowledgeBase
from app.llm import make_assistant
from app.llm.client import LLMUnavailable
from app.llm.reader import read_answer, read_complaint, read_question
from tests.fake_deepseek import Delay, FakeDeepSeek


async def test_route_keeps_only_valid_facts(kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek(
        {
            "intent": "complaint",
            "scenario": "dislocation",
            "facts": {"assault": False, "injury_age": "yesterday", "bogus": 1, "seen_doctor": "no"},
        }
    )
    result = await fake.assistant(kb).route("вчера вывихнул плечо дома, к врачу не ходил")
    assert result.scenario == "dislocation"
    assert result.facts == {"assault": False, "seen_doctor": "no"}


async def test_route_other_intents_have_no_scenario(kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek({"intent": "question", "scenario": "dislocation", "facts": {"x": 1}})
    result = await fake.assistant(kb).route("где вы находитесь?")
    assert (result.intent, result.scenario, result.facts) == ("question", None, {})


async def test_invalid_answer_is_retried_once_with_the_same_prefix(kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek("не json", {"intent": "complaint", "scenario": "burns", "facts": {}})
    result = await fake.assistant(kb).route("обожглась")
    assert result.scenario == "burns"
    first, second = (r["messages"] for r in fake.requests)
    assert second[: len(first)] == first  # cached prefix unchanged
    assert [m["role"] for m in second[len(first) :]] == ["assistant", "user"]
    assert [u.attempt for u in fake.usage] == [1, 2]


async def test_unknown_scenario_twice_is_unavailable(kb: KnowledgeBase) -> None:
    bad = {"intent": "complaint", "scenario": "knee_mri", "facts": {}}
    fake = FakeDeepSeek(bad, bad)
    with pytest.raises(LLMUnavailable):
        await fake.assistant(kb).route("МРТ колена")
    reading = await read_complaint(FakeDeepSeek(bad, bad).assistant(kb), "МРТ колена")
    assert reading.unavailable


async def test_slow_deepseek_falls_back_to_buttons(kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek(Delay(5))
    sc = kb.scenario("dislocation")
    started = time.monotonic()
    reading = await read_answer(fake.assistant(kb, timeout=0.2), sc, sc.question("when"), "вчера")
    assert reading.unavailable
    assert time.monotonic() - started < 1
    assert fake.usage[-1].error == "timeout"


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_api_error_falls_back_to_buttons(kb: KnowledgeBase, status: int) -> None:
    fake = FakeDeepSeek(status)
    sc = kb.scenario("burns")
    reading = await read_answer(fake.assistant(kb), sc, sc.question("burn_cause"), "кипяток")
    assert reading.unavailable
    assert not fake.usage[-1].ok


async def test_no_api_key_means_buttons_only(kb: KnowledgeBase) -> None:
    assert make_assistant(Settings(_env_file=None, deepseek_api_key=None), kb) is None
    sc = kb.scenario("burns")
    assert (await read_answer(None, sc, sc.question("burn_cause"), "кипяток")).unavailable
    assert (await read_complaint(None, "обжёгся")).unavailable
    assert (await read_question(None, "где парковка?")).unavailable


async def test_extract_clarify_is_guarded(kb: KnowledgeBase) -> None:
    sc = kb.scenario("dislocation")
    q = sc.question("when")
    unsafe = {"facts": {}, "unclear": True, "clarify": "Это было 3 дня назад?"}
    safe = {"facts": {}, "unclear": True, "clarify": "Травма была на этой неделе или раньше?"}
    fake = FakeDeepSeek(unsafe, safe)
    assistant = fake.assistant(kb)
    assert (await assistant.extract(sc, q, "не помню")).clarify is None
    assert (await assistant.extract(sc, q, "не помню")).clarify == safe["clarify"]


async def test_extract_without_own_field_is_unclear(kb: KnowledgeBase) -> None:
    sc = kb.scenario("dislocation")
    fake = FakeDeepSeek({"facts": {"seen_doctor": "no"}, "unclear": False})
    result = await fake.assistant(kb).extract(sc, sc.question("when"), "к врачу не ходил")
    assert result.facts == {"seen_doctor": "no"}  # bonus answer kept, «Когда?» asked again


@pytest.mark.parametrize(
    ("answer", "shown"),
    [
        ("Адрес травмпункта: ул. Шейнкмана, 113/2, вход через детское отделение.", True),
        ("Приём стоит 987 рублей.", False),  # a number that is not in kb/info
        ("Позвоните по номеру [ТЕЛЕФОН].", False),
    ],
)
async def test_answers_are_grounded_in_kb_info(kb: KnowledgeBase, answer: str, shown: bool) -> None:
    fake = FakeDeepSeek({"found": True, "answer": answer})
    result = await fake.assistant(kb).answer("вопрос")
    assert (result.answer == answer) is shown
    assert result.offer_admin is not shown


async def test_unknown_question_offers_admin(kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek({"found": False, "answer": None})
    reading = await read_question(fake.assistant(kb), "Делаете МРТ колена?")
    assert reading.answer is None and reading.offer_admin and not reading.unavailable


async def test_usage_is_counted_per_call_without_texts(kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek({"intent": "other"})
    await fake.assistant(kb).route("Кузнецову позвонить можно?")
    (usage,) = fake.usage
    assert (usage.task, usage.prompt_tokens, usage.cache_hit_tokens, usage.cache_miss_tokens) == (
        "route", 5000, 4864, 136,
    )  # fmt: skip
    assert "Кузнецов" not in str(asdict(usage))


async def test_system_prompt_is_identical_across_calls(kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek(
        {"intent": "other"}, {"facts": {}, "unclear": True}, {"facts": {}, "unclear": True}
    )
    assistant = fake.assistant(kb)
    burns, dislocation = kb.scenario("burns"), kb.scenario("dislocation")
    await assistant.route("маркер-альфа")
    await assistant.extract(burns, burns.question("burn_location"), "маркер-бета")
    await assistant.extract(dislocation, dislocation.question("how"), "маркер-гамма")
    systems = {r["messages"][0]["content"] for r in fake.requests}
    assert len(systems) == 1 and fake.requests[0]["messages"][0]["role"] == "system"
    for text, request in zip(("альфа", "бета", "гамма"), fake.requests, strict=True):
        assert text not in request["messages"][0]["content"]
        assert text in request["messages"][-1]["content"]
    assert fake.requests[0]["temperature"] == 0
    assert fake.requests[0]["response_format"] == {"type": "json_object"}
    assert FakeDeepSeek().assistant(kb).dialog_system == assistant.dialog_system


def test_prompts_carry_the_catalogue_but_no_operator_notes(kb: KnowledgeBase) -> None:
    assistant = FakeDeepSeek().assistant(kb)
    for sc in kb.scenarios.values():
        assert f"## {sc.id} — {sc.number}" in assistant.dialog_system
        for note in sc.operator_notes:
            assert note not in assistant.dialog_system and note not in assistant.answer_system
        for name, spec in sc.fields().items():
            assert f"поле {name}" in assistant.dialog_system
            for value in spec.values():
                if isinstance(value, str):
                    assert value in assistant.dialog_system
    assert "json" in assistant.dialog_system and "json" in assistant.answer_system
    assert "Шейнкмана, 113/2" in assistant.answer_system


async def test_warm_up_touches_both_system_prompts(kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek({"intent": "other"}, {"found": False})
    assistant = fake.assistant(kb)
    assert await assistant.warm_up()
    systems = {r["messages"][0]["content"] for r in fake.requests}
    assert systems == {assistant.dialog_system, assistant.answer_system}
    assert {u.task for u in fake.usage} == {"warmup"}
    assert not await FakeDeepSeek(500, 500).assistant(kb).warm_up()  # no exception


async def test_event_dates_reach_deepseek_as_relative_time(kb: KnowledgeBase) -> None:
    from datetime import date

    fake = FakeDeepSeek({"facts": {"injury_age": "within_7_days"}})
    assistant = fake.assistant(kb)
    assistant.today = lambda: date(2026, 11, 12)
    sc = kb.scenario("dislocation")
    await assistant.extract(sc, sc.question("when"), "упал 10 ноября")
    assert "упал 2 дня назад" in fake.requests[0]["messages"][-1]["content"]
