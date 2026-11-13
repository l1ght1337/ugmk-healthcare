"""Who may use the bot during the closed test.

ALLOWED_USER_IDS from .env, plus users an administrator let in with «Разрешить» in the
admin chat (stored in the DB, so clinic staff are added without editing .env). An empty
ALLOWED_USER_IDS means the test is open to everyone.
"""

from __future__ import annotations

from app.bot import texts
from app.bot.conversation import AdminPost, Button, Reply, Turn, UserRef
from app.config import Settings
from app.db.models import AccessRequest, User, utcnow
from app.db.session import Database

ALLOW_PREFIX = "allow:"
REQUEST = (
    "Запрос доступа к тестовому боту: {name}{username}, Telegram id {id}. "
    "Нажмите «Разрешить», чтобы открыть доступ."
)
APPROVED_NOTE = "Доступ открыт ({by})."


class Access:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self._approved: set[int] = set()

    async def allowed(self, user_id: int) -> bool:
        if self.settings.user_allowed(user_id) or user_id in self._approved:
            return True
        async with self.db() as db:
            request = await db.get(AccessRequest, user_id)
        if request is not None and request.approved_at is not None:
            self._approved.add(user_id)
            return True
        return False

    async def refuse(self, user: UserRef) -> Turn:
        """Answer an outsider; the first time, ask the administrators with «Разрешить»."""
        async with self.db() as db:
            known = await db.get(AccessRequest, user.id)
            if known is None and self.settings.admin_chat_id:
                db.add(
                    AccessRequest(
                        user_id=user.id,
                        chat_id=user.chat_id,
                        username=user.username,
                        display_name=user.display_name,
                    )
                )
                await db.commit()
                username = f" @{user.username}" if user.username else ""
                post = AdminPost(
                    REQUEST.format(
                        name=user.display_name or "без имени", username=username, id=user.id
                    ),
                    buttons=((Button(texts.ACCESS_ALLOW, f"{ALLOW_PREFIX}{user.id}"),),),
                )
                return Turn([Reply(f"{texts.NOT_ALLOWED} {texts.ACCESS_REQUESTED}")], [post])
        return Turn([Reply(texts.NOT_ALLOWED)])

    async def forget(self, user_id: int) -> bool:
        """Remove what was stored for an id that is not a person: the bot itself.

        Older versions took the «… закрепил(а) сообщение» service message for a stranger
        and asked the administrators to let the bot in. Called at startup with the bot's id.
        """
        self._approved.discard(user_id)
        async with self.db() as db:
            request = await db.get(AccessRequest, user_id)
            user = await db.get(User, user_id)
            for row in (request, user):
                if row is not None:
                    await db.delete(row)
            await db.commit()
        return request is not None or user is not None

    async def approve(self, user_id: int, by: str) -> Turn | None:
        """«Разрешить» pressed in the admin chat: let the user in and tell them."""
        async with self.db() as db:
            request = await db.get(AccessRequest, user_id)
            if request is None:
                return None
            if request.approved_at is None:
                request.approved_at, request.approved_by = utcnow(), by[:128]
                await db.commit()
            self._approved.add(user_id)
            return Turn([Reply(texts.ACCESS_GRANTED, chat_id=request.chat_id)])
