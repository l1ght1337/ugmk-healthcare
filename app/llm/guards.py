"""Checks on LLM text before a patient sees it: no new facts, no echoed placeholders."""

from __future__ import annotations

import re

from app.flow.loader import digits
from app.llm.masking import PLACEHOLDERS

MAX_CLARIFY_CHARS = 300


def _suspicious(text: str) -> bool:
    lowered = text.lower()
    return any(p in text for p in PLACEHOLDERS) or "http" in lowered or "@" in text


def safe_clarify(text: str | None, template: str) -> str | None:
    """A rephrased question, or None if it adds numbers or anything not in the script phrase."""
    if not text:
        return None
    text = text.strip()
    if len(text) > MAX_CLARIFY_CHARS or _suspicious(text) or digits(text) - digits(template):
        return None
    return text


# Words of advice: «лучше не ждать до утра и вызвать скорую», «рекомендую приложить холод».
# The bot gives none (rule 1); an answer may say such a thing only in the reference's own words.
_ADVICE = re.compile(
    r"\bлучше\s+(?:не\s+)?(?:ждать|вызвать|обратиться|ехать|поехать|показать\w*|приложить|сразу|"
    r"всего)\b"
    r"|\b(?:рекомендую|рекомендуем|рекомендуется|советую|советуем)\b"
    r"|\bне\s+(?:ждите|ждать|откладывайте|затягивайте|тяните)\b"
    r"|\b(?:вызовите|вызывайте|вызвать)\s+(?:\w+\s+)?скорую\b"
    r"|\b(?:приложите|примите|выпейте|промойте|обработайте|наложите|зафиксируйте|обездвижьте)\b"
    r"|\bпри\s+опасениях\b"
)


def _plain(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


def advises(answer: str, reference: str) -> bool:
    """The answer gives advice the reference does not give in the same words."""
    known = _plain(reference)
    return any(m.group() not in known for m in _ADVICE.finditer(_plain(answer)))


def grounded_prefix(partial: str, reference: str) -> bool:
    """A streamed piece of an answer may be shown: only complete numbers are checked."""
    if any(p in partial for p in PLACEHOLDERS) or "http" in partial.lower():
        return False
    if advises(partial, reference):
        return False
    complete = set(re.findall(r"\d+(?=\D)", partial))
    return complete <= digits(reference)


def grounded(answer: str, reference: str) -> bool:
    """Every number in the answer (address, hours, price) must come from the reference text,
    and the answer gives no advice of its own."""
    if any(p in answer for p in PLACEHOLDERS):
        return False
    if "http" in answer.lower() and "http" not in reference.lower():
        return False
    if advises(answer, reference):
        return False
    return digits(answer) <= digits(reference)
