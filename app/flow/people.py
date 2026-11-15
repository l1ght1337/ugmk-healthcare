"""Several patients in one message: who they are, how old, what happened to each.

«Маша 7 лет руку ушибла, Ваня 10 лет ногу подвернул», «у меня запястье, у сына колено»,
«сначала для себя, потом сына». Read by code, before masking and before DeepSeek: a first
name, a word for a relative or the writer, with the age and the part of the message about
this person. The bot books one at a time: it asks who is first, leads the scenario with this
person's part only, and offers the next one after the booking card by name («Записать Ваню»).

The name, the age and the injury may stand in three parts of a sentence: «Маша, 7 лет, руку
ушибла». What a message says about nobody in particular («мы сегодня с горки упали», «к врачу
ещё не обращались») is said about all of them (``shared``): it answers the scenario questions
of each.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from app.flow import topics
from app.flow.age import parse_age_months
from app.flow.commands import YES, find_command, find_small_talk, names_several
from app.flow.names import FIRST_NAMES, stem_pattern

ADULT_FROM_MONTHS = 18 * 12
# A word for a relative → how the bot calls the person (nominative) and who it is.
RELATIVES: tuple[tuple[str, str, Literal["child", "adult"]], ...] = (
    (r"сын\w*|сыноч\w+", "сын", "child"),
    (r"дочк\w+|дочурк\w+", "дочка", "child"),
    (r"дочь|дочер\w+", "дочь", "child"),
    (r"ребен\w+|ребеноч\w+", "ребёнок", "child"),
    (r"внучк\w+", "внучка", "child"),
    (r"внук\w*", "внук", "child"),
    (r"муж\w*|супруг(?:а|у|ом|е)?", "муж", "adult"),
    (r"жен(?:а|у|ы|е|ой)|супруг(?:а|у|и|е|ой)", "жена", "adult"),
    (r"мам\w+|мать|матер\w+", "мама", "adult"),
    (r"пап\w+|отец|отц\w+", "папа", "adult"),
    (r"бабушк\w+", "бабушка", "adult"),
    (r"дедушк\w+", "дедушка", "adult"),
)
MALE = frozenset({"сын", "внук", "муж", "папа", "дедушка"})  # «запись мужа» is not a woman's
FEMALE = frozenset({"дочка", "дочь", "внучка", "жена", "мама", "бабушка"})
ACCUSATIVE = {
    "сын": "сына", "дочка": "дочку", "дочь": "дочь", "ребёнок": "ребёнка", "внучка": "внучку",
    "внук": "внука", "муж": "мужа", "жена": "жену", "мама": "маму", "папа": "папу",
    "бабушка": "бабушку", "дедушка": "дедушку", "Лев": "Льва", "Павел": "Павла", "Пётр": "Петра",
    "Любовь": "Любовь",
}  # fmt: skip
_BEFORE = r"(?:(?P<with>мы\s+с|с)\s+|у\s+|для\s+)?(?:мо\w+\s+|наш\w+\s+)?"
_NAMES = tuple(
    (name, re.compile(rf"\b{_BEFORE}{stem_pattern(name).replace('ё', '[её]')}\b", re.IGNORECASE))
    for name in sorted(FIRST_NAMES, key=len, reverse=True)
)
_RELATIVES = tuple(
    (re.compile(rf"\b{_BEFORE}(?:{stems})\b", re.IGNORECASE), name, audience)
    for stems, name, audience in RELATIVES
)
# The writer as a patient: «у меня…», «для себя», «сначала меня», «мне 44».
_SELF = re.compile(r"\bу\s+меня\b|\bдля\s+себя\b|\bсебя\b|(?<!у\s)\bменя\b|\bмне\s+\d{1,2}\b")
# «я повредил запястье», «я упал», «мне прищемило палец»: the writer, by any word of an injury
_ME = re.compile(r"\b(?:я|мне)\b")
# «вы меня запишете?», «меня примут?»: a question whether the writer is taken — not «I am
# hurt too», and not one more patient
_NOT_ME = re.compile(r"\bвы\s+меня\b|\bменя\s+(?:\w+\s+)?(?:запишете|запишут|примете|примут)\b")
_ME_TOO = re.compile(r"(?<!у\s)\bменя\b|\bсебя\b")  # next to a relative, in the same words
# «у меня двое детей упали», «у меня дети с горки упали»: whose they are, not who is hurt
_MINE = re.compile(
    r"\bу\s+меня\s+(?:двое|трое|оба|обе|дети|детей|ребята|ребят|близнец\w*|мальчик\w*|девочк\w*)\b"
)
_BOOKING_WORD = re.compile(r"\bзапи[сш]\w*")  # «надо записать», «запишите», «на запись»
# «и сына тоже запишите», «ещё мужа запишите потом»: one more patient, not another one instead
_ALSO = re.compile(r"\b(?:тоже|еще|также|заодно|следом|потом)\b")
# what may stand around a first name in a request to book him: «запишите ещё Ваню на 14»
_REQUEST_WORD = re.compile(
    r"\b(?:запи[сш]\w*|тоже|еще|также|заодно|следом|потом|пожалуйста|надо|нужно|можно|"
    r"и|а|бы|ли|на|в|к|\d+)\b"
)
_SENTENCE = re.compile(r"[.!?;\n]+")
_CLAUSE = re.compile(r"[,:]|\s—\s|\s(?:а|и|но|потом|затем)\s")
# An age is a number with its word: «7 лет», «3 года», «4 мес». A bare number is not one —
# «мужа на 14:30», «сына в 9 утра», «через 2 часа» are times, and «Ване 10» is asked about
_AGE = re.compile(r"\b\d{1,2}\s*(?:лет|года|год|годика|месяц\w*|мес)\b")
# a part of a sentence that is an age and nothing else: «7 лет», «ему 10 лет», «ей 4 месяца»
_AGE_ALONE = re.compile(r"^(?:(?:ей|ему)\s+)?\d{1,2}\s*(?:лет|года|год|годика|месяц\w*|мес)$")
_NUMBER_ALONE = re.compile(r"\s*\d{1,2}\s*")  # «Ване 10», «Маша, 7,»: no word — not an age
_HURT = re.compile(
    r"\b(?:упал\w*|ударил\w*|ушиб\w*|сломал\w*|порезал\w*|обжег\w*|обожгл\w*|подвернул\w*|"
    r"вывихнул\w*|поранил\w*|наступил\w*|прищемил\w*|повредил\w*|укусил\w*|потянул\w*|"
    r"бол\w+|перелом\w*|вывих\w*|ожог\w*|порез\w*|ран[аыуе]|укус\w*|растяжени\w*|опух\w+|отек\w*)\b"
)
_FILLER = re.compile(
    r"^(?:а|и|но|у|для|вот|еще|тоже|сначала|сперва|потом|затем|первой|первым|она|он|его|ее|"
    r"надо|нужно|будет|записать|запишите|запишем|запиши|первая|первый|мы|тоже)\b\s*",
    re.IGNORECASE,
)


class Person(BaseModel):
    """A patient named in the conversation, not booked yet."""

    name: str  # «Маша», «сын», «муж»; empty — the one who writes
    audience: Literal["child", "adult", "self"] | None = None  # None: the bot asks
    age_months: int | None = None
    complaint: str = ""  # the part of the message about this person: «ногу подвернул»

    @property
    def whom(self) -> str:
        """Accusative, for «Записать Ваню», «Записываю сына: …», «Кого записать первым?»."""
        if not self.name:
            return "меня"
        if self.name in ACCUSATIVE:
            return ACCUSATIVE[self.name]
        if self.name[-1] == "а":
            return self.name[:-1] + "у"
        if self.name[-1] == "я":
            return self.name[:-1] + "ю"
        if self.name[-1] in "йь":
            return self.name[:-1] + "я"
        return self.name + "а"

    def same(self, other: Person) -> bool:
        return self.name == other.name


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def _marker(clause: str) -> tuple[str, str | None, tuple[int, int], str | None] | None:
    """Who a clause is about: the name, the audience by the word, where the word stands, and
    «с» / «мы с» before it («упали с сыном», «мы с мужем подвернули ногу»)."""
    low = _norm(clause)
    for pattern, name, audience in _RELATIVES:
        if found := pattern.search(low):
            return name, audience, found.span(), found["with"]
    for name, pattern in _NAMES:
        if found := pattern.search(clause):
            return name, None, found.span(), found["with"]
    if (found := _SELF.search(low)) and not _MINE.search(low) and not _NOT_ME.search(low):
        return "", "self", found.span(), None
    if (found := _ME.search(low)) and _about_injury(low[: found.start()] + low[found.end() :]):
        return "", "self", found.span(), None  # «я повредил запястье»: hurt, so a patient
    return None


def _complaint(clause: str, span: tuple[int, int] | None) -> str:
    """What happened, without the word for the person and the age: «руку ушибла»."""
    text = clause if span is None else clause[: span[0]] + " " + clause[span[1] :]
    text = _AGE.sub(" ", text) if span is not None else text
    text = " ".join(text.split()).strip(" ,.:—-")
    while (shorter := _FILLER.sub("", text, count=1)) != text:
        text = shorter.strip(" ,.:—-")
    return text


def _about_injury(text: str) -> bool:
    low = _norm(text)
    return bool(_HURT.search(low) or topics.guess_area(low) or topics.guess_candidates(low))


def _age_alone(clause: str) -> int | None:
    """«7 лет», «ему 10 лет» between two commas: the age in months — or None."""
    low = _norm(clause).strip(" .—-")
    if not _AGE_ALONE.match(low):
        return None
    return parse_age_months(low)


def _by_age(months: int) -> Literal["child", "adult"]:
    return "child" if months < ADULT_FROM_MONTHS else "adult"


def _clauses(sentence: str) -> list[str]:
    return [clause for clause in _CLAUSE.split(sentence) if clause.strip()]


def find_people(text: str) -> list[Person]:
    """The patients a message names, in the order they are named.

    A first name counts only with an age or an injury next to it («Маше 7 лет», «Ваня ногу
    подвернул», «Маша, 7 лет, руку ушибла»): «запишите к Андрею Александровичу» names a
    doctor. An age is a number with its word («лет», «год», «мес…»): «мужа на 14:30» has
    none, «а Ване 10» names Ваня with no age. A part of a sentence with no person in it goes
    on about the one before («у меня запястье болит, похоже перелом»); an age alone is the
    age of the one named just before it («сын, 12 лет, упал»).
    """
    found: list[Person] = []

    def add(person: Person) -> Person:
        earlier = next((p for p in found if p.same(person)), None)
        if earlier is None:
            found.append(person)
            return person
        # named again: what is new is added
        earlier.age_months = earlier.age_months or person.age_months
        earlier.audience = earlier.audience or person.audience
        if person.complaint and person.complaint not in earlier.complaint:
            earlier.complaint = ", ".join(p for p in (earlier.complaint, person.complaint) if p)
        return earlier

    for sentence in _SENTENCE.split(text):
        current: Person | None = None
        waits: str | None = None  # «Маша,» — a first name alone: an age or an injury is next
        ageless: Person | None = None  # named in the part just before, with nothing else in it
        for clause in _clauses(sentence):
            marker = _marker(clause)
            if marker is None:
                if _NUMBER_ALONE.fullmatch(clause):
                    continue  # «Маша, 7, руку ушибла»: a number with no word says nothing
                name, after, waits, ageless = waits, ageless, None, None
                age = _age_alone(clause)
                if age is not None and name is not None:  # «Маша, 7 лет»
                    current = add(Person(name=name, audience=_by_age(age), age_months=age))
                elif age is not None and after is not None and after.audience != "self":
                    after.age_months = after.age_months or age  # «сын, 12 лет»
                elif names_several(clause):
                    pass  # «оба упали», «двое детей»: about nobody in particular
                elif (more := _complaint(clause, None)) and _about_injury(more):
                    if name is not None:  # «Маша, руку ушибла»
                        current = add(Person(name=name, complaint=more))
                    elif current is not None:
                        current.complaint = ", ".join(p for p in (current.complaint, more) if p)
                continue
            waits = ageless = None
            name, audience, span, together = marker
            low = _norm(clause)
            age = parse_age_months(low) if _AGE.search(low) else None
            # «а Ване 10»: he is named; the number has no word, so his age is asked
            numbered = _NUMBER_ALONE.fullmatch(low[span[1] :]) is not None
            said = _complaint(clause, span)
            complaint = said
            if not _about_injury(complaint) or (together and "мы" not in together.lower()):
                complaint = ""  # «упали с сыном на катке»: who was hurt is not said here
            if audience is None:  # a first name: a patient only with an age or an injury
                if age is None and not complaint and not numbered:
                    current = None
                    if not said and not together:  # the name alone: «Маша, 7 лет, руку ушибла»
                        waits = name
                    continue
                audience = None if age is None else _by_age(age)
            if audience == "self":
                age = None  # the writer's age is not asked: «для себя» is an adult
            person = Person(name=name, audience=audience, age_months=age, complaint=complaint)  # type: ignore[arg-type]
            around = low[: span[0]] + " " + low[span[1] :]
            me_too = _ME_TOO.search(around) and not _NOT_ME.search(low)
            if name and me_too and not any(not p.name for p in found):
                found.append(Person(name="", audience="self"))  # «запишите меня с ребёнком»
            if together and "мы" in together.lower() and not any(not p.name for p in found):
                # «мы с мужем подвернули ногу»: the writer too, with the same words
                found.append(Person(name="", audience="self", complaint=complaint))
            current = add(person)
            if age is None and not complaint:
                ageless = current  # «сын, 12 лет»: the age may stand in the next part
            if together and "мы" not in together.lower():
                current = None  # «гуляла с сыном, упала и сломала руку»: not he was hurt
    return found


def _aside(clause: str) -> bool:
    """A part that says nothing about the injuries: a greeting, a word or two, a request to
    book and its condition, a count of the patients («здравствуйте», «пожалуйста», «надо
    обоих записать», «если можно», «двое детей», «обоих надо к врачу»). «У меня двое детей
    сегодня с горки упали» says how and when: it stays. A sentence is read part by part:
    «надо обоих записать к врачу, мы ещё не обращались никуда» keeps its second part."""
    low = _norm(clause).strip()
    return (
        len(low.split()) < 2
        or low.startswith("если ")  # «…, если можно»: the condition of a request
        or find_small_talk(clause) is not None
        or find_command(clause) == "book"
        or _BOOKING_WORD.search(low) is not None
        or (names_several(clause) and not _about_injury(clause))
    )


def shared(text: str) -> list[str]:
    """What a message says about all the patients it is about, not about one of them: the
    sentences that name nobody, and the parts of a sentence before the first one named.

    «Мы сегодня с горки упали, двое детей. Маша, 7 лет, руку ушибла… К врачу ещё не
    обращались» → «Мы сегодня с горки упали», «К врачу ещё не обращались». «Сегодня на катке
    упали с сыном» does not say who was hurt: it is about both, without the words «с сыном».
    """
    parts: list[str] = []
    for sentence in _SENTENCE.split(text):
        clauses = _clauses(sentence)
        markers = [_marker(clause) for clause in clauses]
        kept: list[str] = []
        as_written = True  # nothing is cut out of the sentence
        for clause, marker in zip(clauses, markers, strict=True):
            if marker is not None:
                as_written = False
                _, _, span, together = marker
                if not together or "мы" in together.lower():
                    break  # from here on the sentence is about one of them
                clause = clause[: span[0]] + " " + clause[span[1] :]
            if _aside(clause):
                as_written = False
            elif said := " ".join(clause.split()).strip(" —-"):
                kept.append(said)
        if kept:
            parts.append(sentence.strip(" —-") if as_written else ", ".join(kept))
    return parts


def _asked_for(clause: str) -> bool:
    """The part names somebody a request may be about: a relative, the writer — or a first
    name with nothing but the request around it. «Захарова Игоря Семёновича» is the full
    name of the one named before («и мужа, Захарова Игоря Семёновича, тоже запишите»)."""
    marker = _marker(clause)
    if marker is None:
        return False
    _, audience, span, _ = marker
    if audience is not None:
        return True
    around = _norm(clause[: span[0]] + " " + clause[span[1] :])
    return not _REQUEST_WORD.sub(" ", around).strip(" ,.:—-?!")


def asked_too(text: str) -> list[Person]:
    """Who else is asked to be booked: «и сына тоже запишите», «ещё Ваню запишите, ему 10
    лет, ногу подвернул», «и меня тоже». A sentence with a request to book, «тоже» or «ещё»
    and a person in it; what follows about him — the age, the injury — is his. «Сына
    запишите» without «тоже» says who the booking is for, not that he is one more."""
    found: list[Person] = []
    for sentence in _SENTENCE.split(text):
        low = _norm(sentence)
        if not (_BOOKING_WORD.search(low) and _ALSO.search(low)):
            continue
        clauses = _clauses(sentence)
        # the part with the request, or the nearest part before it that names somebody; a
        # first name next to a surname or a patronymic is a full name, not one more person
        asks = [n for n, c in enumerate(clauses) if _BOOKING_WORD.search(_norm(c))]
        at = next((n for n in range(asks[-1], -1, -1) if _asked_for(clauses[n])), None)
        if at is None:
            continue
        name, audience, span, _ = _marker(clauses[at])  # type: ignore[misc]
        # the word for the person alone, then what the sentence says after it
        about = ", ".join([clauses[at][span[0] : span[1]], *clauses[at + 1 :]])
        known = next((p for p in find_people(about) if p.name == name), None)
        person = known or Person(name=name, audience=audience)  # type: ignore[arg-type]
        if not any(p.same(person) for p in found):
            found.append(person)
    return found


def named_in(text: str, people: list[Person]) -> int | None:
    """Which of these patients a short answer names: «сначала Ваню», «с сына», «меня». Two
    are named — the one that stands first: «сначала Ваню, потом Машу»."""
    low = _norm(text)
    places: list[tuple[int, int]] = []
    for at, person in enumerate(people):
        if not person.name:
            patterns = [re.compile(r"\b(?:меня|себя|я|мне)\b")]
        else:
            patterns = [p for p, name, _ in _RELATIVES if name == person.name]
            patterns += [p for name, p in _NAMES if name == person.name]
        starts = [m.start() for p in patterns if (m := p.search(low))]
        if starts:
            places.append((min(starts), at))
    return min(places)[1] if places else None


_AGREES = re.compile(
    rf"^(?:{'|'.join(sorted(YES))}|хорошо|давайте|давай|записывайте|запишите)\b", re.IGNORECASE
)
_NOT_NOW = re.compile(r"\b(?:не|нет|потом|позже)\b")


def agrees_to(text: str, person: Person) -> bool:
    """«Да, давайте мужа» to «Теперь запишем второго пациента?»: «да» and the name or the
    relation of the one offered, in the first sentence — the button «Записать мужа». What
    follows («…только сначала уточните») does not take the answer back."""
    first = next((part for part in _SENTENCE.split(text) if part.strip()), "")
    low = _norm(first).strip(" ,")
    if not _AGREES.match(low) or _NOT_NOW.search(low):
        return False
    return named_in(first, [person]) is not None


def merge(known: list[Person], named: list[Person]) -> list[Person]:
    """What later messages add about the patients who wait: an age, what happened."""
    for person in named:
        earlier = next((p for p in known if p.same(person)), None)
        if earlier is None:
            continue
        earlier.age_months = person.age_months or earlier.age_months
        earlier.audience = earlier.audience or person.audience
        if person.complaint and person.complaint not in earlier.complaint:
            earlier.complaint = ", ".join(p for p in (earlier.complaint, person.complaint) if p)
    return known
