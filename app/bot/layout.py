"""Button rows that are not cut off on a phone screen.

Labels up to ``SHORT`` characters go two in a row, longer ones get a row of their own
(Telegram shortens long labels in narrow columns with «…»). Times and days use fixed
grids with short labels.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from typing import Protocol

SHORT = 18  # «Растяжение или разрыв связок» (28) — one column; «Похоже на вывих» (15) — two
TIME_COLUMNS = 4
DAY_COLUMNS = 2


class Labeled(Protocol):
    @property
    def label(self) -> str: ...


def width(label: str) -> int:
    """How wide a label is on screen: an emoji takes two letters, joiners take none."""
    total = 0
    for ch in label:
        if ch in "\ufe0f\u200d" or unicodedata.category(ch) == "Mn":
            continue
        total += 2 if ord(ch) >= 0x1F000 or 0x2600 <= ord(ch) <= 0x27BF else 1
    return total


def pack[B: Labeled](buttons: Iterable[B], short: int = SHORT) -> list[list[B]]:
    """Two short buttons per row, a long one alone; the order is kept."""
    rows: list[list[B]] = []
    pending: B | None = None
    for button in buttons:
        if width(button.label) > short:
            if pending is not None:
                rows.append([pending])
                pending = None
            rows.append([button])
        elif pending is None:
            pending = button
        else:
            rows.append([pending, button])
            pending = None
    if pending is not None:
        rows.append([pending])
    return rows


def grid[B: Labeled](buttons: Sequence[B], columns: int) -> list[list[B]]:
    return [list(buttons[i : i + columns]) for i in range(0, len(buttons), columns)]
