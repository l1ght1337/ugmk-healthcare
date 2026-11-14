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
Audience = Literal["adult", "child", "self"]

_BOOKING = r"(?:запис\w*|при[её]м\w*|визит\w*)"
# Requests, not stories: «отмени запись», not «приём у врача отменили».
_CANCEL = r"(?:отмени|отмените|отменить|отменю|отмена)"
_MOVE = r"(?:перенеси|перенесите|перенести|перенос|поменяй|поменяйте|поменять|изменить|измените)"
_ALL = r"(?:пожалуйста\s+)?все(?:\s+(?:мои|наши|эти|три|две|оба|обе))?"
_BOOK_ORDER = r"(?:запиши|запишите|запишусь|записать|записаться)"
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
    # «напомни про мои записи», «напомните, когда у меня приём» — not «напомните, плечо тоже
    # посмотрят на приёме?»: only small words may stand between
    (
        "my_bookings",
        re.compile(
            r"\bнапомни\w*\s+(?:(?:мне|нам|пожалуйста|про|о|об|мо\w+|наш\w+|когда|во|сколько|у|"
            rf"меня|нас|на|какое|время|дату|день)\s+)*{_BOOKING}"
        ),
    ),
    ("my_bookings", re.compile(r"\bпокажи\w*\s+записи\b")),
    # «во сколько мне приходить?» — about the patient's own visit, not the opening hours
    (
        "my_bookings",
        re.compile(
            rf"^(?!.*\b(?:контрольн|повторн)).*\b(?:когда|во\s+сколько|к\s+скольки|ко\s+скольки)"
            rf"\s+(?:мне|нам)\s+{_COME}\b"
        ),
    ),
    # «Отмени все», «отмените все записи», «все записи отмени» (see ``wants_all``)
    ("my_bookings", re.compile(rf"\b{_CANCEL}\s+{_ALL}\b(?!\s+равно)")),
    ("my_bookings", re.compile(rf"\b{_ALL}\s+{_BOOKING}\s+(?:пожалуйста\s+)?{_CANCEL}\b")),
    # «Отмени и запиши на воскресенье»: the old time is dropped for a new one — a move
    ("my_bookings", re.compile(rf"\b{_CANCEL}\b.*\b{_BOOK_ORDER}\b")),
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

NAVIGATION_WORDS = 5  # a longer message is a sentence, not a request to go somewhere


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


_NAV_FILLER = _words(
    """
    а ну и мне меня нас пожалуйста плиз пж бот давай давайте хочу можно надо нужно просто
    снова опять обратно в во на к
    """
)
_HOME_OK = _NAV_FILLER | _words(
    """
    меню открой откройте открыть покажи покажите показать верни верните вернись вернитесь
    вернуться выйди выйти выход перейди перейдите перейти зайди главное главную основное
    первоначальное начальное стартовое первое самое начало назад домой
    """
)
_RESTART = frozenset({"заново", "сначала"})
_RESTART_OK = _NAV_FILLER | _RESTART | _words("начать начни начните начнем все")
_BACK_OK = _NAV_FILLER | _words(
    "назад вернись вернитесь вернуться верни верните шаг шага шагу один вопрос вопросу экран экрану"
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
# A request for medical advice or a diagnosis. «Можно ли записаться», «нужно ли брать
# паспорт», «можно ли снять гипс у вас» are not: they are about the visit and the services.
_MEDICAL_VERB = (
    r"(?:мочить|намочить|наступать|наступить|ходить|греть|прогревать|прикладывать|приложить|"
    r"мазать|намазать|помазать|пить|выпить|двигать|шевелить|нагружать|купать\w*|мыть\w*|"
    r"бинтовать|забинтовать|терпеть|спать|заниматься|тренироваться|трогать|вскрывать|"
    r"прокалывать|промывать|промыть|заклеивать|заклеить|охлаждать|массировать|разрабатывать|"
    r"принимать\s+(?:душ|ванну|лекарств\w+|таблетк\w+|обезболивающ\w+)|"
    r"(?:делать|сделать|ставить|поставить)\s+(?:прививк\w+|укол\w*|рентген\w*|снимок|"
    r"компресс\w*))"
)
_ADVICE = re.compile(
    r"\bчто\s+(?:(?:мне|нам|теперь|сейчас|же|дальше|с\s+(?:этим|ним|ней))\s+)*делать\b"
    r"|\bчем\s+(?:(?:его|ее|это|лучше|можно)\s+)*(?:обработать|обрабатывать|мазать|помазать|"
    r"намазать|промыть|лечить|обезболить|снять\s+боль|помочь)\b"
    r"|\bкак\s+(?:(?:это|его|ее|лучше)\s+)?(?:лечить|вылечить|обезболить|снять\s+(?:боль|отек)|"
    r"обработать|перевязать|помочь)\b"
    r"|\bэто\s+(?:не\s+)?(?:перелом|вывих|растяжение|ушиб|трещина|разрыв|серьезно|опасно|"
    r"страшно|нормально)\b"
    r"|\b(?:перелом|вывих|растяжение|ушиб|трещина)\s+(?:это\s+)?или\b"
    r"|\bкак\w+\s+(?:\w+\s+)?(?:обезболивающ\w+|мазь|мазью|лекарств\w+|таблетк\w+|крем\w*|"
    r"препарат\w*)\b"
    rf"|\b(?:можно|нужно|надо|стоит|обязательно)\s+ли\s+(?:\w+\s+){{0,2}}{_MEDICAL_VERB}"
    rf"|\b{_MEDICAL_VERB}\s+ли\b"
)
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
_MOVE_INTENT = re.compile(
    rf"\b(?:{_MOVE}|перезапи[сш]\w+)\b"
    rf"|\b{_CANCEL}\b.*\b{_BOOK_ORDER}\b|\b{_BOOK_ORDER}\b.*\b{_CANCEL}\b"
)
_INJURED = (
    r"(?:упал\w*|ударил\w*|сломал\w*|порезал\w*|обжег\w*|обожгл\w*|подвернул\w*|вывихнул\w*|"
    r"ушиб\w*|поранил\w*|травмировал\w*|наступил\w*|прищемил\w*|повредил\w*|разбил\w*|"
    r"укусил\w*|покусал\w*|ошпарил\w*|потянул\w*|поскользнул\w*|поцарапал\w*|придавил\w*|"
    r"сбил\w*|избил\w*|обварил\w*)"
)
# What a complaint is about, as a noun or «болит»: «у меня перелом», «у меня болит рука».
_COMPLAINT = (
    r"(?:бол\w+|перелом\w*|вывих\w*|ожог\w*|порез\w*|ран[аыуе]|укус\w*|гипс\w*|растяжени\w*|"
    r"ушиб\w*|травм\w+|опух\w+|отек\w*|трещин\w*|шв[ыа]|шов|связк\w+|спиц\w*|повязк\w+|"
    r"перевязк\w+|ортез\w*|скотчкаст\w*|синяк\w*|ссадин\w*|заноз\w*|ноет|хруст\w*)"
)
_MY = r"(?:мо\w+\s+|наш\w+\s+|сво\w+\s+)?"


def _audience_patterns(words: str) -> re.Pattern[str]:
    return re.compile(
        rf"\b{_BOOK_VERB}\s+{_MY}{words}\b"  # «запишите сына»
        rf"|\bдля\s+{_MY}{words}\b"  # «для ребёнка»
        rf"|\bу\s+{_MY}{words}\b"  # «у мамы перелом»
        rf"|^{_MY}{words}\s+{_INJURED}"  # «дочка упала с качелей»
        rf"|\bу\s+меня\s+{words}\b"  # «у меня сын упал»
        rf"|\b{_MY}{words}\s+(?:нужн\w+|надо|нужно)\b"  # «сыну нужно к врачу»
    )


_FOR_CHILD = _audience_patterns(_CHILD_WORDS)
_FOR_ADULT = _audience_patterns(_ADULT_WORDS)
_WITH_SOMEONE = re.compile(rf"\bс\s+{_MY}(?:{_CHILD_WORDS}|{_ADULT_WORDS})\b")  # «гуляла с сыном»
_SOMEONE = re.compile(rf"\b(?:{_CHILD_WORDS}|{_ADULT_WORDS}|мы|нас|нам|нами)\b")
# «Запишите меня» and «я», «меня», «мне», «у меня» next to the complaint: the visit is for the
# one who writes.
_FOR_SELF = re.compile(
    rf"\b{_BOOK_VERB}\s+меня\b"  # «запишите меня», «запиши меня к Ярунину на завтра»
    rf"|\bменя\s+(?:\w+\s+){{0,2}}{_INJURED}"  # «меня вчера укусила собака»
    rf"|{_INJURED}\s+(?:\w+\s+){{0,2}}меня\b"  # «укусила собака меня»
    rf"|\bя\s+(?:\w+\s+){{0,2}}{_INJURED}"  # «я вчера упала с лестницы»
    rf"|\bу\s+меня\s+(?:\w+\s+){{0,2}}{_COMPLAINT}"  # «у меня болит рука», «у меня перелом»
    rf"|\bмне\s+(?:нужн\w+|надо|нужно)\s+(?!запис|запиш)"  # «мне нужно снять гипс»
    rf"|\bмне\s+(?:больно|наложил\w+|зашил\w+|поставил\w+)\b"
    rf"|\b{_INJURED}\s+себе\b|\bсебе\s+{_INJURED}"  # «порезал себе палец»
)

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


# An order to book someone else, or one more visit: «хочу записать мужа… после моего приёма»,
# «нужен второй приём для мужа» are about a new booking, not about «Мои записи».
_BOOKS_ANOTHER = re.compile(
    rf"\b(?:записать|запиши|запишите|запишу|запишем)\s+(?:еще\s+)?(?:и\s+)?{_MY}"
    rf"(?:{_CHILD_WORDS}|{_ADULT_WORDS})\b"
    r"|\b(?:втор\w+|еще\s+од\w+)\s+(?:запис\w+|прием\w*)\b"
)
# «Я уже записан», «не надо меня заново записывать», «мне не нужна новая запись»
_NO_NEW_BOOKING = re.compile(
    r"\b(?:я|мы)\s+уже\s+записан\w*"
    r"|\bне\s+(?:надо|нужно|нужна|нужен|хочу|стоит|требуется)(?:\s+\w+){0,3}?\s+"
    r"(?:нов\w+\s+запис\w+|втор\w+\s+запис\w+|заново|записывать|оформлять)\b"
    r"|\b(?:нов\w+|втор\w+)\s+запис\w+\s+(?:мне\s+)?не\s+(?:надо|нужно|нужна)\b"
)
# Consent to the processing of personal data, said in words — and what makes it no consent.
_CONSENT = re.compile(
    r"\b(?:согласен|согласна|согласны|соглашаюсь|соглашаемся)\b|\bдаю\s+согласие\b|"
    r"\bсогласие\s+даю\b"
)
_CONSENT_NOT = re.compile(r"\b(?:не|ни|нет|несогласен|несогласна|против|отказ\w*|отзыва\w+)\b")
_CONSENT_IF = re.compile(
    r"\b(?:если|но|однако|только|лишь|пока|сначала|сперва|прежде|кроме|услови\w+)\b"
)
_CONSENT_ASKS = re.compile(
    r"\b(?:с\s+чем|на\s+что|что\s+значит|зачем|почему|для\s+чего|как\w+|ли)\b"
)
_SENTENCE_END = re.compile(r"[.!;…]+(?:\s+|$)")
# «Передать администратору?» answered in words. An imperative is a «да» even with a question
# after it («передайте. А когда позвонят?»); «передать» in a question is not.
_HANDOFF_YES = re.compile(
    r"\b(?:передай|передайте|передавай|передавайте)\b"
    r"|\bпусть\s+(?:\w+\s+){0,2}(?:позвон\w+|перезвон\w+|свяж\w+|ответ\w+)\b"
)
_HANDOFF_NO = re.compile(r"^нет\b|\bне\s+(?:надо|нужно|стоит|хочу|передавай\w*|передавать)\b")
# «Когда позвонит администратор?», «сколько ждать звонка?», «через сколько перезвонят»
_WHEN_CALL = re.compile(
    r"\b(?:когда|во\s+сколько|через\s+сколько|как\s+скоро|скоро\s+ли|как\s+быстро)\b"
    r"(?:\s+\w+){0,4}?\s+(?:позвон\w+|перезвон\w+|свяж\w+|ждать\s+звонка)\b"
    r"|\bсколько\s+(?:\w+\s+){0,2}?ждать\b(?!\s+(?:в\s+)?(?:очеред\w+|прием\w*|врача|доктора))"
)
# «Буду ждать звонка», «больше вопросов нет», «всего доброго» — with a «спасибо» or without:
# the talk is over. Anything asked for in the same message makes it not small talk.
_WAITING = re.compile(
    r"\b(?:буду|будем)\s+ждать\b|\bжду\s+(?:звонка|ответа)\b|\bвопросов\s+(?:больше\s+)?нет\b"
    r"|\bна\s+этом\s+все\b"
)
_BYE = re.compile(
    r"\b(?:до\s+свидания|досвидания|всего\s+(?:доброго|хорошего)|до\s+встречи|прощайте)\b"
)
_THANKS_WORD = re.compile(r"\b(?:спасибо|благодарю|спс)\b")
_ASKS_FOR = re.compile(
    r"\b(?:запиш\w+|запис\w+|передай\w*|передавай\w*|передать|позови\w*|позвать|перенес\w+|"
    r"перенести|отмен\w+|болит|болят|болело|болела|боль|больно|опух\w*|кров\w+|перелом\w*|"
    r"вывих\w*|травм\w+|упал\w*|ушиб\w*)\b"
)
SMALL_TALK_WORDS = 16  # a longer message says more than «спасибо, буду ждать»
# Two patients in one message: «меня и сына», «для себя и для мужа», «обоих», «двоих детей».
# «Обе ноги», «оба колена», «оба раза» are not two patients.
_MANY = re.compile(
    r"\b(?:двоих|двое|двоим|вдвоем|троих|трое|нас\s+дво\w+)\b"
    r"|\b(?:обоих|обеих|обоим|обеим|оба|обе)\b(?!\s+(?:ног\w*|рук\w*|колен\w*|плеч\w*|пальц\w*|"
    r"глаз\w*|стоп\w*|кист\w*|запяст\w*|локт\w*|голен\w*|бедр\w*|лодыж\w*|ух\w*|уш\w*|пятк\w*|"
    r"сторон\w*|раза|варианта|дня|врач\w*))"
)
_SELF_TOO = re.compile(
    rf"\bдля\s+себя\b|\bсебя\b|\b{_BOOK_VERB}\s+(?:\w+\s+)?меня\b|\bменя\s+(?:и|с)\b"
    r"|\b(?:и|с)\s+меня\b|\b(?:сначала|сперва)\s+(?:\w+\s+)?(?:меня|я)\b|\bпосле\s+меня\b"
)
_FIRST = r"\b(?:сначала|сперва|первым|первой)\s+(?:\w+\s+){0,2}?(?:для\s+)?"
_FIRST_OF_TWO: tuple[tuple[Audience, re.Pattern[str]], ...] = (
    ("self", re.compile(rf"{_FIRST}(?:меня|себя|я)\b|\bпосле\s+меня\b")),
    ("child", re.compile(rf"{_FIRST}{_MY}{_CHILD_WORDS}\b")),
    ("adult", re.compile(rf"{_FIRST}{_MY}{_ADULT_WORDS}\b")),
)


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", text.lower().replace("ё", "е")).split())


def find_command(text: str) -> MenuAction | None:
    t = normalize(text)
    another = _BOOKS_ANOTHER.search(t) is not None and _MOVE_INTENT.search(t) is None
    another = another and re.search(rf"\b{_CANCEL}\b", t) is None
    for action, pattern in COMMANDS:
        if pattern.search(t):
            if action == "my_bookings" and another:
                continue  # a new booking for someone else: the text goes on to the complaint
            return action
    if _BOOK_MARK.search(t) and all(_BOOK_FILLER.match(word) for word in t.split()):
        return "book"  # «хочу к врачу», «запишыте пожалуста», «можно к вам на приём?»
    return None


def find_navigation(text: str) -> Navigation | None:
    """«Назад», «в меню», «отмена» — and short requests in other words: «открой меню»,
    «первоначальное меню открой», «вернись назад», «давай заново». A long message where
    «меню» is a part of a question is not navigation."""
    t = normalize(text)
    exact = next((action for action, pattern in NAVIGATION if pattern.search(t)), None)
    words = t.split()
    if exact is not None or not 0 < len(words) <= NAVIGATION_WORDS:
        return exact
    said = set(words)
    if "меню" in said and said <= _HOME_OK:
        return "home"
    if said & _RESTART and said <= _RESTART_OK:
        return "home"
    if any(w == "назад" or w.startswith("предыдущ") for w in words) and all(
        w in _BACK_OK or w.startswith("предыдущ") for w in words
    ):
        return "back"
    return None


def find_small_talk(text: str) -> SmallTalk | None:
    """«Привет», «спасибо», «ок», «пока» as the whole message — and the end of a talk said
    in a sentence: «спасибо, больше вопросов нет, буду ждать звонка», «всего доброго»."""
    t = normalize(text)
    exact = next((kind for kind, pattern in SMALL_TALK if pattern.search(t)), None)
    if exact is not None:
        return exact
    if "?" in text or len(t.split()) > SMALL_TALK_WORDS:
        return None
    if _ASKS_FOR.search(t) or _QUESTION_START.search(t):
        return None  # something is asked for: the message is not only a goodbye
    waiting = _WAITING.search(t) is not None
    if _BYE.search(t) and (waiting or _THANKS_WORD.search(t)):
        return "bye"
    return "thanks" if waiting else None


def wants_handoff(text: str) -> bool | None:
    """«Передать администратору?» answered in words: True — «да, передайте…», «пусть
    позвонит»; False — «не надо», «не передавайте»; None — the text says neither."""
    t = normalize(text)
    if _HANDOFF_NO.search(t):
        return False
    if _HANDOFF_YES.search(t):
        return True
    return True if re.search(r"\bпередать\b", t) and not is_question(text) else None


def asks_when_admin(text: str) -> bool:
    """«Когда позвонит администратор?», «сколько ждать звонка?»."""
    return _WHEN_CALL.search(normalize(text)) is not None


def names_several(text: str) -> bool:
    """More than one patient in a message: «меня и сына», «сначала для себя, потом сына»,
    «мы с мужем, обоих», «двоих детей». «Гуляла с сыном, он упал» names one."""
    t = normalize(text)
    if _MANY.search(t):
        return True
    other = re.search(rf"\b(?:{_CHILD_WORDS}|{_ADULT_WORDS})\b", t) is not None
    return other and _SELF_TOO.search(t) is not None


def after_me(text: str) -> Audience | None:
    """«Запишите мужа после меня»: the other patient — for the one who is booked already."""
    t = normalize(text)
    if re.search(r"\bпосле\s+меня\b", t) is None:
        return None
    child = re.search(rf"\b{_CHILD_WORDS}\b", t) is not None
    adult = re.search(rf"\b{_ADULT_WORDS}\b", t) is not None
    return None if child == adult else "child" if child else "adult"


def find_audience(text: str) -> Audience | None:
    """«Запишите ребёнка» → child, «можно записать маму?» → adult, «меня укусила собака» →
    self, «запишите меня» → self; None — the bot asks («меня и сына», «запишите меня с
    ребёнком», «гуляла с сыном», «запишите брата»)."""
    t = normalize(text)
    if names_several(text):  # «сначала для себя, потом сына»: the first, if the text says who
        for first, pattern in _FIRST_OF_TWO:
            if pattern.search(t):
                return first
    if _WITH_SOMEONE.search(t):
        return None
    child, adult = bool(_FOR_CHILD.search(t)), bool(_FOR_ADULT.search(t))
    if _FOR_SELF.search(t):  # with someone else in the text it is not clear who is hurt
        return None if child or adult or _SOMEONE.search(t) else "self"
    if child == adult:
        return None
    return "child" if child else "adult"


def asks_advice(text: str) -> bool:
    """«Что делать?», «это перелом?», «чем обработать?», «можно ли мочить?» — a request for
    medical advice or a diagnosis; a safety net for route's ``advice`` flag."""
    return _ADVICE.search(normalize(text)) is not None


def is_booking_order(text: str) -> bool:
    """«Запиши», «запишите», «запишусь»: an order to book, whatever else is in the message."""
    return re.search(r"\bзапиш(?:и|ите|усь)\b", normalize(text)) is not None


def wants_move(text: str) -> bool:
    """A new time is wanted, not only the old one dropped: «перенеси на завтра», «отмени и
    запиши на воскресенье», «перезапишите на пятницу». «Отмени запись» is not a move."""
    return _MOVE_INTENT.search(normalize(text)) is not None


def wants_cancel(text: str) -> bool:
    """A request to cancel a booking: «отмени запись», «отмените запись на воскресенье»,
    «отмени все». «Я не приду» is not: the patient may want another time."""
    return re.search(rf"\b{_CANCEL}\b", normalize(text)) is not None


def wants_all(text: str) -> bool:
    """«Отмени все», «отмените все записи», «все записи отмени» — every booking at once."""
    t = normalize(text)
    return (
        re.search(rf"\b{_CANCEL}\s+{_ALL}\b(?!\s+равно)", t) is not None
        or re.search(rf"\b{_ALL}\s+{_BOOKING}\s+(?:пожалуйста\s+)?{_CANCEL}\b", t) is not None
    )


def refuses_new_booking(text: str) -> bool:
    """«Я уже записан», «не надо меня заново записывать», «новая запись не нужна»: said in
    the middle of a booking by a patient who has one, it stops the new one."""
    return _NO_NEW_BOOKING.search(normalize(text)) is not None


def gives_consent(text: str) -> bool:
    """Consent inside a sentence: «согласна, записывайте», «на обработку данных согласен».

    Only a plain consent counts. A «не» or a condition in the sentence with the word
    («не согласен», «согласен, если…», «согласна, но только…»), a refusal anywhere, or a
    question («а с чем я согласен?») — and it is no consent: the bot shows the consent again.
    """
    if "?" in text:
        return False
    said = False
    for sentence in _SENTENCE_END.split(text):
        t = normalize(sentence)
        if re.search(r"\b(?:против|отказ\w*|отзыва\w+)\b", t):
            return False
        if _CONSENT.search(t) is None:
            continue
        if _CONSENT_NOT.search(t) or _CONSENT_IF.search(t) or _CONSENT_ASKS.search(t):
            return False
        said = True
    return said


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
