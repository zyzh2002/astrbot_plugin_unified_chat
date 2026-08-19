"""Thin repository helpers."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlmodel import select

from unified_chat.storage.database import get_session
from unified_chat.storage.models import LearningLog, Memory, MessageRecord


class MessageRepo:
    """Persistence for MessageRecord."""

    @staticmethod
    async def add(record: MessageRecord) -> MessageRecord:
        async with get_session() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    @staticmethod
    async def count() -> int:
        async with get_session() as session:
            result = await session.exec(select(func.count()).select_from(MessageRecord))
            return int(result.one())


class MemoryRepo:
    """Persistence for Memory."""

    @staticmethod
    async def add(memory: Memory) -> Memory:
        async with get_session() as session:
            session.add(memory)
            await session.commit()
            await session.refresh(memory)
            return memory

    @staticmethod
    async def list_all() -> list[Memory]:
        async with get_session() as session:
            result = await session.exec(select(Memory))
            return list(result.all())

    @staticmethod
    async def delete_expired(threshold: float, cutoff: datetime) -> int:
        async with get_session() as session:
            rows = (
                await session.exec(
                    select(Memory)
                    .where(Memory.importance < threshold)
                    .where(Memory.created_at < cutoff)
                )
            ).all()
            for r in rows:
                await session.delete(r)
            await session.commit()
            return len(rows)


class LearningLogRepo:
    """Persistence for LearningLog."""

    @staticmethod
    async def add(log: LearningLog) -> LearningLog:
        async with get_session() as session:
            session.add(log)
            await session.commit()
            await session.refresh(log)
            return log
