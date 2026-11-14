"""The journal of what the bot did not understand: data/misses.jsonl.

Every time the bot answers with a fallback phrase («У меня нет этой информации»,
«выберите кнопкой», «я помогаю только с…», «понимаю только текст», the hint after route)
or passes a question to an administrator, one line is written: the time, the step, the
kind of the answer and the patient's text after masking. No user id, no chat id, no name:
the journal is for finding gaps, not for reading dialogs. /misses in the administrators'
group shows the last lines and the counters of the last 24 hours.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.bot import texts

log = logging.getLogger(__name__)

# The kinds, as /misses shows them.
NO_INFO = "нет информации"
PICK = "выберите кнопкой"
OFFTOPIC = "не по теме"
NOT_OURS = "не наш профиль"
NOT_TEXT = "не текст"
HINT = "подсказка после route"
UNCLEAR = "не понял сообщение"
UNREAD = "не разобрал данные"
DOWN = "DeepSeek не ответил"
ADMIN = "передано администратору"

# The fallback phrase in a reply → the kind. The first that is found wins.
PHRASES: tuple[tuple[str, str], ...] = (
    (texts.INFO_UNKNOWN, NO_INFO),
    (texts.ASIDE_UNKNOWN, NO_INFO),
    (texts.INFO_DOWN, DOWN),
    (texts.OFFTOPIC, OFFTOPIC),
    (texts.OTHER_DEPARTMENT_NO_PHONE, OFFTOPIC),
    (texts.NOT_OUR_PROFILE, NOT_OURS),
    (texts.ONLY_TEXT, NOT_TEXT),
    (texts.HINT_TIME, HINT),
    (texts.HINT_BUTTONS, HINT),
    (texts.REQUEST_UNCLEAR, UNCLEAR),
    (texts.TOPIC_UNCLEAR, UNCLEAR),
    (texts.ASK_NAME_AGAIN, UNREAD),
    (texts.ASK_BIRTH_AGAIN, UNREAD),
    (texts.ASK_CONTACT_AGAIN, UNREAD),
    (texts.ASK_CHILD_AGE_AGAIN, UNREAD),
    (texts.PICK_BUTTON_SHORT, PICK),
    (texts.PICK_BUTTON, PICK),
    (texts.SAME_STEP, PICK),
)
LAST = 20  # /misses lists this many lines
TEXT_SHOWN = 100  # characters of the patient's text in /misses
DAY = timedelta(hours=24)


def kind_of(reply: str) -> str | None:
    """The kind of a fallback answer, or None for an ordinary reply."""
    return next((kind for phrase, kind in PHRASES if phrase in reply), None)


@dataclass(frozen=True)
class Miss:
    at: str  # ISO, with the time zone
    step: str  # the step the patient was on
    kind: str
    text: str  # the patient's text after masking; empty for a photo or a button


class MissLog:
    def __init__(self, path: Path | None) -> None:
        self.path = path

    def write(self, miss: Miss) -> None:
        """Append one line. A journal that cannot be written never breaks the dialog."""
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(miss), ensure_ascii=False) + "\n")
        except OSError:
            log.warning("cannot write the misses journal %s", self.path, exc_info=True)

    def read(self) -> list[Miss]:
        if self.path is None or not self.path.is_file():
            return []
        found = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                found.append(Miss(**json.loads(line)))
            except (ValueError, TypeError):
                continue  # a broken line is skipped
        return found

    def report(self, now: datetime, tz: ZoneInfo) -> str:
        """/misses: the counters of the last 24 hours and the last lines."""
        if self.path is None:
            return texts.MISSES_OFF
        rows = self.read()
        if not rows:
            return texts.MISSES_EMPTY
        recent = [r for r in rows if now - datetime.fromisoformat(r.at) <= DAY]
        counts = Counter(r.kind for r in recent)
        lines = [texts.MISSES_DAY.format(count=len(recent))]
        lines += [f"• {kind} — {n}" for kind, n in sorted(counts.items(), key=lambda c: (-c[1], c))]
        last = rows[-LAST:]
        lines += ["", texts.MISSES_LAST.format(count=len(last))]
        for row in last:
            moment = datetime.fromisoformat(row.at).astimezone(tz)
            line = f"{moment:%d.%m %H:%M} · {row.step} · {row.kind}"
            if row.text:
                shown = row.text if len(row.text) <= TEXT_SHOWN else row.text[:TEXT_SHOWN] + "…"
                line += f" · «{shown}»"
            lines.append(line)
        return "\n".join(lines)
