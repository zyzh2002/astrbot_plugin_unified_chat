"""SQLite database helpers (aiosqlite + SQLModel)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from .models import (  # noqa: F401 ensure tables
    LearningLog,
    Memory,
    MessageRecord,
    UnifiedKV,
)

# SQLModel shares a global metadata registry with AstrBot itself; restrict
# table creation to our own tables so AstrBot's models never leak into the
# plugin database.
_PLUGIN_TABLES = (
    MessageRecord.__table__,
    Memory.__table__,
    LearningLog.__table__,
    UnifiedKV.__table__,
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
            await conn.run_sync(
                lambda sync_conn: SQLModel.metadata.create_all(
                    sync_conn, tables=list(_PLUGIN_TABLES)
                )
            )
        _engine = engine
        return engine


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an AsyncSession bound to the singleton engine.

    Caller is responsible for commit; rollback on exception is automatic.
    Raises RuntimeError if engine not initialized.
    """
    engine = _engine
    if engine is None:
        raise RuntimeError("engine not initialized; call get_engine first")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def close_engine():  # pragma: no cover
    global _engine
    async with _engine_lock:
        if _engine is not None:
            await _engine.dispose()
            _engine = None


def reset_engine_for_tests():  # pragma: no cover - test helper
    global _engine
    _engine = None
