"""SQLite database helpers (aiosqlite + SQLModel)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel import SQLModel

from unified_chat.storage.models import (  # noqa: F401 ensure tables
    LearningLog,
    Memory,
    MessageRecord,
)

_engine: AsyncEngine | None = None
_engine_lock = asyncio.Lock()


async def get_engine(db_path: str | Path) -> AsyncEngine:
    global _engine
    if _engine is not None:
        return _engine
    async with _engine_lock:
        if _engine is not None:
            return _engine
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+aiosqlite:///{db_path}"

        engine = create_async_engine(url, echo=False, future=True)

        @event.listens_for(engine.sync_engine, "connect")
        def _set_pragmas(dbapi_connection, connection_record):  # pragma: no cover
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA cache_size=20000;")
            cursor.close()

        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        _engine = engine
        return engine


async def close_engine():  # pragma: no cover
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


def reset_engine_for_tests():  # pragma: no cover - test helper
    global _engine
    _engine = None
