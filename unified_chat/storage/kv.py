"""KV helpers over UnifiedKV."""

from __future__ import annotations

from sqlalchemy import text

from .database import get_session
from .models import UnifiedKV


def _escape_like(prefix: str) -> str:
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def kv_get(key: str) -> str | None:
    """Get value by key, or None if missing."""
    async with get_session() as session:
        row = await session.get(UnifiedKV, key)
        return row.value if row else None


async def kv_set(key: str, value: str) -> None:
    """Insert or update key atomically (concurrent writers cannot collide)."""
    async with get_session() as session:
        await session.exec(
            text(
                "INSERT INTO unified_kv(key, value, updated_at) "
                "VALUES (:key, :value, CURRENT_TIMESTAMP) "
                "ON CONFLICT(key) DO UPDATE SET "
                "value = excluded.value, updated_at = CURRENT_TIMESTAMP"
            ),
            params={"key": key, "value": str(value)},
        )
        await session.commit()


async def kv_delete(key: str) -> None:
    """Delete key if exists."""
    async with get_session() as session:
        row = await session.get(UnifiedKV, key)
        if row is not None:
            await session.delete(row)
            await session.commit()


async def kv_keys_with_prefix(prefix: str) -> list[str]:
    """List keys starting with prefix."""
    async with get_session() as session:
        result = await session.exec(
            text("SELECT key FROM unified_kv WHERE key LIKE :pat ESCAPE '\\'"),
            params={"pat": _escape_like(prefix) + "%"},
        )
        return [str(row[0]) for row in result.fetchall()]


async def kv_delete_prefix(prefix: str) -> int:
    """Delete all keys starting with prefix; returns count removed."""
    async with get_session() as session:
        result = await session.exec(
            text("DELETE FROM unified_kv WHERE key LIKE :pat ESCAPE '\\'"),
            params={"pat": _escape_like(prefix) + "%"},
        )
        await session.commit()
        return int(result.rowcount or 0)
