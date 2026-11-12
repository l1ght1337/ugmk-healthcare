"""Async engine and sessions (SQLite via aiosqlite for the test, Postgres later)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import PROJECT_ROOT
from app.db.models import Base


class Database:
    def __init__(self, url: str) -> None:
        parsed = make_url(url)
        if parsed.drivername.startswith("sqlite") and parsed.database not in (None, ":memory:"):
            path = Path(parsed.database)
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            path.parent.mkdir(parents=True, exist_ok=True)
            parsed = parsed.set(database=str(path))
        self.engine = create_async_engine(parsed)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    def __call__(self) -> AsyncSession:
        return self.session()
