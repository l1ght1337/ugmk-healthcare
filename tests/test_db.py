"""Database: UTC round trip and adding new columns to an existing SQLite file."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.db.models import User
from app.db.session import Database


async def test_times_are_stored_in_utc(tmp_path: Path) -> None:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'b.db'}")
    await db.init()
    local = datetime(2026, 11, 12, 15, 0, tzinfo=ZoneInfo("Asia/Yekaterinburg"))
    async with db() as s:
        s.add(User(id=1, chat_id=1, session={}, consent_at=local))
        await s.commit()
    raw = sqlite3.connect(tmp_path / "b.db").execute("select consent_at from users").fetchone()
    assert raw[0].startswith("2026-11-12 10:00:00")
    async with db() as s:
        user = await s.get(User, 1)
        assert user is not None and user.consent_at == local
    with pytest.raises(Exception, match="naive"):
        async with db() as s:
            s.add(User(id=2, chat_id=2, session={}, consent_at=datetime(2026, 11, 12)))
            await s.commit()
    await db.close()


async def test_new_columns_are_added_to_an_old_database(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute(
        "create table users (id integer primary key, chat_id integer, username varchar, "
        "display_name varchar, phone varchar, consent_at datetime, session json, "
        "created_at datetime)"
    )
    con.commit()
    con.close()
    db = Database(f"sqlite+aiosqlite:///{path}")
    await db.init()
    await db.close()
    columns = {row[1] for row in sqlite3.connect(path).execute("pragma table_info(users)")}
    assert "guardian_at" in columns
