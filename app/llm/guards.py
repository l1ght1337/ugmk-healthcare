"""Checks on LLM text before a patient sees it: no new facts, no echoed placeholders."""

from __future__ import annotations

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


def grounded(answer: str, reference: str) -> bool:
    """Every number in the answer (address, hours, price) must come from the reference text."""
    if any(p in answer for p in PLACEHOLDERS):
        return False
    if "http" in answer.lower() and "http" not in reference.lower():
        return False
    return digits(answer) <= digits(reference)
