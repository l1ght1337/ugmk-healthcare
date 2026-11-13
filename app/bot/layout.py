"""Button rows that are not cut off on a phone screen.

Labels up to ``SHORT`` characters go two in a row, longer ones get a row of their own
(Telegram shortens long labels in narrow columns with «…»). Times and days use fixed
grids with short labels.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

SHORT = 18  # «Растяжение или разрыв связок» (28) — one column; «Похоже на вывих» (15) — two
TIME_COLUMNS = 4
DAY_COLUMNS = 2


class Labeled(Protocol):
    @property
    def label(self) -> str: ...


def pack[B: Labeled](buttons: Iterable[B], short: int = SHORT) -> list[list[B]]:
    """Two short buttons per row, a long one alone; the order is kept."""
    rows: list[list[B]] = []
    pending: B | None = None
    for button in buttons:
        if len(button.label) > short:
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
