"""Regression tests for in-place plugin DB schema upgrades."""

import sqlite3
from pathlib import Path

import pytest

from unified_chat.storage.database import close_engine, get_engine, reset_engine_for_tests


def _old_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE memories ("
        "id INTEGER PRIMARY KEY, content TEXT, importance FLOAT, source VARCHAR(64), "
        "dedup_hash VARCHAR(64), access_count INTEGER, created_at DATETIME, "
        "last_accessed_at DATETIME)"
    )
    con.execute(
        "INSERT INTO memories VALUES "
        "(1, 'legacy cobalt fact', 0.8, 'u', 'h', 0, '2026-01-01', '2026-01-01')"
    )
    con.commit()
    con.close()


@pytest.mark.asyncio
async def test_upgrade_old_schema_preserves_rows_and_takes_backup(tmp_path):
    reset_engine_for_tests()
    db = tmp_path / "legacy.db"
    backup = tmp_path / "before.db"
    _old_db(db)

    def before_migrate():
        src = sqlite3.connect(db)
        dst = sqlite3.connect(backup)
        src.backup(dst)
        dst.close()
        src.close()
        return backup

    await get_engine(db, before_migrate=before_migrate)
    try:
        con = sqlite3.connect(db)
        columns = {row[1] for row in con.execute("PRAGMA table_info(memories)")}
        row = con.execute(
            "SELECT content, memory_type, session_id, reinforce_count, expires_at "
            "FROM memories WHERE id=1"
        ).fetchone()
        version = con.execute("PRAGMA user_version").fetchone()[0]
        con.close()
        assert {
            "kb_doc_id",
            "expires_at",
            "memory_type",
            "session_id",
            "reinforce_count",
        } <= columns
        assert row[0] == "legacy cobalt fact"
        assert row[1:4] == ("FACTUAL", "", 0)
        assert row[4] is not None
        assert version == 3
        old_con = sqlite3.connect(backup)
        old_columns = {r[1] for r in old_con.execute("PRAGMA table_info(memories)")}
        old_con.close()
        assert "memory_type" not in old_columns
    finally:
        await close_engine()
        reset_engine_for_tests()


@pytest.mark.asyncio
async def test_migration_aborts_when_backup_fails(tmp_path):
    reset_engine_for_tests()
    db = tmp_path / "legacy.db"
    _old_db(db)
    with pytest.raises(RuntimeError, match="backup"):
        await get_engine(db, before_migrate=lambda: None)
    con = sqlite3.connect(db)
    columns = {r[1] for r in con.execute("PRAGMA table_info(memories)")}
    con.close()
    assert "memory_type" not in columns


@pytest.mark.asyncio
async def test_schema_migration_backfills_fts(tmp_path):
    from datetime import datetime

    from unified_chat.storage.repo import MemoryFts

    reset_engine_for_tests()
    db = tmp_path / "legacy.db"
    backup = tmp_path / "before.db"
    _old_db(db)

    def before_migrate():
        src = sqlite3.connect(db)
        dst = sqlite3.connect(backup)
        src.backup(dst)
        dst.close()
        src.close()
        return backup

    await get_engine(db, before_migrate=before_migrate)
    try:
        hits = await MemoryFts.search(
            "cobalt",
            isolation=False,
            now=datetime(2026, 2, 1),
        )
        assert hits and hits[0][0] == 1
    finally:
        await close_engine()
        reset_engine_for_tests()


@pytest.mark.asyncio
async def test_migration_keeps_latest_affinity_duplicate(tmp_path):
    reset_engine_for_tests()
    db = tmp_path / "affinity-legacy.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE memories ("
        "id INTEGER PRIMARY KEY, content TEXT, importance FLOAT, source VARCHAR(64), "
        "dedup_hash VARCHAR(64), access_count INTEGER, created_at DATETIME, "
        "last_accessed_at DATETIME)"
    )
    con.execute(
        "CREATE TABLE user_affinity ("
        "id INTEGER PRIMARY KEY, umo VARCHAR(255), user_id VARCHAR(64), "
        "score FLOAT, updated_at DATETIME)"
    )
    con.execute("INSERT INTO user_affinity VALUES (1, 's', 'u', 20, '2026-01-01')")
    con.execute("INSERT INTO user_affinity VALUES (2, 's', 'u', 80, '2026-02-01')")
    con.commit()
    con.close()
    backup = tmp_path / "before.db"

    def before_migrate():
        src = sqlite3.connect(db)
        dst = sqlite3.connect(backup)
        src.backup(dst)
        dst.close()
        src.close()
        return backup

    await get_engine(db, before_migrate=before_migrate)
    try:
        con = sqlite3.connect(db)
        rows = con.execute(
            "SELECT score FROM user_affinity WHERE umo='s' AND user_id='u'"
        ).fetchall()
        con.close()
        assert rows == [(80.0,)]
    finally:
        await close_engine()
        reset_engine_for_tests()
