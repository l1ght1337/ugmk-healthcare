"""Several things asked for in one message: a booking, a move, a cancellation, a question.

«Отмените запись мужа, а мою перенесите на вечер» is two things, and each has its own «кто»
and its own «когда»: «на вечер» is the move's, the husband's booking is the one to cancel.
A message is cut into parts by the words of app/flow/commands.py («отмените», «перенесите»,
«запишите», «хочу перенести», «надо отменить»); what a part names — a person, a day, a time —
stays in it. DeepSeek is not asked.

* A complaint joins the booking of the same person wherever it stands: «Сын руку ушиб,
  запишите его, а мою перенесите на завтра». A complaint of somebody else with no word of a
  booking is a booking too («…а сын руку ушиб»).
* A part with a negation is not a thing to do: «мужа не записывайте» is a refusal.
* «Отмени и запиши на воскресенье» — the old time for a new one — is one move, as before.
* A day or a time that stands in no part («…, на завтра, …») is nobody's: not guessed.

Only a message with a move or a cancellation and something else is cut: bookings alone are
the queue of patients (app/flow/people.py), one thing goes the way it went before.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel

from app.flow.commands import ActionKind, negates, request_in
from app.flow.people import Person, find_people, for_them, hurt, refused
from app.flow.wishes import Parsed, Wish

QUEUE_MAX = 3  # more things than this are not done in a row: the patient says which first


class Action(BaseModel):
    """One thing a message asks for."""

    kind: ActionKind
    who: Person | None = None  # named in the part: «сына», «мужа»; empty name — the writer
    mine: bool = False  # «мою», «свою», «меня»: the writer's own booking
    said: str = ""  # the words of the part: its day, its time, its doctor
    wish: Wish | None = None
    # «а мою перенесите»: not the booking of those the message names for other things
    not_of: list[Person] = []


@dataclass
class Plan:
    actions: list[Action]
    refused: list[Person] = field(default_factory=list)  # «мужа не записывайте»
    question: str | None = None  # «…И сколько стоит приём?»
    unclear: bool = False  # a day or a time that stands in no part: whose is it?


_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")
_CLAUSE = re.compile(r"[,:]|\s—\s|\s(?:а|и|но|потом|затем)\s")
_MINE = re.compile(r"\b(?:мо[юеяйи]\w*|сво[юеяйи]\w*|наш[уеаи]\w*|меня|мне|себя|я)\b")
_INSTEAD = re.compile(r"\bвместо\s+(?:меня|себя)\b")
_THEM = re.compile(r"\b(?:он|она|его|ее|ему|ей|него|нее|ним|ней)\b")


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def _book(actions: list[Action], who: Person, clause: str, *, mine: bool = False) -> None:
    """A booking for this person: one for all the parts about him."""
    same = next((a for a in actions if a.kind == "book" and a.who and a.who.same(who)), None)
    if same is None:
        actions.append(Action(kind="book", who=who, mine=mine, said=clause))
    else:
        same.said = f"{same.said}, {clause}"


def split(text: str, read: Callable[[str], Parsed]) -> Plan | None:
    """The things a message asks for, in its order — or None: one thing, or bookings only.

    ``read`` reads the day, the time and the doctor of a part (app/flow/wishes.py with the
    clinic's doctors and clock)."""
    known = {p.name: p for p in find_people(text) if p.name}
    actions: list[Action] = []
    refusals: list[Person] = []
    loose: list[int] = []  # a «когда» in no part: how many things stood before it
    question: str | None = None
    last: Person | None = None  # the one named last: «он», «её»
    for sentence in _SENTENCE.split(text):
        plain: list[str] = []  # the parts that ask for nothing and name nothing
        clauses = [part.strip() for part in _CLAUSE.split(sentence) if part.strip()]
        whens: list[int] = []  # the parts of this sentence that are a day or a time
        only = True  # … and nothing else: «можно на вечер?», not «а ночью вы работаете?»
        for clause in clauses:
            low = _norm(clause)
            if said_no := refused(clause):
                refusals += [p for p in said_no if not any(p.same(r) for r in refusals)]
                last = said_no[0]
                continue
            if negates(clause):
                continue
            people = [p for p in find_people(clause) if p.name]
            who = people[0] if people else None
            if who is None and last is not None and _THEM.search(low):
                who = last
            last = who or last
            mine = who is None and _MINE.search(_INSTEAD.sub(" ", low)) is not None
            kind = request_in(clause)
            me = Person(name="", audience="self")
            if kind == "book" and (who is not None or mine):
                _book(actions, who or me, clause, mine=mine)
            elif kind is not None:
                actions.append(Action(kind=kind, who=who, mine=mine, said=clause))
            elif hurt(clause) and who is not None:
                _book(actions, who, clause)  # «сын руку ушиб»: his booking, wherever it stands
            elif hurt(clause) and mine and not any(a.mine for a in actions):
                _book(actions, me, clause, mine=True)  # «я руку ушибла»: the writer's booking
            elif hurt(clause) and actions:
                actions[-1].said += f", {clause}"  # the reason, or more about its patient
            elif who is not None and actions and for_them(low, who.name):
                # «…и запись мужа», «…и дочку»: the verb of the part before goes on
                actions.append(Action(kind=actions[-1].kind, who=who, said=clause))
            elif not (found := read(clause)).wish.empty:
                whens.append(len(actions))
                only = only and found.pure
                plain.append(clause)
            else:
                plain.append(clause)
        asks = len(plain) == len(clauses) and sentence.strip().endswith("?")
        if asks and not (whens and only):
            # a sentence of its own that asks for nothing to be done: answered, and its
            # «ночью» is not a time for a booking
            question = question or sentence.strip()
            continue
        loose += whens
        if actions and plain:
            actions[-1].said = ", ".join([actions[-1].said, *plain])  # the words go with it
    tail = len(actions)  # where a «когда» of no part may stand: after the last thing
    # «отмени и запиши на воскресенье»: the old time for a new one is a move, as before
    merged: list[Action] = []
    for action in actions:
        before = merged[-1] if merged and merged[-1].kind != "book" else None
        again = action.kind == "book" and before is not None and not hurt(action.said)
        if again and _same_patient(action, before):
            # «…и запишите на воскресенье», «отмените запись сына и запишите его на завтра»
            before.kind, before.said = "move", f"{before.said}, {action.said}"
        else:
            merged.append(action)
    actions = merged
    # the writer's own complaint next to a move of the writer's booking is why it is moved
    if any(a.mine and a.kind != "book" for a in actions):
        actions = [a for a in actions if not (a.kind == "book" and a.mine and _hint(a))]
    movers = [a for a in actions if a.kind != "book"]
    if not movers or len(actions) + len(refusals) + (question is not None) < 2:
        return None
    # a «когда» in no part is clear only after the last thing: «…, если можно на вечер»
    unclear = len(actions) > 1 and any(at != tail for at in loose)
    for action in actions:
        if action.who is not None and action.who.name in known:
            action.who = known[action.who.name]  # with the age and the injury of the message
        wish = read(action.said).wish
        action.wish = None if wish.empty or unclear else wish
        action.not_of = [
            a.who for a in actions if a is not action and a.who is not None and a.who.name
        ]
    return Plan(actions, refusals, question, unclear)


def _same_patient(booking: Action, before: Action) -> bool:
    """A booking with no injury right after a cancellation or a move is the new time of that
    very visit, if it names nobody or the same patient."""
    if booking.who is None or not booking.who.name:
        return booking.who is None or before.mine or before.who is None
    return before.who is not None and before.who.same(booking.who)


def _hint(action: Action) -> bool:
    """A booking of the writer made of a complaint alone: no «запишите меня» in it."""
    return request_in(action.said) is None
