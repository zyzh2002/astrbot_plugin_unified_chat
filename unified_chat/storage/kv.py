"""KV helpers over UnifiedKV."""

from __future__ import annotations

from datetime import UTC, datetime

from .database import get_session
from .models import UnifiedKV


async def kv_get(key: str) -> str | None:
    """Get value by key, or None if missing."""
    async with get_session() as session:
        row = await session.get(UnifiedKV, key)
        return row.value if row else None


async def kv_set(key: str, value: str) -> None:
    """Insert or update key."""
    async with get_session() as session:
        row = await session.get(UnifiedKV, key)
        if row is not None:
            row.value = value
            row.updated_at = datetime.now(UTC)
            session.add(row)
        else:
            session.add(UnifiedKV(key=key, value=value))
        await session.commit()


async def kv_delete(key: str) -> None:
    """Delete key if exists."""
    async with get_session() as session:
        row = await session.get(UnifiedKV, key)
        if row is not None:
            await session.delete(row)
            await session.commit()
