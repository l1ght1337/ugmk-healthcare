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
from app.flow.commands import find_command, find_small_talk, names_several
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
_ME_TOO = re.compile(r"(?<!у\s)\bменя\b|\bсебя\b")  # next to a relative, in the same words
_SENTENCE = re.compile(r"[.!?;\n]+")
_CLAUSE = re.compile(r"[,:]|\s—\s|\s(?:а|и|но|потом|затем)\s")
_AGE = re.compile(r"\b\d{1,2}(?:\s*(?:лет|года|год|годика|месяц\w*|мес))?\b")
# a part of a sentence that is an age and nothing else: «7 лет», «ему 10», «ей 4 месяца»
_AGE_ALONE = re.compile(
    r"^(?:(?:ей|ему)\s+\d{1,2}(?:\s*(?:лет|года|год|годика|месяц\w*|мес))?"
    r"|\d{1,2}\s*(?:лет|года|год|годика|месяц\w*|мес))$"
)
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
    if found := _SELF.search(low):
        return "", "self", found.span(), None
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
    """«7 лет», «ему 10» between two commas: the age in months — or None."""
    low = _norm(clause).strip(" .—-")
    if not _AGE_ALONE.match(low):
        return None
    months = parse_age_months(low)
    return months if months is not None else int(re.search(r"\d{1,2}", low)[0]) * 12  # type: ignore[index]


def _by_age(months: int) -> Literal["child", "adult"]:
    return "child" if months < ADULT_FROM_MONTHS else "adult"


def _clauses(sentence: str) -> list[str]:
    return [clause for clause in _CLAUSE.split(sentence) if clause.strip()]


def find_people(text: str) -> list[Person]:
    """The patients a message names, in the order they are named.

    A first name counts only with an age or an injury next to it («Маше 7 лет», «Ваня ногу
    подвернул», «Маша, 7 лет, руку ушибла»): «запишите к Андрею Александровичу» names a
    doctor. A part of a sentence with no person in it goes on about the one before («у меня
    запястье болит, похоже перелом»); an age alone is the age of the one named just before
    it («сын, 12 лет, упал»).
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
            has_age = re.search(r"\b\d{1,2}\s*(?:лет|года|год|годика|месяц\w*|мес)?\b", low)
            age = parse_age_months(low) if has_age else None
            if age is None and has_age and (bare := re.search(r"\b(\d{1,2})\b", low)):
                age = int(bare[1]) * 12  # «Ване 10»
            said = _complaint(clause, span)
            complaint = said
            if not _about_injury(complaint) or (together and "мы" not in together.lower()):
                complaint = ""  # «упали с сыном на катке»: who was hurt is not said here
            if audience is None:  # a first name: a patient only with an age or an injury
                if age is None and not complaint:
                    current = None
                    if not said and not together:  # the name alone: «Маша, 7 лет, руку ушибла»
                        waits = name
                    continue
                audience = None if age is None else _by_age(age)
            if audience == "self":
                age = None  # the writer's age is not asked: «для себя» is an adult
            person = Person(name=name, audience=audience, age_months=age, complaint=complaint)  # type: ignore[arg-type]
            around = low[: span[0]] + " " + low[span[1] :]
            if name and _ME_TOO.search(around) and not any(not p.name for p in found):
                found.append(Person(name="", audience="self"))  # «запишите меня с ребёнком»
            if together and "мы" in together.lower() and not any(not p.name for p in found):
                # «мы с мужем подвернули ногу»: the writer too, with the same words
                found.append(Person(name="", audience="self", complaint=complaint))
            current = add(person)
            if age is None and not complaint:
                ageless = current  # «сын, 12 лет»: the age may stand in the next part
    return found


def _aside(clause: str) -> bool:
    """A part that says nothing about the injuries: a greeting, a count of the patients, a
    request to book («здравствуйте», «двое детей», «надо обоих записать»)."""
    return (
        names_several(clause)
        or find_small_talk(clause) is not None
        or find_command(clause) == "book"
    )


def shared(text: str) -> list[str]:
    """What a message says about all the patients it is about, not about one of them: the
    sentences that name nobody, and the parts of a sentence before the first one named.

    «Мы сегодня с горки упали, двое детей. Маша, 7 лет, руку ушибла… К врачу ещё не
    обращались» → «Мы сегодня с горки упали», «К врачу ещё не обращались».
    """
    parts: list[str] = []
    for sentence in _SENTENCE.split(text):
        clauses = _clauses(sentence)
        if not clauses:
            continue
        first = next((n for n, clause in enumerate(clauses) if _marker(clause)), None)
        before = clauses if first is None else clauses[:first]
        kept = [clause.strip(" —-") for clause in before if not _aside(clause)]
        kept = [clause for clause in kept if clause]
        if first is None and len(kept) == len(clauses):
            parts.append(sentence.strip(" —-"))  # the sentence as it was written
        elif kept:
            parts.append(", ".join(kept))
    return parts


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
