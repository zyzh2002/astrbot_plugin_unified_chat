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

    @staticmethod
    async def exists_hash(h: str) -> bool:
        async with get_session() as session:
            result = await session.exec(
                select(MessageRecord.id)
                .where(MessageRecord.dedup_hash == h)
                .limit(1)
            )
            return result.first() is not None


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
    async def count() -> int:
        async with get_session() as session:
            result = await session.exec(select(func.count()).select_from(Memory))
            return int(result.one())

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

    @staticmethod
    async def list_expired(threshold: float, cutoff: datetime) -> list[Memory]:
        async with get_session() as session:
            rows = (
                await session.exec(
                    select(Memory)
                    .where(Memory.importance < threshold)
                    .where(Memory.created_at < cutoff)
                )
            ).all()
            return list(rows)

    @staticmethod
    async def delete_by_ids(ids: list[int]) -> int:
        if not ids:
            return 0
        async with get_session() as session:
            rows = (
                await session.exec(
                    select(Memory).where(Memory.id.in_(ids))  # type: ignore[attr-defined]
                )
            ).all()
            for r in rows:
                await session.delete(r)
            await session.commit()
            return len(rows)

    @staticmethod
    async def search_by_keyword(keyword: str, limit: int = 5) -> list[Memory]:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        async with get_session() as session:
            rows = (
                await session.exec(
                    select(Memory)
                    .where(Memory.content.like(f"%{escaped}%", escape="\\"))
                    .order_by(Memory.importance.desc())
                    .limit(limit)
                )
            ).all()
            return list(rows)

    @staticmethod
    async def update_kb_doc_id(memory_id: int, kb_doc_id: str) -> Memory | None:
        async with get_session() as session:
            mem = await session.get(Memory, memory_id)
            if mem is None:
                return None
            mem.kb_doc_id = kb_doc_id
            session.add(mem)
            await session.commit()
            await session.refresh(mem)
            return mem

    @staticmethod
    async def exists_hash(h: str) -> bool:
        async with get_session() as session:
            result = await session.exec(
                select(Memory.id).where(Memory.dedup_hash == h).limit(1)
            )
            return result.first() is not None

    @staticmethod
    async def clear_kb_doc_ids() -> int:
        async with get_session() as session:
            rows = (
                await session.exec(
                    select(Memory).where(Memory.kb_doc_id.is_not(None))  # type: ignore[attr-defined]
                )
            ).all()
            for r in rows:
                r.kb_doc_id = None
                session.add(r)
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

    @staticmethod
    async def count_by_stage(stage: str) -> int:
        async with get_session() as session:
            result = await session.exec(
                select(func.count())
                .select_from(LearningLog)
                .where(LearningLog.stage == stage)
            )
            return int(result.one())
