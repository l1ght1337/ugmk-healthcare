"""Several patients in one message: who they are, how old, what happened to each.

«Маша 7 лет руку ушибла, Ваня 10 лет ногу подвернул», «у меня запястье, у сына колено»,
«сначала для себя, потом сына». Read by code, before masking and before DeepSeek: a first
name, a word for a relative or the writer, with the age and the part of the message about
this person. The bot books one at a time: it asks who is first, leads the scenario with this
person's part only, and offers the next one after the booking card by name («Записать Ваню»).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from app.flow import topics
from app.flow.age import parse_age_months
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


def find_people(text: str) -> list[Person]:
    """The patients a message names, in the order they are named.

    A first name counts only with an age or an injury next to it («Маше 7 лет», «Ваня ногу
    подвернул»): «запишите к Андрею Александровичу» names a doctor. A part of a sentence with
    no person in it goes on about the one before («у меня запястье болит, похоже перелом»).
    """
    found: list[Person] = []
    for sentence in _SENTENCE.split(text):
        current: Person | None = None
        for clause in _CLAUSE.split(sentence):
            if not clause.strip():
                continue
            marker = _marker(clause)
            if marker is None:
                more = _complaint(clause, None)
                if current is not None and more and _about_injury(more):
                    current.complaint = ", ".join(p for p in (current.complaint, more) if p)
                continue
            name, audience, span, together = marker
            low = _norm(clause)
            has_age = re.search(r"\b\d{1,2}\s*(?:лет|года|год|годика|месяц\w*|мес)?\b", low)
            age = parse_age_months(low) if has_age else None
            if age is None and has_age and (bare := re.search(r"\b(\d{1,2})\b", low)):
                age = int(bare[1]) * 12  # «Ване 10»
            complaint = _complaint(clause, span)
            if not _about_injury(complaint) or (together and "мы" not in together.lower()):
                complaint = ""  # «упали с сыном на катке»: who was hurt is not said here
            if audience is None:  # a first name: a patient only with an age or an injury
                if age is None and not complaint:
                    current = None
                    continue
                audience = None if age is None else "child" if age < ADULT_FROM_MONTHS else "adult"
            if audience == "self":
                age = None  # the writer's age is not asked: «для себя» is an adult
            person = Person(name=name, audience=audience, age_months=age, complaint=complaint)  # type: ignore[arg-type]
            around = low[: span[0]] + " " + low[span[1] :]
            if name and _ME_TOO.search(around) and not any(not p.name for p in found):
                found.append(Person(name="", audience="self"))  # «запишите меня с ребёнком»
            if together and "мы" in together.lower() and not any(not p.name for p in found):
                # «мы с мужем подвернули ногу»: the writer too, with the same words
                found.append(Person(name="", audience="self", complaint=complaint))
            earlier = next((p for p in found if p.same(person)), None)
            if earlier is None:
                found.append(person)
                current = person
            else:  # named again: what is new is added
                earlier.age_months = earlier.age_months or person.age_months
                earlier.audience = earlier.audience or person.audience
                if person.complaint and person.complaint not in earlier.complaint:
                    parts = (earlier.complaint, person.complaint)
                    earlier.complaint = ", ".join(p for p in parts if p)
                current = earlier
    return found


def named_in(text: str, people: list[Person]) -> int | None:
    """Which of these patients a short answer names: «сначала Ваню», «с сына», «меня»."""
    low = _norm(text)
    for at, person in enumerate(people):
        if not person.name:
            if re.search(r"\b(?:меня|себя|я|мне)\b", low):
                return at
            continue
        patterns = [p for p, name, _ in _RELATIVES if name == person.name]
        patterns += [p for name, p in _NAMES if name == person.name]
        if any(p.search(low if p.flags & re.IGNORECASE else text) for p in patterns):
            return at
    return None


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
