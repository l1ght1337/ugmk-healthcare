"""Pre-checks run on every free-text message before any scenario logic or LLM call.

* crisis — words about suicide or self-harm (config/clinic.yaml, ``crisis``): a calm reply
  with a helpline and a separate note to the administrators;
* emergency — signs of a threat to life (``emergency``, phrases only, never single words
  like «кровь»): «При угрозе жизни звоните 103 или 112».

Matching is by whole words after lower-casing, «ё» → «е» and dropping punctuation. The bot
does not assess the patient's state.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Protocol

_NON_WORD = re.compile(r"[^\w]+")


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    return " ".join(_NON_WORD.sub(" ", text).split())


def find_phrase(text: str, phrases: Iterable[str]) -> str | None:
    """The first of ``phrases`` found in ``text`` as whole words, or None."""
    haystack = f" {normalize(text)} "
    for phrase in phrases:
        if f" {normalize(phrase)} " in haystack:
            return phrase
    return None


find_emergency = find_phrase


class Over(Protocol):
    """Words that a sign has passed, and the phrases they answer (config, EmergencyOver)."""

    phrases: Sequence[str]
    after: Sequence[str]


def sign_passed(text: str, said: Sequence[str], over: Iterable[Over]) -> bool:
    """«Кровь остановилась» after «кровь не останавливается»: the patient says the sign has
    passed, and every phrase that was said is one these words answer (``after``). After any
    other phrase — «не дышит», «судороги» — no words count."""
    if not said:
        return False
    return any(
        find_phrase(text, group.phrases) is not None
        and all(phrase in group.after for phrase in said)
        for group in over
    )
