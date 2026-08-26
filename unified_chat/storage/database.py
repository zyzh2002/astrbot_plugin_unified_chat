"""SQLite database helpers (aiosqlite + SQLModel)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
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
    SlangTerm,
    UnifiedKV,
    UserAffinity,
)

# SQLModel shares a global metadata registry with AstrBot itself; restrict
# table creation to our own tables so AstrBot's models never leak into the
# plugin database.
_PLUGIN_TABLES = (
    MessageRecord.__table__,
    Memory.__table__,
    LearningLog.__table__,
    UnifiedKV.__table__,
    SlangTerm.__table__,
    UserAffinity.__table__,
)

_engine: AsyncEngine | None = None
_engine_lock = asyncio.Lock()
_SCHEMA_VERSION = 3


def _needs_migration(db_path: Path) -> bool:
    if not db_path.exists() or db_path.stat().st_size == 0:
        return False
    import sqlite3

    con = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "memories" not in tables:
            return False
        columns = {row[1] for row in con.execute("PRAGMA table_info(memories)")}
        required = {"kb_doc_id", "expires_at", "memory_type", "session_id", "reinforce_count"}
        version = con.execute("PRAGMA user_version").fetchone()[0]
        return version < _SCHEMA_VERSION or not required <= columns
    finally:
        con.close()


def _migrate_schema(sync_conn) -> None:
    columns = {
        row[1]
        for row in sync_conn.exec_driver_sql("PRAGMA table_info(memories)").fetchall()
    }
    additions = {
        "kb_doc_id": "VARCHAR(64)",
        "expires_at": "DATETIME",
        "memory_type": "VARCHAR(16) NOT NULL DEFAULT 'FACTUAL'",
        "session_id": "VARCHAR(255) NOT NULL DEFAULT ''",
        "reinforce_count": "INTEGER NOT NULL DEFAULT 0",
    }
    for name, ddl in additions.items():
        if name not in columns:
            sync_conn.exec_driver_sql(f"ALTER TABLE memories ADD COLUMN {name} {ddl}")
    SQLModel.metadata.create_all(sync_conn, tables=list(_PLUGIN_TABLES))
    sync_conn.exec_driver_sql(
        "DELETE FROM memories WHERE dedup_hash != '' AND id NOT IN ("
        "SELECT MIN(id) FROM memories WHERE dedup_hash != '' "
        "GROUP BY dedup_hash, session_id)"
    )
    sync_conn.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_hash_scope "
        "ON memories(dedup_hash, session_id) WHERE dedup_hash != ''"
    )
    sync_conn.exec_driver_sql(
        "DELETE FROM user_affinity WHERE id NOT IN ("
        "SELECT latest.id FROM user_affinity AS latest "
        "WHERE NOT EXISTS (SELECT 1 FROM user_affinity AS newer "
        "WHERE newer.umo = latest.umo AND newer.user_id = latest.user_id "
        "AND (newer.updated_at > latest.updated_at "
        "OR (newer.updated_at = latest.updated_at AND newer.id > latest.id))))"
    )
    sync_conn.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_affinity_scope_user "
        "ON user_affinity(umo, user_id)"
    )
    for table in _PLUGIN_TABLES:
        for index in table.indexes:
            index.create(sync_conn, checkfirst=True)
    sync_conn.exec_driver_sql(
        "UPDATE memories SET expires_at = datetime(created_at, '+' || CASE memory_type "
        "WHEN 'EPISODIC' THEN 14 WHEN 'PLANNED' THEN 30 WHEN 'FACTUAL' THEN 90 "
        "WHEN 'RELATIONAL' THEN 180 WHEN 'PREFERENCE' THEN 365 ELSE 90 END || ' days') "
        "WHERE expires_at IS NULL"
    )
    sync_conn.exec_driver_sql(
        "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
        "memory_id UNINDEXED, content, session_id UNINDEXED)"
    )
    sync_conn.exec_driver_sql("DELETE FROM memory_fts")
    sync_conn.exec_driver_sql(
        "INSERT INTO memory_fts(memory_id, content, session_id) "
        "SELECT id, content, COALESCE(session_id, '') FROM memories"
    )
    sync_conn.exec_driver_sql(f"PRAGMA user_version = {_SCHEMA_VERSION}")


async def get_engine(
    db_path: str | Path,
    before_migrate: Callable[[], Path | None] | None = None,
) -> AsyncEngine:
    global _engine
    if _engine is not None:
        return _engine
    async with _engine_lock:
        if _engine is not None:
            return _engine
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        needs_migration = _needs_migration(db_path)
        if needs_migration:
            backup = before_migrate() if before_migrate is not None else None
            if backup is None:
                raise RuntimeError("schema migration requires a successful backup")
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
            if needs_migration:
                await conn.run_sync(_migrate_schema)
            else:
                await conn.run_sync(
                    lambda sync_conn: SQLModel.metadata.create_all(
                        sync_conn, tables=list(_PLUGIN_TABLES)
                    )
                )
                await conn.exec_driver_sql(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_hash_scope "
                    "ON memories(dedup_hash, session_id) WHERE dedup_hash != ''"
                )
                await conn.exec_driver_sql(f"PRAGMA user_version = {_SCHEMA_VERSION}")
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
