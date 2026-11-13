"""What a patient asks the bot to do, in plain words — read by code before DeepSeek.

* menu commands: «когда у меня приём» opens «Мои записи», as the button does;
* navigation: «назад», «в меню», «отмена» — on any step;
* small talk: «привет», «спасибо», «ок», «пока» — a polite reply, not «я помогаю только с…»;
* who the visit is for: «запишите ребёнка», «можно записать маму?»;
* short answers: «да», «нет», «не знаю», «ближайшее», «другое время».

Only unambiguous wordings: «записаться» alone is a command, but «хочу записаться, сломал
руку» is a complaint and goes to route. Wordings this list misses come back from route as
the ``menu_action`` type. No Telegram and no LLM here.
"""

from __future__ import annotations

import re
from typing import Literal

MenuAction = Literal[
    "book", "my_bookings", "ask", "admin", "forget", "about_bot", "confirm", "feedback"
]
Navigation = Literal["back", "home", "cancel"]
SmallTalk = Literal["greeting", "thanks", "ack", "bye"]
Audience = Literal["adult", "child"]

_BOOKING = r"(?:запис\w*|при[её]м\w*|визит\w*)"
# Requests, not stories: «отмени запись», not «приём у врача отменили».
_CANCEL = r"(?:отмени|отмените|отменить|отменю|отмена)"
_MOVE = r"(?:перенеси|перенесите|перенести|перенос|поменяй|поменяйте|поменять|изменить|измените)"
_PERSON = r"(?:администратор\w*|админ\w*|оператор\w*|жив\w+\s+человек\w*)"
_CALL = (
    r"(?:позови|позовите|позвать|соедини|соедините|соединить|переключи|переключите|свяжите|"
    r"дайте|нужен|нужна|поговорить)"
)
_COME = r"(?:прийти|подойти|приехать|приходить|подходить|приезжать|быть)"
_WANT = r"(?:(?:я\s+)?(?:хочу|можно|нужно|надо|хотел\w*\s+бы|хотелось\s+бы)\s+)?"

COMMANDS: tuple[tuple[MenuAction, re.Pattern[str]], ...] = (
    # «Подтверждаю», «да, приду», «буду» — the visit is confirmed, as the button does
    (
        "confirm",
        re.compile(
            r"^(?:да\s+)?(?:я\s+)?(?:подтверждаю|подтверждаем|подтвержу|подтвердить)"
            r"(?:\s+(?:запись|прием|визит|приход))?$"
        ),
    ),
    ("confirm", re.compile(r"\bподтвержда\w+\s+(?:запись|прием|визит)\b")),
    (
        "confirm",
        re.compile(
            r"^(?:да\s+)?(?:я\s+|мы\s+)?(?:приду|прийду|придем|буду|будем)(?:\s+обязательно)?$"
        ),
    ),
    # «Напомни про мои записи», «когда у меня приём», «моя запись», «на когда я записан»
    ("my_bookings", re.compile(rf"\bмо[йяеиюё]\w*\s+{_BOOKING}")),
    ("my_bookings", re.compile(rf"\bкогда\b.*\bу\s+меня\b.*\b{_BOOKING}")),
    ("my_bookings", re.compile(r"\b(?:я|мы)\s+записан\w*")),
    ("my_bookings", re.compile(rf"\bнапомни\w*\b.*\b{_BOOKING}")),
    ("my_bookings", re.compile(r"\bпокажи\w*\s+записи\b")),
    # «во сколько мне приходить?» — about the patient's own visit, not the opening hours
    (
        "my_bookings",
        re.compile(
            rf"^(?!.*\b(?:контрольн|повторн)).*\b(?:когда|во\s+сколько|к\s+скольки|ко\s+скольки)"
            rf"\s+(?:мне|нам)\s+{_COME}\b"
        ),
    ),
    # «Отмени запись», «перенеси запись на завтра», «запись нужно перенести»
    ("my_bookings", re.compile(rf"\b(?:{_CANCEL}|{_MOVE})\b.*\b{_BOOKING}")),
    ("my_bookings", re.compile(rf"\b{_BOOKING}\b.*\b(?:{_CANCEL}|{_MOVE})\b")),
    # «хочу перенести», «перезапишите меня на пятницу», «можно прийти в другой день?»
    (
        "my_bookings",
        re.compile(
            rf"^{_WANT}(?:перенести|перенеси|перенесите|перезаписаться|перезапишите|перезапиши)\b"
        ),
    ),
    ("my_bookings", re.compile(rf"\b{_COME}\s+в\s+друг\w+\s+(?:день|время)\b")),
    # «я не приду», «не смогу прийти завтра» — the booking, with «Отменить» and «Перенести»
    ("my_bookings", re.compile(r"\bне\s+(?:приду|прийду|придем)\b(?!\s+в\s+себя)")),
    (
        "my_bookings",
        re.compile(rf"\bне\s+(?:смогу|сможем|получится|получается|успею|успеваю)\s+{_COME}\b"),
    ),
    # «Позови администратора», «соедините с оператором», «нужен живой человек»
    ("admin", re.compile(rf"\b{_CALL}\b.*\b{_PERSON}")),
    ("admin", re.compile(rf"^{_PERSON}$")),
    (
        "admin",
        re.compile(
            r"\b(?:поговорить|связаться|соедини\w*|переключи\w*)\b.*\bс\s+(?:человеком|кем\s+нибудь|"
            r"сотрудником|менеджером|кем\s+то)\b"
        ),
    ),
    ("admin", re.compile(r"^(?:человек\w*|сотрудник\w*|нужен\s+человек)$")),
    ("admin", re.compile(r"\b(?:перезвони\w*|позвони\w*\s+мне|свяжитесь\s+со\s+мной)\b")),
    # the whole message is «записаться» — nothing about an injury
    (
        "book",
        re.compile(
            r"^(?:я\s+)?(?:хочу|хотел\w*|можно|нужно|надо|как)?\s*(?:бы\s+)?"
            r"(?:записаться|записать\s+меня|запишите(?:\s+меня)?|запиши(?:\s+меня)?|запись)"
            r"(?:\s+(?:к\s+вам|в\s+травмпункт|на\s+при[её]м|к\s+врачу|к\s+травматологу))?"
            r"(?:\s+пожалуйста)?$"
        ),
    ),
    # «Удалите мои данные», «забудь меня» — the /forget confirmation
    (
        "forget",
        re.compile(
            r"\b(?:удал\w*|сотр\w*|стер\w*|отзыва\w*|отозва\w*)\b.*\b(?:данн\w*|согласи\w*)"
        ),
    ),
    ("forget", re.compile(r"\bзабуд\w+\s+(?:меня|обо\s+мне|про\s+меня)\b")),
    ("forget", re.compile(r"\bудал\w+\s+(?:меня|все\s+обо\s+мне|мой\s+профиль)\b")),
    # «Ты бот?», «ты человек?», «с кем я говорю»
    (
        "about_bot",
        re.compile(r"^(?:а\s+)?(?:ты|вы)\s+(?:бот|человек|робот|живой|живая|нейросеть)\b"),
    ),
    ("about_bot", re.compile(r"\bс\s+кем\s+я\s+(?:говорю|разговариваю|общаюсь|переписываюсь)\b")),
    ("ask", re.compile(r"^(?:у\s+меня\s+)?(?:есть\s+)?(?:хочу\s+задать\s+|задать\s+)?вопрос$")),
    # «хочу пожаловаться», «оставить отзыв» — what happened is asked next
    (
        "feedback",
        re.compile(
            rf"^{_WANT}(?:как\s+|куда\s+|где\s+)?(?:мне\s+)?(?:пожаловаться|"
            r"(?:оставить|написать|подать)\s+(?:жалобу|отзыв|претензию)|жалоба|отзыв|претензия|"
            r"книга\s+жалоб\w*)(?:\s+на\s+\w+)?$"
        ),
    ),
)

NAVIGATION: tuple[tuple[Navigation, re.Pattern[str]], ...] = (
    (
        "back",
        re.compile(
            r"^(?:назад|вернуться|вернись|вернитесь|обратно|шаг\s+назад|вернуться\s+назад|"
            r"на\s+шаг\s+назад|предыдущ\w+(?:\s+шаг)?)$"
        ),
    ),
    (
        "home",
        re.compile(
            r"^(?:(?:в\s+)?(?:главное\s+)?меню|(?:в\s+)?начало|(?:на\s+)?главн\w+|домой|"
            r"(?:начать|начни|начнем)\s+(?:заново|сначала|с\s+начала)|заново|сначала)$"
        ),
    ),
    (
        "cancel",
        re.compile(
            r"^(?:отмена|отменить|отмени|отмените|стоп|хватит|передумал\w*|выйти|выход|закончить|"
            r"прекрати\w*|ничего\s+не\s+(?:надо|нужно)|не\s+буду\s+записываться)$"
        ),
    ),
)

SMALL_TALK: tuple[tuple[SmallTalk, re.Pattern[str]], ...] = (
    (
        "greeting",
        re.compile(
            r"^(?:привет\w*|здравствуй\w*|здрасте|здрасьте|добрый\s+(?:день|вечер)|доброе\s+утро|"
            r"доброй\s+ночи|доброго\s+(?:дня|вечера|утра|времени\s+суток)|хай|hello|hi|алло)"
            r"(?:\s+(?:всем|вам|бот|доктор))?$"
        ),
    ),
    (
        "thanks",
        re.compile(
            r"^(?:(?:ок|хорошо|понятно|ясно|все\s+понятно|поняла?)\s+)?"
            r"(?:спасибо|спс|благодарю|благодарствую|мерси|пасиб\w*|thanks)"
            r"(?:\s+(?:большое|огромное|вам|тебе|за\s+помощь|за\s+ответ|за\s+информацию|"
            r"все\s+понятно|понятно|поняла?|ясно|хорошо|ок))*$"
        ),
    ),
    (
        "ack",
        re.compile(
            r"^(?:ок|окей|ok|хорошо|понятно|поняла?|ясно|ладно|договорились|принято|отлично|супер)$"
        ),
    ),
    (
        "bye",
        re.compile(
            r"^(?:пока|до\s+свидания|досвидания|до\s+встречи|всего\s+(?:доброго|хорошего)|"
            r"прощай\w*|до\s+завтра)$"
        ),
    ),
)

_UNSURE = re.compile(
    r"^(?:я\s+)?(?:не\s+знаю|незнаю|не\s+понимаю|не\s+уверен\w*|затрудняюсь(?:\s+ответить)?|"
    r"сложно\s+сказать|трудно\s+сказать|без\s+понятия|хз|непонятно|не\s+помню|не\s+понял\w*)$"
)
_QUESTION_START = re.compile(
    r"^(?:а\s+|скажите\s+|подскажите\s+)?(?:сколько|где|как|какой|какая|какие|каком|когда|"
    r"во\s+сколько|до\s+скольки|почему|зачем|можно\s+ли|можно|есть\s+ли|есть|что|куда|чем|кто|"
    r"нужно\s+ли|надо\s+ли|обязательно\s+ли|почем|цена|стоимость|адрес|парковка)\b"
)
YES = frozenset({"да", "ага", "угу", "конечно", "верно", "правильно", "именно", "точно", "так"})
NO = frozenset({"нет", "неа", "не", "не надо", "не нужно", "не хочу"})
_NEAREST = re.compile(
    r"^(?:на\s+)?(?:ближайш\w+|люб\w+|все\s+равно|без\s+разницы|неважно|не\s+важно|"
    r"как\s+можно\s+(?:скорее|быстрее|раньше)|поскорее|побыстрее|скорее|срочно|"
    r"перв\w+(?:\s+свободн\w+)?|самое\s+раннее)(?:\s+(?:время|свободное\s+время|окно|запись))?$"
)
_OTHER_TIME = re.compile(r"^(?:друг\w+|ино\w+)\s+(?:время|час\w*)$|^не\s+подходит(?:\s+время)?$")
_OTHER_DAY = re.compile(r"^(?:друг\w+|ино\w+)\s+(?:день|дат\w+|число)$|^(?:в\s+)?друг\w+\s+день$")

# Who the visit is for, when the message says it: «запишите ребёнка», «у мамы перелом».
# Only clear cases: «брата», «сестру», «друга» may be a child or an adult — the bot asks.
_CHILD_WORDS = (
    r"(?:ребен\w+|ребеноч\w+|сын\w*|сыноч\w+|дочь|дочер\w+|дочк\w+|дочурк\w+|малыш\w*|"
    r"внук\w*|внучк\w+|мальчик\w*|девочк\w+)"
)
_ADULT_WORDS = (
    r"(?:мам\w+|мать|матер\w+|пап\w+|отц\w+|отец|муж\w*|жен\w+|супруг\w*|бабушк\w+|дедушк\w+|"
    r"тещ\w+|свекров\w+|тест\w+|свекр\w*)"
)
_BOOK_VERB = r"(?:запис\w+|запиш\w+)"
_BOOK_ORDER = r"(?:запиши|запишите|запишусь|записать|записаться)"
_MOVE_INTENT = re.compile(
    rf"\b(?:{_MOVE}|перезапи[сш]\w+)\b"
    rf"|\b{_CANCEL}\b.*\b{_BOOK_ORDER}\b|\b{_BOOK_ORDER}\b.*\b{_CANCEL}\b"
)
_INJURED = (
    r"(?:упал\w*|ударил\w*|сломал\w*|порезал\w*|обжег\w*|обожгл\w*|подвернул\w*|вывихнул\w*|"
    r"ушиб\w*|поранил\w*|травмировал\w*|наступил\w*|прищемил\w*|повредил\w*|разбил\w*|"
    r"укусил\w*|покусал\w*|ошпарил\w*|потянул\w*|поскользнул\w*)"
)
_MY = r"(?:мо\w+\s+|наш\w+\s+|сво\w+\s+)?"


def _audience_patterns(words: str) -> re.Pattern[str]:
    return re.compile(
        rf"\b{_BOOK_VERB}\s+{_MY}{words}\b"  # «запишите сына»
        rf"|\bдля\s+{_MY}{words}\b"  # «для ребёнка»
        rf"|\bу\s+{_MY}{words}\b"  # «у мамы перелом»
        rf"|^{_MY}{words}\s+{_INJURED}"  # «дочка упала с качелей»
        rf"|\b{_MY}{words}\s+(?:нужн\w+|надо|нужно)\b"  # «сыну нужно к врачу»
    )


_FOR_CHILD = _audience_patterns(_CHILD_WORDS)
_FOR_ADULT = _audience_patterns(_ADULT_WORDS)
_WITH_SOMEONE = re.compile(rf"\bс\s+{_MY}(?:{_CHILD_WORDS}|{_ADULT_WORDS})\b")  # «гуляла с сыном»

# Words of a request to book with nothing else in it: «мне бы к травматологу попасть».
_BOOK_FILLER = re.compile(
    r"^(?:я|мне|меня|нас|бы|хочу|хотел\w*|хотим|хочется|надо|нужно|нужен|нужна|можно|пожал\w*|"
    r"плиз|пж|здравствуйте|здрасте|добрый|доброе|день|вечер|утро|привет|запис\w+|запиш\w+|на|"
    r"к|ко|в|во|врач\w*|доктор\w*|травматолог\w*|прием\w*|травмпункт\w*|вам|вас|попасть|прийти|"
    r"подойти|срочно|очень|ли|а|и|это|мо\w+|наш\w+|сво\w+|"
    + _CHILD_WORDS[3:-1]
    + "|"
    + _ADULT_WORDS[3:-1]
    + r")$"
)
_BOOK_MARK = re.compile(
    r"\b(?:запис\w+|запиш\w+)\b|\b(?:к|ко)\s+(?:врач\w*|доктор\w*|травматолог\w*|вам)\b|"
    r"\bна\s+прием\b"
)


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", text.lower().replace("ё", "е")).split())


def find_command(text: str) -> MenuAction | None:
    t = normalize(text)
    for action, pattern in COMMANDS:
        if pattern.search(t):
            return action
    if _BOOK_MARK.search(t) and all(_BOOK_FILLER.match(word) for word in t.split()):
        return "book"  # «хочу к врачу», «запишыте пожалуста», «можно к вам на приём?»
    return None


def find_navigation(text: str) -> Navigation | None:
    t = normalize(text)
    return next((action for action, pattern in NAVIGATION if pattern.search(t)), None)


def find_small_talk(text: str) -> SmallTalk | None:
    t = normalize(text)
    return next((kind for kind, pattern in SMALL_TALK if pattern.search(t)), None)


def find_audience(text: str) -> Audience | None:
    """«Запишите ребёнка» → child, «можно записать маму?» → adult; None — the bot asks."""
    t = normalize(text)
    if _WITH_SOMEONE.search(t):
        return None
    child, adult = bool(_FOR_CHILD.search(t)), bool(_FOR_ADULT.search(t))
    if child == adult:
        return None
    return "child" if child else "adult"


def is_booking_order(text: str) -> bool:
    """«Запиши», «запишите», «запишусь»: an order to book, whatever else is in the message."""
    return re.search(r"\bзапиш(?:и|ите|усь)\b", normalize(text)) is not None


def wants_move(text: str) -> bool:
    """A new time is wanted, not only the old one dropped: «перенеси на завтра», «отмени и
    запиши на воскресенье», «перезапишите на пятницу». «Отмени запись» is not a move."""
    return _MOVE_INTENT.search(normalize(text)) is not None


def is_unsure(text: str) -> bool:
    return _UNSURE.search(normalize(text)) is not None


def is_question(text: str) -> bool:
    """A question, not an answer: it ends with «?» or starts with a question word."""
    return "?" in text or _QUESTION_START.search(normalize(text)) is not None


def wants_nearest(text: str) -> bool:
    return _NEAREST.search(normalize(text)) is not None


def wants_other_time(text: str) -> bool:
    return _OTHER_TIME.search(normalize(text)) is not None


def wants_other_day(text: str) -> bool:
    return _OTHER_DAY.search(normalize(text)) is not None


def yes_no(text: str) -> bool | None:
    """«да» / «нет» as a whole message."""
    t = normalize(text)
    return True if t in YES else False if t in NO else None
