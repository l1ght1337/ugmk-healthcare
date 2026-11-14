"""What patients ask the bot to do, in their own words: one source for docs/intent_matrix.md
and tests/test_intents.py.

    uv run python -m tests.intent_cases --write    # regenerate docs/intent_matrix.md

Every phrase is typed on every step where a patient may type it (``at``). The rule the
table checks: a request for an action never gets «У меня нет этой информации» or «Я помогаю
только с…», no part of the message is lost, and every answer has a button that leads on.

``was`` — what the bot did before the fix of 13.11.2026, taken from a run of the same
phrases on the old code with the kinds the real DeepSeek gave (empty: it was right).
``llm`` — what DeepSeek answers when the code passes the text on (tests only).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

DOC = Path(__file__).resolve().parent.parent / "docs" / "intent_matrix.md"

# --- where the patient is when the phrase is typed -----------------------------------------
MENU = "главное меню"
COMPLAINT = "«Что случилось?»"
AUDIENCE = "«Для кого запись?»"
AGE = "возраст ребёнка"
QUESTION = "вопрос сценария"
YES_NO = "вопрос «да / нет»"
DAYS = "выбор дня"
PERIODS = "выбор времени суток"
TIMES = "выбор времени"
CONSENT = "согласие"
NAME = "ФИО"
BIRTH = "дата рождения"
PHONE = "телефон"
PAYMENT = "вопрос об оплате"
ASK = "«Задать вопрос»"
BOOKED = "есть запись"
REMINDED = "после напоминания"

WIZARD = (COMPLAINT, AUDIENCE, AGE, QUESTION, DAYS, PERIODS, TIMES, CONSENT, NAME, BIRTH, PHONE)
ANYWHERE = (MENU, *WIZARD, PAYMENT, ASK)

# --- what the bot must do (the checks are in tests/test_intents.py) ------------------------
BOOK = "Начинает запись: «С чем обращаетесь?»"
BOOK_WISH = "Начинает запись и помнит пожелание: строка «Пожелание: …»"
BOOK_CHILD = "Начинает запись для ребёнка: «Для кого запись?» не спрашивает, только возраст"
BOOK_ADULT = "Начинает запись для взрослого: «Для кого запись?» не спрашивает"
GO_ON = "Запись уже идёт: показывает тот же шаг, ничего не сбрасывает"
TOPICS_CHILD = "Темы по жалобе; запись для ребёнка — «Для кого запись?» не спрашивает"
MY = "«Мои записи»: запись с кнопками «Перенести» и «Отменить» (или «записей нет» и меню)"
CANCEL_ASKS = (
    "Запись одна — сразу «Отменить запись на … в …?» с кнопками «Да, отменить» и «Нет, "
    "оставить»; записей нет — «У Вас нет предстоящих записей» и меню"
)
MY_MOVE = (
    "Перенос сразу: «Переношу запись с …» и выбор времени (назван день — сразу он), "
    "внизу «Оставить как есть»"
)
CONFIRM = "Подтверждает ближайшую запись: «Спасибо, ждём Вас …»"
CONFIRM_NONE = "Записей нет: «У Вас нет предстоящих записей» и меню"
ANSWER = "Отвечает по справке; под ответом «Помогло», «Позвать администратора», меню"
ASIDE = "Отвечает на вопрос и снова показывает тот же шаг записи"
ADMIN = "Передаёт администратору, как кнопка «Позвать администратора»"
FEEDBACK_ASK = "Просит написать, что случилось, и передаёт администратору"
FEEDBACK = "Предлагает передать сообщение администратору"
THANKS = "«Пожалуйста!» и меню"
GREETING = "Здоровается и показывает меню"
STAY = "Показывает тот же шаг записи"
ACK = "«Могу я ещё чем-нибудь Вам помочь?» и меню"
BYE = "Прощается фразой скрипта; меню остаётся"
FORGET = "Спрашивает подтверждение удаления данных"
BACK = "Возвращает на предыдущий шаг (с первого шага — в меню)"
HOME = "Главное меню"
CANCEL = "Останавливает запись: «Хорошо, остановились: запись не оформлена» и меню"
NEXT = "Принимает ответ и идёт дальше, без DeepSeek"
SAME = "Просит выбрать кнопкой и показывает кнопки этого шага"
UNCLEAR = "Переспрашивает вопрос с кнопками, без DeepSeek"
TOPICS = "Показывает список тем кнопками"
NEAREST = "Берёт ближайшее свободное время и идёт дальше"
TO_DAYS = "Показывает выбор дня"
TO_PERIODS = "Показывает время суток этого дня"
TWO_BOOK = "Отвечает на вопрос и начинает запись"
TWO_COMPLAINT = "Отвечает на вопрос и ведёт по сценарию жалобы"
TWO_ADVICE = (
    "Одним сообщением говорит, что с этой травмой делают в травмпункте (если это есть в "
    "скрипте) и что рекомендации даст врач, затем ведёт по сценарию жалобы"
)

NO_LLM = frozenset(
    {BOOK, BOOK_WISH, BOOK_CHILD, BOOK_ADULT, GO_ON, MY, MY_MOVE, CANCEL_ASKS, CONFIRM,
     CONFIRM_NONE, ADMIN,
     FEEDBACK_ASK, THANKS, GREETING, STAY, ACK, BYE, FORGET, BACK, HOME, CANCEL, NEXT, SAME,
     UNCLEAR, TOPICS, NEAREST, TO_DAYS, TO_PERIODS}
)  # fmt: skip


@dataclass(frozen=True)
class Intent:
    group: str
    phrase: str
    at: tuple[tuple[str, str], ...]  # (where it is typed, what the bot must do)
    was: str = ""
    llm: tuple[dict, ...] = ()
    live: str | None = None  # the route kind the real DeepSeek must give (live test)


def everywhere(steps: tuple[str, ...], must: str) -> tuple[tuple[str, str], ...]:
    return tuple((step, must) for step in steps)


def route(kind: str, **more: object) -> dict:
    return {"type": kind, **more}


def answer(text: str) -> dict:
    return {"found": True, "answer": text}


Q = route("clinic_question")
PRICE = answer("Первичный приём травматолога-ортопеда — 2000 рублей (демо-цена).")
ADDRESS = answer("Адрес: ул. Шейнкмана, 113/2. Вход через детское отделение.")
PARKING = answer("С 8:00 до 20:00 работает подземный паркинг.")
DOCUMENTS = answer("Взрослый пациент: паспорт. При первом обращении — ещё СНИЛС.")
ANSWERS = tuple(a["answer"] for a in (PRICE, ADDRESS, PARKING, DOCUMENTS))

BY_LLM = "Работало, но только через DeepSeek; теперь — кодом, без него"
OFFTOPIC = "«Я помогаю только с записью в травмпункт и вопросами о нём»"
PICK = "«Выберите, пожалуйста, вариант кнопкой» — слово не понималось"

G_BOOK = "Записаться"
G_OTHER = "Записать ребёнка или другого взрослого"
G_MY = "Свои записи"
G_MOVE = "Перенести"
G_CANCEL = "Отменить"
G_CONFIRM = "Подтвердить"
G_PRICE = "Цена"
G_ADDRESS = "Адрес и парковка"
G_DOCS = "Документы"
G_HUMAN = "Позвать человека"
G_COMPLAIN = "Пожаловаться"
G_THANKS = "Поблагодарить, поздороваться, попрощаться"
G_FORGET = "Удалить данные"
G_TWO = "Сообщения из двух частей"
G_SHORT = "Короткие ответы посреди записи"

INTENTS: tuple[Intent, ...] = (
    # --- Записаться ---------------------------------------------------------------------------
    Intent(G_BOOK, "запишите меня",
           (*everywhere((MENU, COMPLAINT, ASK), BOOK), (QUESTION, GO_ON), (DAYS, GO_ON)),
           was="Посреди записи начинал её заново с первого шага"),
    Intent(G_BOOK, "хочу к врачу", everywhere((MENU, COMPLAINT, ASK), BOOK), was=BY_LLM),
    Intent(G_BOOK, "мне бы к травматологу попасть", ((MENU, BOOK),), was=BY_LLM),
    Intent(G_BOOK, "можно к вам на приём?", ((MENU, BOOK),), was=BY_LLM),
    Intent(G_BOOK, "запишыте пожалуста", ((MENU, BOOK),), was=BY_LLM),
    Intent(G_BOOK, "нужна запись на сегодня", ((MENU, BOOK_WISH),)),
    Intent(G_BOOK, "запись", ((MENU, BOOK),)),
    Intent(G_BOOK, "записаться можно?", ((MENU, BOOK),), was=BY_LLM),
    Intent(G_BOOK, "здравствуйте, запишите меня", ((MENU, BOOK),), was=BY_LLM),
    # --- Записать ребёнка или другого взрослого -------------------------------------------------
    Intent(G_OTHER, "запишите ребёнка", everywhere((MENU, COMPLAINT), BOOK_CHILD),
           was="Начинал запись, но потом спрашивал «Для кого запись?»: слово «ребёнка» терялось"),
    Intent(G_OTHER, "хочу записать сына к травматологу", ((MENU, BOOK_CHILD),),
           was="То же: «сына» терялось"),
    Intent(G_OTHER, "можно записать маму?", ((MENU, BOOK_ADULT),), was="То же: «маму» терялось"),
    Intent(G_OTHER, "запишите дочку на завтра", ((MENU, BOOK_CHILD),),
           was="Пожелание «завтра» запоминал, «дочку» терял"),
    Intent(G_OTHER, "дочка упала с качелей, болит рука",
           everywhere((MENU, COMPLAINT), TOPICS_CHILD),
           was="Темы показывал, но потом спрашивал «Для кого запись?»",
           llm=(route("complaint", area="limb", scenario=None,
                      candidates=["fracture", "dislocation", "ligament_rupture"]),),
           live="complaint"),
    # --- Свои записи --------------------------------------------------------------------------
    Intent(G_MY, "когда у меня приём", everywhere((MENU, BOOKED, QUESTION), MY)),
    Intent(G_MY, "на когда я записан?", ((BOOKED, MY),)),
    Intent(G_MY, "во сколько мне приходить?", ((BOOKED, MY),),
           was="Отвечал общим текстом из справки, запись не показывал"),
    Intent(G_MY, "мои записи", everywhere((MENU, BOOKED), MY)),
    Intent(G_MY, "я записан?", ((BOOKED, MY),), was=BY_LLM),
    Intent(G_MY, "к какому врачу я записан", ((BOOKED, MY),), was=BY_LLM),
    # --- Перенести ----------------------------------------------------------------------------
    Intent(G_MOVE, "перенеси запись на завтра", ((BOOKED, MY_MOVE),),
           was="Показывал «Мои записи»; день открывался только после кнопки «Перенести»"),
    Intent(G_MOVE, "перенеси на завтра", ((BOOKED, MY_MOVE),)),
    Intent(G_MOVE, "эту запись отмени и давай на воскресенье запиши меня когда можно",
           ((BOOKED, MY_MOVE),),
           was="Показывал «Мои записи» со строкой о пожелании между заголовком и записью"),
    Intent(G_MOVE, "хочу перенести", ((BOOKED, MY_MOVE),), was=BY_LLM),
    Intent(G_MOVE, "перезапишите меня на пятницу", ((BOOKED, MY_MOVE),), was=BY_LLM),
    Intent(G_MOVE, "можно прийти в другой день?", ((BOOKED, MY),), was=BY_LLM),
    # --- Отменить -----------------------------------------------------------------------------
    Intent(G_CANCEL, "отмени запись", everywhere((BOOKED, MENU), CANCEL_ASKS),
           was="Показывал «Мои записи» с кнопками «Перенести» и «Отменить»"),
    Intent(G_CANCEL, "отмени все", everywhere((BOOKED, MENU), CANCEL_ASKS),
           was="Не понимал: второй раз показывал тот же список записей"),
    Intent(G_CANCEL, "отмени запись на завтра", ((BOOKED, CANCEL_ASKS),),
           was="Показывал весь список, не выбирая запись"),
    Intent(G_CANCEL, "я не приду", everywhere((BOOKED, REMINDED), MY), was=OFFTOPIC),
    Intent(G_CANCEL, "не смогу прийти завтра", ((BOOKED, MY),), was=BY_LLM),
    Intent(G_CANCEL, "отмена",
           (*everywhere(WIZARD, CANCEL), (MENU, HOME), (ASK, HOME), (BOOKED, CANCEL_ASKS),
            (PAYMENT, CANCEL_ASKS)),
           was="В меню и на первом шаге открывал «Мои записи»; на шагах с кнопками — " + PICK
               + "; на шагах «возраст», «ФИО», «дата рождения» переспрашивал без единой кнопки"),
    # --- Подтвердить --------------------------------------------------------------------------
    Intent(G_CONFIRM, "подтверждаю",
           ((REMINDED, CONFIRM), (BOOKED, CONFIRM), (MENU, CONFIRM_NONE)), was=OFFTOPIC),
    Intent(G_CONFIRM, "да, приду", ((REMINDED, CONFIRM),), was="Начинал новую запись"),
    Intent(G_CONFIRM, "буду", ((REMINDED, CONFIRM),), was=OFFTOPIC),
    # --- Цена ---------------------------------------------------------------------------------
    Intent(G_PRICE, "сколько стоит приём?",
           ((MENU, ANSWER), (ASK, ANSWER), (QUESTION, ASIDE), (DAYS, ASIDE)),
           was="Посреди записи вопрос пропадал: «Уточните, пожалуйста…» или «Выберите день "
               "кнопкой»",
           llm=(Q, PRICE), live="clinic_question"),
    Intent(G_PRICE, "почём приём", ((MENU, ANSWER),), llm=(Q, PRICE)),
    Intent(G_PRICE, "цена", everywhere((MENU, ASK), ANSWER), llm=(Q, PRICE)),
    # --- Адрес и парковка ---------------------------------------------------------------------
    Intent(G_ADDRESS, "какой адрес?", ((MENU, ANSWER), (ASK, ANSWER), (DAYS, ASIDE)),
           was="На шаге выбора дня вопрос пропадал: «Выберите, пожалуйста, день кнопкой»",
           llm=(Q, ADDRESS)),
    Intent(G_ADDRESS, "где вы находитесь", ((MENU, ANSWER),), llm=(Q, ADDRESS)),
    Intent(G_ADDRESS, "как добраться", ((MENU, ANSWER),), llm=(Q, ADDRESS)),
    Intent(G_ADDRESS, "где парковаться?", everywhere((MENU, BOOKED), ANSWER), llm=(Q, PARKING)),
    Intent(G_ADDRESS, "парковка есть?", ((MENU, ANSWER),), llm=(Q, PARKING)),
    # --- Документы ----------------------------------------------------------------------------
    Intent(G_DOCS, "что взять с собой?", ((MENU, ANSWER), (BOOKED, ANSWER), (PHONE, ASIDE)),
           was="На шаге «телефон» вопрос пропадал: бот снова просил поделиться контактом",
           llm=(Q, DOCUMENTS)),
    Intent(G_DOCS, "какие документы нужны", ((MENU, ANSWER),), llm=(Q, DOCUMENTS)),
    # --- Позвать человека ---------------------------------------------------------------------
    Intent(G_HUMAN, "позови администратора", everywhere((MENU, QUESTION, DAYS, NAME), ADMIN),
           was="На шаге «ФИО» принимал фразу за имя пациента и спрашивал дату рождения"),
    Intent(G_HUMAN, "хочу поговорить с человеком", ((MENU, ADMIN),), was=BY_LLM),
    Intent(G_HUMAN, "оператор", everywhere((MENU, QUESTION), ADMIN)),
    Intent(G_HUMAN, "перезвоните мне", ((MENU, ADMIN),), was=BY_LLM),
    # --- Пожаловаться -------------------------------------------------------------------------
    Intent(G_COMPLAIN, "хочу пожаловаться", ((MENU, FEEDBACK_ASK),),
           was="Сразу предлагал передать администратору — передавать было нечего, кроме «хочу "
               "пожаловаться»"),
    Intent(G_COMPLAIN, "ждал приёма час, это безобразие", ((MENU, FEEDBACK),),
           llm=(route("feedback"),), live="feedback"),
    # --- Поблагодарить, поздороваться, попрощаться ----------------------------------------------
    Intent(G_THANKS, "спасибо", everywhere((MENU, BOOKED, PAYMENT), THANKS),
           was="«Спасибо, что написали. Передать Ваше сообщение администратору?»"),
    Intent(G_THANKS, "спасибо большое, всё понятно", ((MENU, THANKS),), was="То же"),
    Intent(G_THANKS, "огромное спасибо доктору!", ((MENU, FEEDBACK),), llm=(route("feedback"),)),
    Intent(G_THANKS, "привет", ((MENU, GREETING), (QUESTION, STAY)),
           was=OFFTOPIC + "; посреди записи — «Уточните, пожалуйста…»"),
    Intent(G_THANKS, "ок", ((MENU, ACK),), was=OFFTOPIC),
    Intent(G_THANKS, "пока", ((MENU, BYE),), was=OFFTOPIC),
    # --- Удалить данные -----------------------------------------------------------------------
    Intent(G_FORGET, "удалите мои данные", everywhere((MENU, BOOKED), FORGET)),
    Intent(G_FORGET, "забудь меня", ((MENU, FORGET),), was=BY_LLM),
    # --- Сообщения из двух частей -------------------------------------------------------------
    Intent(G_TWO, "сколько стоит и запишите на завтра", ((MENU, TWO_BOOK),),
           was="Начинал запись на завтра, вопрос о цене терялся",
           llm=(route("menu_action", action="book", question=True), PRICE), live="menu_action"),
    Intent(G_TWO, "сломал руку, сколько стоит приём?", ((MENU, TWO_COMPLAINT),),
           was="Вёл по сценарию перелома, вопрос о цене терялся",
           llm=(route("complaint", area="limb", scenario="fracture", question=True), PRICE),
           live="complaint"),
    Intent(G_TWO, "где вы находитесь? хочу записаться к Кузнецову", ((MENU, TWO_BOOK),),
           was="Начинал запись к Кузнецову, вопрос об адресе терялся",
           llm=(route("menu_action", action="book", question=True), ADDRESS), live="menu_action"),
    Intent(G_TWO, "спасибо, а как отменить запись?", ((BOOKED, CANCEL_ASKS),)),
    Intent(G_TWO, "что делать укусила собака меня", ((MENU, TWO_ADVICE),),
           was="Сразу спрашивал «Для кого запись?»; на «что делать» не отвечал",
           llm=(route("complaint", scenario="animal_bite", advice=True),)),
    # --- Короткие ответы посреди записи -------------------------------------------------------
    Intent(G_SHORT, "да",
           ((YES_NO, NEXT), (CONSENT, SAME), (DAYS, SAME), (QUESTION, UNCLEAR), (MENU, ACK)),
           was="На вопросе «да / нет» ответ уходил в DeepSeek; в меню — " + OFFTOPIC),
    Intent(G_SHORT, "нет", ((YES_NO, NEXT), (CONSENT, SAME), (QUESTION, UNCLEAR)),
           was="На вопросе «да / нет» ответ уходил в DeepSeek"),
    Intent(G_SHORT, "назад", everywhere(ANYWHERE, BACK),
           was="Не работало нигде: в меню и на первом шаге — " + OFFTOPIC + "; на шагах с "
               "кнопками — " + PICK + "; на шагах «возраст», «ФИО», «дата рождения» бот "
               "переспрашивал без единой кнопки"),
    Intent(G_SHORT, "в меню", everywhere(ANYWHERE, HOME),
           was="Не работало нигде: в меню, на первом шаге и после записи открывался «Задать "
               "вопрос» без кнопок; на остальных шагах — как «назад»"),
    Intent(G_SHORT, "первоначальное меню открой", everywhere(ANYWHERE, HOME),
           was="На шаге выбора дня — «Выберите, пожалуйста, день кнопкой» и тот же экран"),
    Intent(G_SHORT, "открой меню", ((QUESTION, HOME), (DAYS, HOME), (CONSENT, HOME)),
           was="Понимал только «меню», «в меню», «главное меню»"),
    Intent(G_SHORT, "давай заново", ((COMPLAINT, HOME), (DAYS, HOME)),
           was="Понимал только «заново» и «начать заново»"),
    Intent(G_SHORT, "вернись назад", everywhere(ANYWHERE, BACK),
           was="Понимал только «назад», «вернись», «вернуться назад»"),
    Intent(G_SHORT, "не знаю",
           ((COMPLAINT, TOPICS), (QUESTION, UNCLEAR), (AGE, SAME), (DAYS, SAME), (AUDIENCE, SAME)),
           was="На первом шаге — " + OFFTOPIC + " и запись сбрасывалась; на шаге «возраст» — "
               "переспрашивал без кнопок"),
    Intent(G_SHORT, "другое время", ((TIMES, TO_PERIODS), (PERIODS, TO_DAYS), (DAYS, TO_DAYS)),
           was="«Выберите, пожалуйста, время кнопкой» и тот же экран"),
    Intent(G_SHORT, "другой день", everywhere((TIMES, PERIODS), TO_DAYS), was="То же"),
    Intent(G_SHORT, "ближайшее", everywhere((DAYS, PERIODS), NEAREST),
           was="«Выберите, пожалуйста, день кнопкой» и тот же экран"),
    Intent(G_SHORT, "любое", ((DAYS, NEAREST),), was="То же"),
    Intent(G_SHORT, "<время с экрана>", ((TIMES, NEXT),),
           was="«Выберите, пожалуйста, время кнопкой»: время, набранное цифрами, не принималось"),
    Intent(G_SHORT, "для себя", ((AUDIENCE, NEXT),)),
    Intent(G_SHORT, "сыну", ((AUDIENCE, NEXT),)),
    Intent(G_SHORT, "бытовая", ((QUESTION, NEXT),), was="Ответ уходил в DeepSeek"),
    Intent(G_SHORT, "согласен", ((CONSENT, NEXT),), was=PICK),
)  # fmt: skip


def render_doc() -> str:
    pairs = sum(len(i.at) for i in INTENTS)
    fixed = sum(1 for i in INTENTS if i.was and i.was != BY_LLM)
    lines = [
        "# Просьбы пациента: что бот делал и что делает",
        "",
        "Файл собирается из `tests/intent_cases.py` командой `uv run python -m "
        "tests.intent_cases --write`. Каждую фразу `tests/test_intents.py` набирает на каждом "
        "шаге, где пациент может её написать, и проверяет три правила:",
        "",
        "1. просьба о действии никогда не получает «У меня нет этой информации» или «Я помогаю "
        "только с записью…»;",
        "2. ни одна часть сообщения не теряется;",
        "3. у каждого ответа есть кнопка, которая ведёт дальше.",
        "",
        "«Что бот делал» — прогон тех же фраз на коде до исправлений 13.11.2026 с типами, которые "
        "вернул настоящий DeepSeek. Прочерк — уже работало. «Где написана» — шаг, на котором "
        "пациент пишет фразу; «есть запись» — у пациента уже есть предстоящая запись.",
        "",
        f"Всего фраз: {len(INTENTS)}, проверок «фраза на шаге»: {pairs}. Исправлено фраз: {fixed}; "
        f"ещё {sum(1 for i in INTENTS if i.was == BY_LLM)} раньше зависели от DeepSeek, а теперь "
        "разбираются кодом.",
        "",
        "Исключение из правила 3 — ответ на слова о самоубийстве: там только телефоны помощи, "
        "без меню (решение из `docs/CONTEXT.md`, раздел 4).",
        "",
    ]
    for group in dict.fromkeys(i.group for i in INTENTS):
        lines += [
            f"## {group}",
            "",
            "| Фраза | Где написана | Что бот делал | Что должен и делает теперь |",
            "|---|---|---|---|",
        ]
        for intent in (i for i in INTENTS if i.group == group):
            by_must: dict[str, list[str]] = {}
            for step, must in intent.at:
                by_must.setdefault(must, []).append(step)
            rows = list(by_must.items())
            for n, (must, steps) in enumerate(rows):
                phrase = f"«{intent.phrase}»" if n == 0 else ""
                was = (intent.was or "—") if n == 0 else ""
                lines.append(f"| {phrase} | {', '.join(steps)} | {was} | {must} |")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Intent matrix")
    parser.add_argument("--write", action="store_true")
    if parser.parse_args().write:
        DOC.write_text(render_doc(), encoding="utf-8")
        print(f"written {DOC}")
    else:
        print(render_doc())


if __name__ == "__main__":
    main()
