"""The style of the bot's messages (15.11.2026): one bold line, marks, quotes, the greeting.

- A screen has one bold line: what the patient is to answer or to read first.
- An emoji is a label at the start of a line, of one set (texts.MARK_*).
- An example of what to type is italic; a service note is a quote.
- A line about 103 and 112 stands alone, bold, behind ⚠️.
- What came from the patient is shown as it was typed.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

from datetime import timedelta

from app.bot import texts
from app.bot.conversation import WELCOME, Reply
from tests.test_sim_findings_2 import MENU

GREETING = (
    "Добрый день! Травмпункт «УГМК-Здоровье», я виртуальный помощник. Чем я могу Вам помочь?\n\n"
    "📝 Запишу в травмпункт за минуту — 5 коротких шагов.\n"
    "💬 Отвечу на вопросы о травмпункте: адрес, цены, парковка, документы. Если ответа у меня "
    "нет — передам вопрос администратору.\n\n"
    "Напишите своими словами, например подвернул ногу или запишите к Ярунину на завтра, — или "
    "нажмите кнопку.\n\n"
    "Тестовый режим: записи демонстрационные."
)


def marked(reply: Reply) -> list[tuple[str, str]]:
    return [(s.kind, reply.text[s.start : s.end]) for s in reply.spans]


# --- the greeting ----------------------------------------------------------------------------


async def test_the_greeting(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    (hello,) = chat.replies
    assert hello.text == GREETING
    assert marked(hello) == [
        ("bold", "Добрый день!"),
        ("bold", "Запишу в травмпункт за минуту"),
        ("bold", "Отвечу на вопросы о травмпункте"),
        ("italic", "подвернул ногу"),
        ("italic", "запишите к Ярунину на завтра"),
        ("quote", texts.BOT_DEMO_NOTE),
    ]
    assert chat.labels() == MENU  # the examples are in the text now, not buttons
    assert len(hello.text) < 1024  # fits the caption of the animation


async def test_an_example_button_of_an_old_greeting_still_works(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.data("ex:ankle")
    assert "На что больше похоже?" in chat.text


async def test_the_animation_at_a_start_but_once_a_day(make_chat, clock) -> None:
    chat = make_chat()
    await chat.start()
    assert chat.replies[0].media == WELCOME
    await chat.start()  # the same day: the text alone
    assert chat.replies[0].media is None and chat.replies[0].text == GREETING
    clock.now += timedelta(hours=23, minutes=59)
    await chat.start()
    assert chat.replies[0].media is None
    clock.now += timedelta(minutes=2)  # a day has passed since the animation
    await chat.start()
    assert chat.replies[0].media == WELCOME


# --- other screens -----------------------------------------------------------------------------


async def test_the_question_of_a_step_is_the_bold_line(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("запишите меня к Ярунину на завтра")
    screen = chat.replies[-1]
    assert screen.text.split("\n")[:3] == [
        "Шаг 1 из 5 · Что случилось",  # the step is plain
        "📝 Запись для себя",
        "🩺 Пожелание: к Ярунину А. А., завтра, 13 ноября",  # the label of what is named first
    ]
    assert marked(screen) == [("bold", "С чем обращаетесь? Что произошло?")]
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    assert marked(chat.replies[-1]) == [("bold", "Как получена травма? Что произошло?")]
    assert "❓" not in chat.replies[-1].text  # that is the icon of «Задать вопрос»


async def test_the_day_and_the_doctor_above_the_times_have_their_labels(make_chat) -> None:
    from tests.test_wish_flow import through_dislocation

    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)
    await chat.pick_day("Завтра")
    await chat.pick_period()
    lines = chat.replies[-1].text.split("\n")
    assert "📅 13 ноября (пятница)" in lines
    assert any(line.startswith("🩺 Врач смены: Ярунин Андрей Александрович") for line in lines)
    assert marked(chat.replies[-1]) == [("bold", "Выберите, пожалуйста, время:")]


async def test_notes_are_quotes_and_examples_are_italic(make_chat) -> None:
    from tests.test_wish_flow import through_dislocation

    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)
    await chat.pick_slot("Завтра")
    assert ("quote", texts.CONSENT_WITHDRAW) in marked(chat.replies[-1])
    await chat.press(texts.CONSENT_YES)
    assert marked(chat.replies[-1]) == [("bold", texts.ASK_NAME), ("quote", texts.WHY_NAME)]
    await chat.say("ааа")
    assert ("italic", "Иванов Иван Иванович") in marked(chat.replies[0])
    await chat.say("петров иван сергеевич")
    assert marked(chat.replies[-1])[-1] == ("italic", "12.03.2015")
    await chat.say("12.03.1985")
    assert marked(chat.replies[-1]) == [("quote", texts.WHY_PHONE)]


async def test_questions_outside_the_steps_are_bold_too(make_chat) -> None:
    from tests.test_conversation import book_dislocation

    chat = make_chat()
    await book_dislocation(chat)
    assert marked(chat.replies[-1]) == [("bold", chat.replies[-1].text)]  # the payment
    await chat.press(texts.PAYMENT_SELF)
    await chat.data("m:my")
    assert marked(chat.replies[-1])[0] == ("bold", texts.MY_ONE)
    await chat.press(texts.CANCEL)
    assert marked(chat.replies[-1]) == [("bold", chat.replies[-1].text)]  # «Отменить запись…?»


# --- the marks are closed, the patient's text is the patient's ---------------------------------


def test_every_template_closes_its_marks() -> None:
    from app.bot import markup

    marked_templates = 0
    for name, value in vars(texts).items():
        if not name.isupper() or not isinstance(value, str):
            continue
        text, spans = markup.parse(value)
        assert "**" not in text, name  # a bold that does not close would stay as asterisks
        assert not any(line.startswith("> ") for line in text.split("\n")), name
        if "**" in value:
            marked_templates += 1
            assert any(s.kind == "bold" for s in spans), name
    assert marked_templates >= 2  # the greeting's two lines, at least


async def test_a_complaint_with_marks_in_it_is_shown_as_typed(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    said = "Маша 7 лет руку **ушибла** _сильно_, Ваня 10 лет ногу подвернул [фото](http://x.ru)"
    await chat.say(said)
    await chat.press("Машу, 7 лет")
    screen = chat.replies[-1]
    assert "📝 Записываю Машу, 7 лет: руку **ушибла** _сильно_" in screen.text.split("\n")
    assert [kind for kind, _ in marked(screen)] == ["bold"]  # the question only
    assert all("ушибла" not in piece for _, piece in marked(screen))


def test_a_message_quoted_back_is_escaped() -> None:
    """«Администратору передано: Ваше сообщение «…»»: the patient's words as they were."""
    from app.bot import markup

    said = "позовите **администратора** _срочно_ [тут](http://x.ru)"
    what = texts.PASSED_MESSAGE.format(text=markup.esc(said))
    text, spans = markup.render(texts.HANDOFF_NOTHING_NEW.format(what=what, when=""))
    assert said in text and spans == ()


async def test_the_line_about_the_ambulance_in_a_route_of_the_scripts(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рука или нога")
    await chat.press("Похоже на перелом")
    await chat.press(texts.AUDIENCE_SELF)
    await chat.press("Открытый перелом, смещение")
    route = next(r for r in chat.replies if "103" in r.text)
    lines = route.text.split("\n")
    call = (
        "Если кость видна в ране или кровотечение не останавливается, вызывайте скорую: "
        "103 или 112."
    )
    assert lines[0] == f"⚠️ {call}" and lines[1] == "С такой травмой Вам нужно обратиться:"
    assert lines[2].startswith("📍 ") and ("bold", call) in marked(route)
    links = [route.text[s.start : s.end] for s in route.spans if s.kind == "link"]
    assert links and all(link in lines[2 + n] for n, link in enumerate(links))
