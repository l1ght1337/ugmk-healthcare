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
from collections.abc import Collection, Iterable, Sequence
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


def sign_passed(
    text: str, said: Sequence[str], over: Iterable[Over], never: Collection[str]
) -> bool:
    """«Кровь остановилась» after «кровь не останавливается»: the patient says the sign has
    passed — in words of the kind of every phrase that was said. After a phrase of ``never``
    (consciousness, breathing) no words count; «кровь остановилась» does not answer «не
    дышит» said in the same conversation."""
    if not said or any(phrase in never for phrase in said):
        return False
    for group in over:
        if find_phrase(text, group.phrases) is None:
            continue
        if not group.after or all(phrase in group.after for phrase in said):
            return True
    return False
