"""A patient talking to Conversation, without Telegram: press buttons by label, type text."""

from __future__ import annotations

from datetime import datetime

from app.bot import texts
from app.bot.conversation import AdminPost, Button, Conversation, Reply, Turn, UserRef
from app.config import Settings

PATIENT = UserRef(id=1001, chat_id=1001, username="tester", display_name="Тест Тестов")
ADMIN_CHAT = -1001234567890


def make_settings(**overrides: object) -> Settings:
    base = {"schedule_provider": "mock", "demo_mode": True, "admin_chat_id": ADMIN_CHAT}
    return Settings(_env_file=None, **(base | overrides))  # type: ignore[arg-type]


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Chat:
    def __init__(self, conversation: Conversation, user: UserRef = PATIENT) -> None:
        self.conversation = conversation
        self.user = user
        self.turn = Turn()
        self.admin: list[AdminPost] = []

    def _take(self, turn: Turn | None) -> Turn:
        turn = turn or Turn()
        for reply in turn.replies:
            for row in reply.buttons:
                for button in row:
                    assert len(button.data.encode()) <= 64, button
                    assert len(button.label) <= 64, button
        self.turn = turn
        self.admin += turn.admin
        return turn

    # actions
    async def start(self) -> Turn:
        return self._take(await self.conversation.start(self.user))

    async def say(self, text: str) -> Turn:
        return self._take(await self.conversation.text(self.user, text))

    async def press(self, label: str) -> Turn:
        return self._take(await self.conversation.button(self.user, self.button(label).data))

    async def data(self, data: str) -> Turn:
        return self._take(await self.conversation.button(self.user, data))

    async def contact(self, phone: str = "+79123456789", owner: int | None = None) -> Turn:
        owner = self.user.id if owner is None else owner
        return self._take(await self.conversation.contact(self.user, phone, owner))

    async def demo_next(self) -> Turn:
        return self._take(await self.conversation.demo_next(self.user))

    async def due(self) -> list[Turn]:
        return [self._take(t) for t in await self.conversation.due()]

    # what the patient sees
    @property
    def replies(self) -> list[Reply]:
        return self.turn.replies

    @property
    def text(self) -> str:
        return "\n\n".join(r.text for r in self.turn.replies)

    def buttons(self) -> list[Button]:
        return [b for r in self.turn.replies for row in r.buttons for b in row]

    def labels(self) -> list[str]:
        return [b.label for b in self.buttons()]

    def button(self, label: str) -> Button:
        exact = [b for b in self.buttons() if b.label == label]
        found = exact or [b for b in self.buttons() if b.label.startswith(label)]
        assert found, f"no button {label!r} in {self.labels()}"
        return found[0]

    # the day picker and the time grid
    def days(self) -> list[Button]:
        return [b for b in self.buttons() if b.data[2:3].isdigit() and b.data.startswith("d:")]

    def times(self) -> list[Button]:
        return [b for b in self.buttons() if b.data.startswith("s:")]

    async def pick_day(self, label: str | None = None) -> Turn:
        days = self.days()
        assert days, f"no day buttons in {self.labels()}"
        chosen = next(b for b in days if label is None or b.label.startswith(label))
        return await self.data(chosen.data)

    async def pick_time(self, not_before: str = "00:00") -> Turn:
        times = [b for b in self.times() if b.label >= not_before]
        assert times, f"no time from {not_before} in {self.labels()}"
        return await self.data(times[0].data)

    async def pick_slot(self, day: str | None = None, not_before: str = "00:00") -> Turn:
        """First a day (if the day picker is shown), then the first time."""
        if self.days():
            await self.pick_day(day)
        return await self.pick_time(not_before)


TOPICS = {
    "Перелом": ("Рука или нога", "Упал, ударился, подвернул", "Похоже на перелом"),
    "Ожог": ("Рана, ожог, укус", "Ожог"),
    "Повторный приём": ("Уже лечусь у вас", "Повторный приём"),
}


async def to_topic(chat: Chat, topic: str) -> None:
    """«Записаться» → «Выбрать из списка» → the five groups → … → the topic."""
    await chat.press(texts.MENU_BOOK)
    await chat.press(texts.TOPIC_LIST)
    for label in TOPICS[topic]:
        await chat.press(label)
