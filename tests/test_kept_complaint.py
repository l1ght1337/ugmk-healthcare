"""One patient: what the complaint said is kept until the questions of the scenario end.

«Вчера упал, болит нога» → the patient picks the topic with a button → the bot asked «Когда
была получена травма?»: the facts of a complaint with no scenario were dropped. Now the text
of the complaint stays in the session (``Session.told``) while the booking goes on, and when
the scenario begins it is read as the parts of a named patient are: the time of the injury by
the code, the rest by DeepSeek, one call for a question. What was taken stands over step 3 —
«Ваши ответы: …» with «Изменить ответы»; what was not said is asked.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

from app.bot import texts
from app.flow.loader import KnowledgeBase
from tests.test_intents import session
from tests.test_two_kids_dialog import ANSWERED, UNCLEAR, ByTask, sent

SAID = "Вчера упал, болит нога"
WHEN, HOW = "Когда была получена травма?", "Как получена травма? Что произошло?"
SEEN = "Обращались ли к врачу по данному случаю?"


def labels_of(reply) -> list[str]:
    return [b.label for row in reply.buttons for b in row]


async def test_yesterday_is_read_when_the_topic_is_picked_with_a_button(make_chat) -> None:
    chat = make_chat()  # no DeepSeek: the word lists open the topics, the code reads «вчера»
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say(SAID)
    assert "На что больше похоже?" in chat.text
    assert (await session(chat))["told"] == [SAID]
    await chat.press("Похоже на вывих")
    await chat.press(texts.AUDIENCE_SELF)
    summary, question = chat.replies  # what was taken stands over step 3
    assert summary.text == "Ваши ответы: вывих; для себя; в течение 7 дней."
    assert labels_of(summary) == [texts.SUMMARY_EDIT]
    assert question.text.endswith(HOW)
    await chat.press("Бытовая травма")
    assert chat.text.endswith(SEEN) and WHEN not in chat.text  # «Когда?» is not asked
    assert len(chat.replies) == 1  # the summary is shown once, not over every question
    await chat.press("Нет, не обращались")
    assert chat.replies[0].text.startswith(
        "Ваши ответы: вывих; для себя; бытовая травма; в течение 7 дней; к врачу не обращались."
    )


async def test_the_rest_is_read_by_deepseek_one_call_for_a_question(
    make_chat, kb: KnowledgeBase
) -> None:
    said = "Вчера упал на лестнице, болит нога, к врачу не ходил"

    def own(asked: dict) -> dict:
        facts = {k: v for k, v in ANSWERED["facts"].items() if k in asked["fields"]}
        return {**ANSWERED, "facts": facts}

    fake = ByTask(route=UNCLEAR, extract=own)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say(said)
    await chat.press("Не знаю")
    await chat.press(texts.AUDIENCE_SELF)
    asked = [(s["question"], s["text"]) for s in map(sent, fake.requests) if s["task"] == "extract"]
    assert asked == [("how", said), ("seen_doctor", said)]  # «Когда?» — the code
    summary, days = chat.replies  # nothing is left to ask
    assert summary.text.startswith(
        "Ваши ответы: не знаю, на что похоже; для себя; бытовая травма; в течение 7 дней; "
        "к врачу не обращались."
    )
    assert "Выберите, пожалуйста, день приёма" in days.text


async def test_several_messages_of_the_complaint_are_kept(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say("Позавчера упал с велосипеда")
    await chat.say("болит нога, опухла")
    assert (await session(chat))["told"] == ["Позавчера упал с велосипеда", "болит нога, опухла"]
    if "Упал, ударился, подвернул" in chat.labels():
        await chat.press("Упал, ударился, подвернул")
    await chat.press("Похоже на перелом")
    await chat.press(texts.AUDIENCE_SELF)
    assert chat.replies[0].text == "Ваши ответы: перелом; для себя; в течение 7 дней."
    assert WHEN not in chat.text


async def test_change_the_answers_over_step_three_asks_them_all(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say(SAID)
    await chat.press("Похоже на вывих")
    await chat.press(texts.AUDIENCE_SELF)
    await chat.press(texts.SUMMARY_EDIT)
    assert chat.text.endswith(HOW)
    await chat.press("Бытовая травма")
    assert chat.text.endswith(WHEN)  # the patient answers himself now
    assert not any(r.text.startswith("Ваши ответы") for r in chat.replies)


async def test_a_word_on_the_threshold_is_asked(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say("Неделю назад упал, болит нога")  # seven days: the question decides
    await chat.press("Похоже на вывих")
    await chat.press(texts.AUDIENCE_SELF)
    assert chat.text.endswith(HOW) and len(chat.replies) == 1  # nothing was taken: no summary
    await chat.press("Бытовая травма")
    assert chat.text.endswith(WHEN)


async def test_the_complaint_is_not_kept_for_the_next_booking(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say(SAID)
    await chat.press(texts.MENU)  # «Меню»: the booking is left
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    await chat.press(texts.AUDIENCE_SELF)
    await chat.press("Бытовая травма")
    assert chat.text.endswith(WHEN)
