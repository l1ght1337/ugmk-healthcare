"""Booking wishes: the doctor, the day and the time of day the patient asked for.

«Запиши меня к Ярунину на завтра утром» — the wish is read by code, before masking and
before any LLM call, and only narrows the schedule: the scenario questions stay, and the
04-09 rules for repeat visits come first.

* the doctor — a surname from config/schedule.yaml in any case and register, with or
  without initials, or the first name with the patronymic; one wrong letter is forgiven
  after «к»; a patient's own surname («меня зовут Кузнецов…») is not a doctor;
* the day — сегодня, завтра, послезавтра, a weekday, «15 ноября», «15.11», «20 числа»,
  «через 2 дня»; always today or later, by the clinic's calendar;
* the time — утром, днём, вечером, ночью, «в 14:30», «после 18», «до 12», «с 10 до 12».

The day of the injury is not a wish: «в субботу упал» and «сегодня утром ударился» are
about what happened. A word counts when it follows «на» («на завтра»), stands next to a
request («можно в субботу?») or the message is about the visit only.

No Telegram and no LLM here: the same parser will serve MAX.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel

DOCTOR_LABEL = "[ВРАЧ]"  # what DeepSeek sees instead of a doctor's name
Context = Literal["free", "time", "strict"]
Question = Literal["who", "doctor", "free"]
PERIODS = ("morning", "day", "evening", "night")


class Wish(BaseModel):
    """What the patient asked for. Kept in the session; a filter over the schedule."""

    doctor: str | None = None  # the full name as in config/schedule.yaml
    day: date | None = None
    period: str | None = None  # morning / day / evening / night
    start: time | None = None  # «после 18» → 18:00
    end: time | None = None  # «до 12» → 12:00
    at: time | None = None  # «в 14:30»
    noted: datetime | None = None  # UTC; an old wish is forgotten

    @property
    def has_time(self) -> bool:
        return any((self.period, self.start, self.end, self.at))

    @property
    def empty(self) -> bool:
        return not (self.doctor or self.day or self.has_time)

    def merge(self, other: Wish) -> Wish:
        """``other`` said later: it replaces the doctor, the day or the whole time wish."""
        data = self.model_dump()
        if other.doctor:
            data["doctor"] = other.doctor
        if other.day:
            data["day"] = other.day
        if other.has_time:
            data.update(period=other.period, start=other.start, end=other.end, at=other.at)
        if other.noted:
            data["noted"] = other.noted
        return Wish(**data)

    def without(self, *names: str) -> Wish:
        return self.model_copy(update=dict.fromkeys(names))

    def without_time(self) -> Wish:
        return self.without("period", "start", "end", "at")


@dataclass(frozen=True)
class Parsed:
    wish: Wish
    unknown_doctor: bool = False  # «к Иванову»: a surname that is not in the list
    booking: bool = False  # «запишите», «записаться»
    pure: bool = False  # nothing but the wish and words like «запишите», «можно»
    question: Question | None = None  # a question about the schedule
    rest: str = ""  # the other words of the message
    doctor_spans: tuple[tuple[int, int], ...] = ()  # where doctors are named in the text


# --- names ---------------------------------------------------------------------------------


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def short_name(full: str) -> str:
    """«Ярунин Андрей Александрович» → «Ярунин А. А.»."""
    surname, *names = full.split()
    return " ".join([surname, *(f"{n[0]}." for n in names)])


def dative(full: str, *, initials: bool = True) -> str:
    """«к Ярунину А. А.»; a surname the rules do not cover — «к врачу Шевченко Т. Г.».

    ``initials=False`` — «к Ярунину», as a patient would write it.
    """
    surname, *names = full.split()
    letters = " ".join(f"{n[0]}." for n in names) if initials else ""
    female = bool(names) and _norm(names[-1]).endswith(("вна", "чна", "кызы"))
    low = _norm(surname)
    form = None
    if low.endswith(("ов", "ев", "ин", "ын")) and not female:
        form = surname + "у"
    elif low.endswith(("ова", "ева", "ина", "ына")) and female:
        form = surname[:-1] + "ой"
    elif low.endswith(("ский", "цкий")) and not female:
        form = surname[:-2] + "ому"
    elif low.endswith(("ская", "цкая")) and female:
        form = surname[:-2] + "ой"
    if form is None:
        return f"к врачу {surname} {letters}".strip()
    return f"к {form} {letters}".strip()


def _surname_forms(surname: str) -> list[str]:
    """Case forms of a surname, lower-case: Ярунин, Ярунина, Ярунину, Яруниным, Ярунине."""
    s = _norm(surname)
    if s.endswith(("ов", "ев", "ин", "ын")):
        return [s, s + "а", s + "у", s + "ым", s + "е"]
    if s.endswith(("ова", "ева", "ина", "ына")):
        return [s, s[:-1] + "ой", s[:-1] + "у", s[:-1] + "ою"]
    if s.endswith(("ский", "цкий")):
        stem = s[:-2]
        return [s, stem + "ого", stem + "ому", stem + "им", stem + "ом"]
    if s.endswith(("ская", "цкая")):
        stem = s[:-2]
        return [s, stem + "ой", stem + "ую"]
    if s[-1] in "бвгджзклмнпрстфхцчшщ":
        return [s, s + "а", s + "у", s + "ом", s + "е"]
    return [s]


def _name_forms(name: str) -> str:
    """A first name or patronymic in any case, as a pattern: Андрей/Андрея/Андрею."""
    s = _norm(name)
    stem = s[:-1] if s[-1] in "йьая" else s
    return re.escape(stem) + r"[а-я]{0,3}"


@dataclass(frozen=True)
class _Doctor:
    full: str
    forms: tuple[str, ...]
    surname: re.Pattern[str]  # the surname, with the doctor's own name or initials around it
    names: re.Pattern[str] | None  # «Андрею Александровичу» without the surname

    @classmethod
    def of(cls, full: str) -> _Doctor:
        surname, *names = full.split()
        forms = tuple(sorted(_surname_forms(surname), key=len, reverse=True))
        alternatives = "|".join(re.escape(f) for f in forms)
        lead = tail = ""
        both = None
        if names:
            parts = [_name_forms(n) for n in names]
            letters = [re.escape(_norm(n[0])) for n in names]
            initials = r"\s+" + r"\.\s?".join(letters) + r"\.?(?![а-я])"
            own = rf"\s+{parts[0]}\b" + (rf"(?:\s+{parts[1]}\b)?" if len(parts) > 1 else "")
            lead = rf"(?P<lead>{parts[0]}\s+)?"
            tail = rf"(?P<tail>{initials}|{own})?"
            if len(parts) > 1:
                both = re.compile(rf"\b{parts[0]}\s+{parts[1]}\b")
        pattern = re.compile(rf"\b{lead}(?P<surname>{alternatives})\b{tail}")
        return cls(full, forms, pattern, both)


def _near(word: str, form: str) -> bool:
    """One letter wrong, missing or extra."""
    if word == form:
        return True
    if abs(len(word) - len(form)) > 1:
        return False
    if len(word) == len(form):
        return sum(a != b for a, b in zip(word, form, strict=True)) == 1
    short, long = sorted((word, form), key=len)
    return any(long[:i] + long[i + 1 :] == short for i in range(len(long)))


# --- words ---------------------------------------------------------------------------------


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
    "октября", "ноября", "декабря",
)  # fmt: skip
_WEEKDAYS = (
    r"понедельник[ауе]?", r"вторник[ауе]?", r"сред[ауые]", r"четверг[ауе]?", r"пятниц[ауые]",
    r"суббот[ауые]", r"воскресень[еяю]",
)  # fmt: skip
_NUMBER_WORDS = {"один": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6}
_PREP = r"(?:\b(?P<prep>на|в|во|к|ко)\s+)?"

_RELATIVE = re.compile(rf"{_PREP}\b(?P<word>сегодня|завтра|послезавтра)\b")
_WEEKDAY = re.compile(
    rf"(?P<past>прошл\w+\s+)?{_PREP}(?P<next>следующ\w+\s+)?\b(?P<day>{'|'.join(_WEEKDAYS)})\b"
)
_MONTH_DATE = re.compile(rf"{_PREP}\b(?P<d>\d{{1,2}})(?:-?го)?\s+(?P<m>{'|'.join(_MONTHS)})\b")
_DOTTED = re.compile(
    rf"{_PREP}(?<![\d./])(?P<d>\d{{1,2}})[./](?P<m>\d{{1,2}})(?:[./](?P<y>\d{{4}}|\d{{2}}))?(?![\d./])"
)
_DAY_NUMBER = re.compile(
    rf"{_PREP}(?<![\d.:])(?P<d>\d{{1,2}})(?:(?:-?го|-?е)?\s+числ[оа]|-(?:го|е)\b)"
)
_IN_DAYS = re.compile(
    r"\bчерез\s+(?:(?P<n>\d{1,2}|один|два|две|три|четыре|пять|шесть)\s+)?"
    r"(?P<unit>день|дня|дней|неделю|недели|недель)\b"
)
_WEEKEND = re.compile(r"\b(?:на|в)\s+выходны[ехй]\b")

_PERIOD_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "morning",
        re.compile(
            r"\b(?:(?P<prep>на)\s+утро|утром|утречком|с\s+утра|с\s+утречка|до\s+обеда|"
            r"в\s+первой\s+половине\s+дня)\b"
        ),
    ),
    (
        "day",
        re.compile(
            r"\b(?:днем|в\s+обед|после\s+обеда|в\s+обеденное\s+время|в\s+середине\s+дня|"
            r"во\s+второй\s+половине\s+дня)\b"
        ),
    ),
    (
        "evening",
        re.compile(
            r"\b(?:(?P<prep>на)\s+вечер|вечером|вечерком|после\s+работы|в\s+вечернее\s+время)\b"
        ),
    ),
    ("night", re.compile(r"\b(?:(?P<prep>на)\s+ночь|ночью|в\s+ночь|в\s+ночное\s+время)\b")),
)
_CLOCK = r"(?P<h{0}>\d{{1,2}})(?:[:.](?P<m{0}>\d{{2}}))?(?:\s*(?P<u{0}>ч\b|час\w*))?"
_PART = r"(?:\s+(?P<part{0}>утра|дня|вечера|ночи))?"
_AT = re.compile(rf"\b(?P<prep>в|во|к|на|около)\s+{_CLOCK.format('')}{_PART.format('')}(?![\d.:])")
_AFTER = re.compile(rf"\bпосле\s+{_CLOCK.format('')}{_PART.format('')}(?![\d.:])")
_BEFORE = re.compile(rf"\bдо\s+{_CLOCK.format('')}{_PART.format('')}(?![\d.:])")
_FROM_TO = re.compile(
    rf"\bс\s+{_CLOCK.format('')}\s+(?:до|по)\s+{_CLOCK.format('2')}{_PART.format('')}(?![\d.:])"
)
# «7 лет», «3 дня», «2 метров», «в 3 раза» — a number with a unit is not a time
_NOT_A_TIME = re.compile(
    r"\s*(?:лет|год\w*|месяц\w*|мес\b|дн\w+|день|недел\w+|метр\w*|раз\w*|этаж\w*|кг|см|мм|"
    r"руб\w*|тыс\w*|шт\w*|числ\w+|кабинет\w*|корпус\w*|%)"
)
# a bare «в 3» is an hour only at the end of a phrase or before words about the visit
_BARE_HOUR_OK = re.compile(
    r"\s*(?:$|[?!.,;)]|можно|сегодня|завтра|послезавтра|будет|есть|свобод\w+|удобн\w+|"
    r"подойд\w+|получ\w+|пожалуйста|или|примерно|нормальн\w+)"
)

# What happened (the day next to it is the day of the injury) and what is asked for.
_EVENT = re.compile(
    r"\b(?:упал|упад|ударил|ударен|ушиб|подверн|вывихн|слома|порез|поранил|обжег|обожг|ошпар|"
    r"укус|покусал|наступил|поскользн|потянул|прищем|придавил|травмир|получил|случил|произошл|"
    r"бол[ия]т|болел|опух|отек|ноет|хрустн|защем|повредил|поврежд|разбил|рассек|уронил|"
    r"сбил|подрал|избил|началось|начал[ао]сь)\w*"
)
_CUE = re.compile(
    r"\b(?:запис\w+|запиш\w+|прийти|приду|придем|приехать|приеду|приедем|подойти|подойду|"
    r"подъехать|подъеду|попасть|прием\w*|можно|хочу|хотел\w*|хотим|хочется|нужно|надо|время|"
    r"времени|окн[оа]|окошк\w+|свободн\w+|принима\w+|примет\w*|принять|работа[ею]\w+|дежур\w+|"
    r"перенес\w*|перенос\w*|перенести|поменя\w+|смен[еу])\b"
)
_CLAUSE_END = re.compile(r"[!?;\n]|[.,](?=\s|$)|\s(?:и|а|но)\s|\s—\s")
_IMPERATIVE = re.compile(r"\b(?:запиши|запишите|запишусь|записать)\b")
_BOOKING_WORD = re.compile(r"\b(?:запис\w+|запиш\w+)\b")

_DOCTOR_WORD = r"(?:врач\w*|доктор\w*|травматолог\w*|специалист\w*)"
_BEFORE_DOCTOR = re.compile(rf"(?:\b(?:к|ко)\s+(?:{_DOCTOR_WORD}\s+)?|\b{_DOCTOR_WORD}\s+)$")
_BEFORE_PATIENT = re.compile(r"\b(?:зовут|фамилия|фио|я|это|пациент\w*)[\s:,—-]*$")
_ABOUT_SHIFT = re.compile(
    r"\b(?:принима\w+|работа[ею]\w+|дежур\w+|график\w*|расписани\w+|смен[еау]|будет|время|"
    r"времени|окн[оа]|окошк\w+|свобод\w+|запис\w+|запиш\w+|попасть)\b"
)
_TO_SOMEONE = re.compile(
    rf"\b(?:к|ко)\s+(?P<title>{_DOCTOR_WORD}\s+)?(?P<name>[а-я][а-я-]{{3,}})(?P<next>\s+[а-я]+)?"
)
_SURNAME_DATIVE = re.compile(r"^[а-я-]{3,}(?:(?:ов|ев|ин|ын)(?:у|ой)|(?:ск|цк)(?:ому|ой))$")
_NOT_SURNAMES = _words(
    """
    магазину господину карантину витамину аспирину острову улову покрову детскому детской
    взрослому городскому городской женскому женской мужскому мужской заводскому русскому
    районному областному дежурному другому любому своему вашему нашему этому такому
    травматологу хирургу врачу доктору специалисту ортопеду педиатру неврологу терапевту
    """
)
_AFTER_ADJECTIVE = re.compile(
    r"^\s+(?:врач\w*|доктор\w*|травматолог\w*|хирург\w*|ортопед\w*|поликлиник\w*|больниц\w*|"
    r"отделени\w*|клиник\w*|травмпункт\w*|специалист\w*)"
)

_WHO = re.compile(
    r"\bкто\b(?!\s+меня)(?=.*\b(?:принима\w+|работа[ею]\w+|дежур\w+|смен[еу]|врач\w*|"
    r"доктор\w*|травматолог\w*))"
)
_WHICH_DOCTOR = re.compile(r"\bкак(?:ой|ие)\s+(?:врач|доктор|травматолог)\w*")
_NOW_OR_SHIFT = re.compile(r"\b(?:сейчас|дежур\w+|смен[еу])\b")
_WHEN_DOCTOR = re.compile(
    r"\b(?:когда|в\s+какие\s+дни|по\s+каким\s+дням|график\w*|расписани\w+|работает|принимает|"
    r"дежурит|будет|смен[еу])\b"
)
_FREE = re.compile(
    r"\b(?:есть|будет|найдется|остал\w*)\b[^.!?]*\b(?:время|времени|окн[оа]|окошк\w+|мест[оа]?|"
    r"запись|записи|слот\w*)\b"
    r"|\bсвободн\w+"
    r"|\bближайш\w+\s+(?:\w+\s+)?(?:время|запись|окно|окошко|прием|дата|день)\b"
    r"|\bкогда\s+(?:можно|я\s+могу|мне\s+можно)\s+(?:записаться|прийти|подойти|попасть|приехать)\b"
    r"|\bна\s+когда\s+(?:можно|есть)\b"
)

_FILLERS = _words(
    """
    я мне меня нас нам мы бы хочу хотел хотела хотелось хотим хочется можно нужно надо
    пожалуйста плиз пж здравствуйте здрасте здравствуй добрый доброе доброй день вечер утро
    ночи привет запиши запишите записать записаться запись записи запишусь на прием приема
    к ко врачу врач врача доктору доктор доктора травматологу травматолог травматолога в во
    травмпункт травмпункте вам вас у именно только ли а и то это попасть прийти подойти
    приехать время времени лучше желательно удобно было будет там тогда если давайте давай
    ну он она есть как раз же уже еще или либо перенеси перенесите перенести мою
    нужен нужна нужны ребенка сына сыночка дочь дочку дочери малыша внука внучку маму папу
    мужа жену бабушку
    дедушку моего нашего нашу своего свою
    """
)


@dataclass(frozen=True)
class _Mention:
    start: int
    end: int
    explicit: bool  # «на завтра», «на вечер»: always a wish
    future: bool = False  # «завтра», «через 2 дня»: cannot be the day of the injury
    day: date | None = None
    period: str | None = None
    window: tuple[time | None, time | None] | None = None
    at: time | None = None


def _free_span(taken: list[tuple[int, int]], start: int, end: int) -> bool:
    return all(end <= a or start >= b for a, b in taken)


def _upcoming(today: date, month: int, day: int, year: int | None = None) -> date | None:
    """A date that is today or ahead; a past one is the day of the injury, not a wish."""
    try:
        if year is not None:
            found = date(year + 2000 if year < 100 else year, month, day)
            return found if found >= today else None
        found = date(today.year, month, day)
        if found < today:
            found = date(today.year + 1, month, day)
            if (found - today).days > 60:
                return None
        return found
    except ValueError:
        return None


def _day_of_month(today: date, number: int) -> date | None:
    for offset in range(62):
        candidate = today + timedelta(days=offset)
        if candidate.day == number:
            return candidate
    return None


def _clock(hour: str, minute: str | None, part: str | None) -> time | None:
    """«9 утра», «7 вечера», «14:30»; a bare «в 3» is three in the afternoon."""
    h, m = int(hour), int(minute or 0)
    if h > 23 or m > 59:
        return None
    if part in ("вечера", "дня") and h < 12:
        h += 12
    elif part == "ночи" and h == 12:
        h = 0
    elif part is None and 1 <= h <= 7:
        h += 12
    return time(h, m)


def _days(t: str, today: date, taken: list[tuple[int, int]]) -> list[_Mention]:
    found: list[_Mention] = []

    def add(m: re.Match[str], day: date | None, *, future: bool = False) -> None:
        if day is None or day < today or not _free_span(taken, m.start(), m.end()):
            return
        prep = m.groupdict().get("prep")
        taken.append((m.start(), m.end()))
        found.append(_Mention(m.start(), m.end(), prep == "на", future, day=day))

    for m in _MONTH_DATE.finditer(t):
        add(m, _upcoming(today, _MONTHS.index(m["m"]) + 1, int(m["d"])), future=True)
    for m in _DOTTED.finditer(t):
        if m["prep"] in ("в", "во", "к") and not m["y"]:  # «в 14.30» is a time
            continue
        day = _upcoming(today, int(m["m"]), int(m["d"]), int(m["y"]) if m["y"] else None)
        add(m, day, future=True)
    for m in _DAY_NUMBER.finditer(t):
        add(m, _day_of_month(today, int(m["d"])), future=True)
    for m in _IN_DAYS.finditer(t):
        n = m["n"]
        count = 1 if n is None else _NUMBER_WORDS.get(n) or int(n)
        if m["unit"].startswith("недел"):
            add(m, today + timedelta(days=7 * count), future=True)
        else:  # «через день» is the day after tomorrow
            add(m, today + timedelta(days=count + 1 if n is None else count), future=True)
    for m in _WEEKEND.finditer(t):
        ahead = (5 - today.weekday()) % 7 if today.weekday() < 6 else 0
        add(m, today + timedelta(days=ahead))
    for m in _RELATIVE.finditer(t):
        offset = ("сегодня", "завтра", "послезавтра").index(m["word"])
        add(m, today + timedelta(days=offset), future=offset > 0)
    for m in _WEEKDAY.finditer(t):
        if m["past"]:
            continue
        index = next(i for i, w in enumerate(_WEEKDAYS) if re.fullmatch(w, m["day"]))
        ahead = (index - today.weekday()) % 7 or 7  # «в четверг» on a Thursday: the next one
        day = today + timedelta(days=ahead)
        if m["next"] and day.isocalendar()[:2] == today.isocalendar()[:2]:
            day += timedelta(days=7)
        add(m, day)
    return found


def _times(t: str, taken: list[tuple[int, int]]) -> list[_Mention]:
    found: list[_Mention] = []

    def take(m: re.Match[str]) -> bool:
        if not _free_span(taken, m.start(), m.end()):
            return False
        taken.append((m.start(), m.end()))
        return True

    def bare(m: re.Match[str]) -> bool:
        return not (m["m"] or m["u"] or m["part"])

    def unit_after(m: re.Match[str]) -> bool:
        """«после 18 лет», «до 7 дней»: a number with a unit, not an hour."""
        return bare(m) and _NOT_A_TIME.match(t, m.end()) is not None

    for m in _FROM_TO.finditer(t):
        if _NOT_A_TIME.match(t, m.end()) and not (m["u2"] or m["part"]):
            continue
        start = _clock(m["h"], m["m"], m["part"])
        end = _clock(m["h2"], m["m2"], m["part"])
        if start and end and start < end and take(m):
            found.append(_Mention(m.start(), m.end(), False, window=(start, end)))
    for pattern, side in ((_AFTER, 0), (_BEFORE, 1)):
        for m in pattern.finditer(t):
            moment = _clock(m["h"], m["m"], m["part"])
            if moment is None or unit_after(m) or not take(m):
                continue
            window = (moment, None) if side == 0 else (None, moment)
            found.append(_Mention(m.start(), m.end(), False, window=window))
    for m in _AT.finditer(t):
        moment = _clock(m["h"], m["m"], m["part"])
        if moment is None or unit_after(m) or (bare(m) and not _BARE_HOUR_OK.match(t, m.end())):
            continue
        if take(m):
            found.append(_Mention(m.start(), m.end(), m["prep"] == "на", at=moment))
    for period, pattern in _PERIOD_WORDS:
        for m in pattern.finditer(t):
            if take(m):
                explicit = m.groupdict().get("prep") == "на"
                found.append(_Mention(m.start(), m.end(), explicit, period=period))
    return found


_OWN_NAME_AFTER = re.compile(r"\s+[а-я]{3,}\s+[а-я]+(?:вич|вна|чна)[а-я]{0,2}\b")
_WORD_AFTER = re.compile(r"\s+(?P<word>[а-я]{3,})\b")
_WISH_WORD = re.compile(
    r"завтра|сегодня|послезавтра|утром|днем|вечером|ночью|можно|пожалуйста|свобод\w+"
)


_Named = tuple[_Doctor, int, int, bool]  # doctor, start, end, sure


def _named_doctors(t: str, doctors: tuple[_Doctor, ...]) -> list[_Named]:
    """Doctors named in the text, in the order they are named.

    ``sure`` — named as a doctor («к Ярунину», «Кузнецов завтра работает?»), not just a
    surname that matches: «Кузнецов» alone may be the patient.
    """
    found: list[_Named] = []
    for doctor in doctors:
        for m in doctor.surname.finditer(t):
            before, after = t[: m.start()], t[m.end() :]
            own_name = bool(m.groupdict().get("lead") or m.groupdict().get("tail"))
            if _BEFORE_PATIENT.search(before):
                continue
            if not own_name and _OWN_NAME_AFTER.match(after):
                continue  # «Кузнецова Ивана Петровича»: another person
            clause_end = _CLAUSE_END.search(after)
            clause = before[-60:] + after[: clause_end.start() if clause_end else len(after)]
            if _BEFORE_DOCTOR.search(before) or own_name or _ABOUT_SHIFT.search(clause):
                found.append((doctor, m.start(), m.end(), True))
                continue
            word = _WORD_AFTER.match(after)
            if word and not _WISH_WORD.fullmatch(word["word"]):
                continue  # «Билалов Тимур упал»: somebody's own name
            found.append((doctor, m.start(), m.end(), False))
        if doctor.names is not None:
            for m in doctor.names.finditer(t):
                if _BEFORE_DOCTOR.search(t[: m.start()]):
                    found.append((doctor, m.start(), m.end(), True))
    for m in _TO_SOMEONE.finditer(t):  # «к кузницову»: one wrong letter after «к»
        word = m["name"]
        if len(word) < 6 or _OWN_NAME_AFTER.match(t, m.end("name")):
            continue
        for doctor in doctors:
            if any(_near(word, form) for form in doctor.forms):
                found.append((doctor, m.start("name"), m.end("name"), True))
                break
    unique: list[_Named] = []
    for item in sorted(found, key=lambda f: (f[1], not f[3])):
        if all(item[1] >= other[2] or item[2] <= other[1] for other in unique):
            unique.append(item)
    sure = [f for f in unique if f[3]]
    return sure or unique[:1]


def _unknown_doctor(t: str, original: str, booking: bool) -> tuple[int, int] | None:
    """«к Иванову», «к доктору Петровой»: a surname after «к» that is not in the list."""
    for m in _TO_SOMEONE.finditer(t):
        word = m["name"]
        if word in _NOT_SURNAMES or _AFTER_ADJECTIVE.match(t, m.end("name")):
            continue
        capital = original[m.start("name")].isupper()
        surname_like = len(word) >= 6 and _SURNAME_DATIVE.match(word) is not None
        if (surname_like and (capital or booking or m["title"])) or (m["title"] and capital):
            return m.start("name"), m.end("name")
    return None


def _clauses(t: str) -> list[tuple[int, int]]:
    spans, start = [], 0
    for m in _CLAUSE_END.finditer(t):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(t)))
    return spans


def parse(
    text: str,
    doctors: tuple[str, ...] | list[str],
    today: date,
    *,
    context: Context = "free",
) -> Parsed:
    """The wish in a message.

    ``context``: «time» — the patient is choosing the time, any word about it counts;
    «strict» — the patient is answering a scenario question, only an explicit request
    counts («на завтра», «запишите к Кузнецову»); «free» — any other text.
    """
    t = _norm(text)
    if len(t) != len(text):  # an exotic letter changed the length: positions would drift
        text = t
    known = tuple(_Doctor.of(d) for d in doctors)
    taken: list[tuple[int, int]] = []
    imperative = _IMPERATIVE.search(t) is not None
    booking_word = _BOOKING_WORD.search(t) is not None

    named = _named_doctors(t, known)
    unknown = None
    if named:
        taken += [(start, end) for _, start, end, _ in named]
    else:
        unknown = _unknown_doctor(t, text, booking_word)
        if unknown is not None:
            taken.append(unknown)

    mentions = _days(t, today, taken) + _times(t, taken)
    clauses = _clauses(t)
    events = [m.start() for m in _EVENT.finditer(t)]
    cues = [m.start() for m in _CUE.finditer(t)]
    doctor_at = named[0][1] if named and named[0][3] else None

    def counts(mention: _Mention) -> bool:
        if mention.explicit:
            return True
        start, end = next(c for c in clauses if c[0] <= mention.start <= c[1])
        marks = sorted(
            [(p, "event") for p in events if start <= p < end]
            + [(p, "wish") for p in cues if start <= p < end]
        )
        left = [kind for p, kind in marks if p < mention.start]
        right = [kind for p, kind in marks if p >= mention.end]
        if left:
            return left[-1] == "wish"
        if right:
            return right[0] == "wish"
        if context == "time":
            return True
        if context == "strict":
            return False
        named_here = doctor_at is not None and start <= doctor_at < end
        return mention.future or named_here or not events

    counted = sorted((m for m in mentions if counts(m)), key=lambda m: (not m.explicit, m.start))
    wish = Wish()
    for mention in counted:
        if mention.day is not None and wish.day is None:
            wish.day = mention.day
        window = wish.start or wish.end
        if mention.period is not None and not (wish.period or window):
            wish.period = mention.period
        elif mention.window is not None and not wish.has_time:
            wish.start, wish.end = mention.window
        elif mention.at is not None and not (wish.at or window):
            wish.at = mention.at

    # everything but the wish, the request words and the greeting
    consumed = [(m.start, m.end) for m in counted]
    consumed += [(start, end) for _, start, end, _ in named]
    if unknown is not None:
        consumed.append(unknown)
    chars = list(t)
    for start, end in consumed:
        chars[start:end] = " " * (end - start)
    words = re.findall(r"[а-яa-z0-9]+", "".join(chars))
    rest = [w for w in words if w not in _FILLERS and not re.fullmatch(r"[а-я]", w)]

    # a bare surname is a doctor only when the message is about nothing else
    if named and not named[0][3] and (rest or _EVENT.search(t)):
        named = []
    if named:
        wish.doctor = named[0][0].full

    question: Question | None = None
    if imperative:
        pass
    elif _FREE.search(t):
        question = "free"
    elif not booking_word:  # «можно записаться к Кузнецову?» is a request, not a question
        about_now = wish.day or wish.has_time or _NOW_OR_SHIFT.search(t)
        if named and _WHEN_DOCTOR.search(t):
            question = "doctor"
        elif not named and about_now and (_WHO.search(t) or _WHICH_DOCTOR.search(t)):
            question = "who"

    has_wish = not wish.empty or unknown is not None
    return Parsed(
        wish=wish,
        unknown_doctor=unknown is not None,
        booking=question is None and (imperative or booking_word),
        pure=has_wish and not rest and question is None,
        question=question,
        rest=" ".join(rest),
        doctor_spans=tuple((start, end) for _, start, end, _ in named),
    )


def mark_doctors(text: str, doctors: tuple[str, ...] | list[str]) -> str:
    """The doctor's name as a neutral label for DeepSeek: «к Ярунину» → «к [ВРАЧ]».

    The label tells route that the patient asks for a doctor; without it the masking would
    turn the surname into «[ИМЯ]» like any other name.
    """
    if len(_norm(text)) != len(text):
        return text
    spans = parse(text, doctors, date.today(), context="time").doctor_spans
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + DOCTOR_LABEL + text[end:]
    return text
