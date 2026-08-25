"""Thin repository helpers."""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import func, text
from sqlmodel import select

from .database import get_session
from .models import LearningLog, Memory, MessageRecord

_FTS_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
    "memory_id UNINDEXED, content, session_id UNINDEXED)"
)


def _fts_match_expr(query: str) -> str:
    tokens = re.findall(r"\w+", query)[:8]
    return " OR ".join(f'"{token}"' for token in tokens)


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
                select(MessageRecord.id).where(MessageRecord.dedup_hash == h).limit(1)
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
        for mid in ids:
            await MemoryFts.index_remove(mid)
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
            result = await session.exec(select(Memory.id).where(Memory.dedup_hash == h).limit(1))
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
                select(func.count()).select_from(LearningLog).where(LearningLog.stage == stage)
            )
            return int(result.one())


class MemoryFts:
    """Best-effort FTS5 sparse index over memory contents."""

    @staticmethod
    async def _ensure_table(session) -> None:
        await session.execute(text(_FTS_DDL))

    @staticmethod
    async def index_add(memory_id: int | None, content: str, session_id: str) -> None:
        if memory_id is None or not content.strip():
            return
        try:
            async with get_session() as session:
                await MemoryFts._ensure_table(session)
                await session.execute(
                    text(
                        "INSERT INTO memory_fts(memory_id, content, session_id) "
                        "VALUES (:mid, :content, :sid)"
                    ),
                    {"mid": int(memory_id), "content": content, "sid": session_id or ""},
                )
                await session.commit()
        except Exception:
            pass  # sparse index is best-effort; keyword fallback remains

    @staticmethod
    async def index_remove(memory_id: int) -> None:
        try:
            async with get_session() as session:
                await MemoryFts._ensure_table(session)
                await session.execute(
                    text("DELETE FROM memory_fts WHERE memory_id = :mid"),
                    {"mid": int(memory_id)},
                )
                await session.commit()
        except Exception:
            pass

    @staticmethod
    async def search(query: str, limit: int = 10) -> list[tuple[int, float]]:
        """Return (memory_id, bm25_rank) hits, best first. Never raises."""
        if not query.strip():
            return []
        expr = _fts_match_expr(query)
        if not expr:
            return []
        try:
            async with get_session() as session:
                await MemoryFts._ensure_table(session)
                result = await session.execute(
                    text(
                        "SELECT memory_id, bm25(memory_fts) FROM memory_fts "
                        "WHERE memory_fts MATCH :expr ORDER BY rank LIMIT :lim"
                    ),
                    {"expr": expr, "lim": int(limit)},
                )
                rows = result.fetchall()
            return [(int(row[0]), float(row[1])) for row in rows]
        except Exception:
            return []


class MemoryHybridRepo:
    """Read-side helpers spanning multiple sparse sources."""

    @staticmethod
    async def get_by_ids(ids: list[int]) -> list[Memory]:
        if not ids:
            return []
        async with get_session() as session:
            rows = (
                await session.exec(select(Memory).where(Memory.id.in_(ids)))
            ).all()
            return list(rows)


class MessageSessionRepo:
    """Per-session message window helpers for summarization."""

    @staticmethod
    async def count_by_session(umo: str) -> int:
        async with get_session() as session:
            result = await session.exec(
                select(func.count()).select_from(MessageRecord).where(MessageRecord.umo == umo)
            )
            return int(result.one())

    @staticmethod
    async def list_recent_by_session(umo: str, limit: int) -> list[MessageRecord]:
        if limit <= 0:
            return []
        async with get_session() as session:
            rows = (
                await session.exec(
                    select(MessageRecord)
                    .where(MessageRecord.umo == umo)
                    .order_by(MessageRecord.created_at.desc(), MessageRecord.id.desc())
                    .limit(limit)
                )
            ).all()
            return list(reversed(rows))


class MemoryLookupRepo:
    """Single-row memory lookups."""

    @staticmethod
    async def get_by_hash(h: str) -> Memory | None:
        if not h:
            return None
        async with get_session() as session:
            rows = (
                await session.exec(select(Memory).where(Memory.dedup_hash == h).limit(1))
            ).all()
            return rows[0] if rows else None


class MemoryAdminRepo:
    """Aggregation and admin operations over memories."""

    @staticmethod
    async def count_by_type() -> dict[str, int]:
        async with get_session() as session:
            rows = (
                await session.exec(
                    select(Memory.memory_type, func.count())
                    .select_from(Memory)
                    .group_by(Memory.memory_type)
                )
            ).all()
            return {str(row[0]): int(row[1]) for row in rows}

    @staticmethod
    async def delete_by_session(session_id: str) -> int:
        if not session_id:
            return 0
        async with get_session() as session:
            rows = (
                await session.exec(select(Memory).where(Memory.session_id == session_id))
            ).all()
            ids = [row.id for row in rows if row.id is not None]
        return await MemoryRepo.delete_by_ids(ids)


class MessageScanRepo:
    """Cross-session scans for proactive scheduling."""

    @staticmethod
    async def distinct_umos(limit: int = 50) -> list[tuple[str, float]]:
        async with get_session() as session:
            rows = (
                await session.exec(
                    select(
                        MessageRecord.umo, func.max(MessageRecord.created_at)
                    )
                    .group_by(MessageRecord.umo)
                    .limit(limit)
                )
            ).all()
            result = []
            for umo, last_ts in rows:
                if hasattr(last_ts, "timestamp"):
                    result.append((str(umo), float(last_ts.timestamp())))
                else:
                    result.append((str(umo), 0.0))
            return result
