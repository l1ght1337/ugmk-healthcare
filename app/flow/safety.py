"""Emergency pre-check, run on every free-text message before any scenario logic.

Matching is by whole phrases from config/clinic.yaml (never single words like «кровь»),
after lower-casing, «ё» → «е» and dropping punctuation. The bot does not assess the
patient's state: on a match it only answers «При угрозе жизни звоните 103 или 112».
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_NON_WORD = re.compile(r"[^\w]+")


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    return " ".join(_NON_WORD.sub(" ", text).split())


def find_emergency(text: str, phrases: Iterable[str]) -> str | None:
    """The first emergency phrase found in ``text`` as whole words, or None."""
    haystack = f" {normalize(text)} "
    for phrase in phrases:
        if f" {normalize(phrase)} " in haystack:
            return phrase
    return None
