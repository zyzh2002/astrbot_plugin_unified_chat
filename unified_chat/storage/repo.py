"""Thin repository helpers."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, text
from sqlmodel import select

from .database import get_session
from .models import LearningLog, Memory, MessageRecord, SlangTerm, UserAffinity

_FTS_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
    "memory_id UNINDEXED, content, session_id UNINDEXED)"
)


def _fts_match_expr(query: str) -> str:
    tokens = re.findall(r"\w+", query)[:8]
    return " OR ".join(f'"{token}"' for token in tokens)


def _active_clause(now: datetime):
    return or_(Memory.expires_at.is_(None), Memory.expires_at > now)


def _visible_clause(session_id: str, isolation: bool):
    if not isolation:
        return Memory.id.is_not(None)
    return or_(Memory.session_id == "", Memory.session_id == session_id)


def _utcnow() -> datetime:
    """Aware-UTC now. SQLite DATETIME round-trips as naive UTC wall clock,
    so every comparison must use aware UTC (never local time)."""
    return datetime.now(UTC)


def _utc_wall(now: datetime) -> str:
    """Format a datetime as the UTC wall-clock string SQLite DATETIME stores."""
    if now.tzinfo is None:
        return now.strftime("%Y-%m-%d %H:%M:%S.%f")
    return now.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def _utc_ts(value: datetime) -> float:
    """Epoch seconds for a value read back from SQLite (naive = UTC wall)."""
    return value.timestamp() if value.tzinfo else value.replace(tzinfo=UTC).timestamp()


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
    async def delete_older_than(cutoff: datetime) -> int:
        async with get_session() as session:
            rows = (
                await session.exec(
                    select(MessageRecord).where(MessageRecord.created_at < cutoff)
                )
            ).all()
            for row in rows:
                await session.delete(row)
            await session.commit()
            return len(rows)

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
    async def add_unique(memory: Memory) -> tuple[Memory, bool]:
        async with get_session() as session:
            result = await session.exec(
                text(
                    "INSERT OR IGNORE INTO memories("
                    "content, importance, source, dedup_hash, kb_doc_id, access_count, "
                    "memory_type, session_id, reinforce_count, created_at, "
                    "last_accessed_at, expires_at) VALUES ("
                    ":content, :importance, :source, :dedup_hash, NULL, 0, :memory_type, "
                    ":session_id, 0, :created_at, :last_accessed_at, :expires_at)"
                ),
                params={
                    "content": memory.content,
                    "importance": memory.importance,
                    "source": memory.source,
                    "dedup_hash": memory.dedup_hash,
                    "memory_type": memory.memory_type,
                    "session_id": memory.session_id,
                    "created_at": memory.created_at,
                    "last_accessed_at": memory.last_accessed_at,
                    "expires_at": memory.expires_at,
                },
            )
            await session.commit()
            rows = (
                await session.exec(
                    select(Memory)
                    .where(Memory.dedup_hash == memory.dedup_hash)
                    .where(Memory.session_id == memory.session_id)
                    .limit(1)
                )
            ).all()
            return rows[0], bool(result.rowcount)

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
        now = datetime.now(cutoff.tzinfo) if cutoff.tzinfo else datetime.now()
        async with get_session() as session:
            rows = (
                await session.exec(
                    select(Memory)
                    .where(
                        or_(
                            Memory.expires_at <= now,
                            and_(
                                Memory.expires_at.is_(None),
                                Memory.importance < threshold,
                                Memory.created_at < cutoff,
                            ),
                        )
                    )
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
    async def search_by_keyword(
        keyword: str,
        limit: int = 5,
        *,
        session_id: str = "",
        isolation: bool = True,
        now: datetime | None = None,
    ) -> list[Memory]:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        now = now or _utcnow()
        async with get_session() as session:
            rows = (
                await session.exec(
                    select(Memory)
                    .where(Memory.content.like(f"%{escaped}%", escape="\\"))
                    .where(_active_clause(now))
                    .where(_visible_clause(session_id, isolation))
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
    async def delete_older_than(cutoff: datetime) -> int:
        async with get_session() as session:
            rows = (
                await session.exec(
                    select(LearningLog).where(LearningLog.created_at < cutoff)
                )
            ).all()
            for row in rows:
                await session.delete(row)
            await session.commit()
            return len(rows)

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
        await session.exec(text(_FTS_DDL))

    @staticmethod
    async def index_add(memory_id: int | None, content: str, session_id: str) -> None:
        if memory_id is None or not content.strip():
            return
        try:
            async with get_session() as session:
                await MemoryFts._ensure_table(session)
                await session.exec(
                    text(
                        "INSERT INTO memory_fts(memory_id, content, session_id) "
                        "VALUES (:mid, :content, :sid)"
                    ),
                    params={
                        "mid": int(memory_id),
                        "content": content,
                        "sid": session_id or "",
                    },
                )
                await session.commit()
        except Exception:
            pass  # sparse index is best-effort; keyword fallback remains

    @staticmethod
    async def index_remove(memory_id: int) -> None:
        try:
            async with get_session() as session:
                await MemoryFts._ensure_table(session)
                await session.exec(
                    text("DELETE FROM memory_fts WHERE memory_id = :mid"),
                    params={"mid": int(memory_id)},
                )
                await session.commit()
        except Exception:
            pass

    @staticmethod
    async def search(
        query: str,
        limit: int = 10,
        *,
        session_id: str = "",
        isolation: bool = True,
        now: datetime | None = None,
    ) -> list[tuple[int, float]]:
        """Return (memory_id, bm25_rank) hits, best first. Never raises."""
        if not query.strip():
            return []
        expr = _fts_match_expr(query)
        if not expr:
            return []
        now = now or _utcnow()
        try:
            async with get_session() as session:
                await MemoryFts._ensure_table(session)
                result = await session.exec(
                    text(
                        "SELECT memory_fts.memory_id, bm25(memory_fts) FROM memory_fts "
                        "JOIN memories ON memories.id = memory_fts.memory_id "
                        "WHERE memory_fts MATCH :expr "
                        "AND (memories.expires_at IS NULL OR memories.expires_at > :now) "
                        "AND (:isolation = 0 OR memories.session_id = '' "
                        "OR memories.session_id = :sid) "
                        "ORDER BY bm25(memory_fts) LIMIT :lim"
                    ),
                    params={
                        "expr": expr,
                        "lim": int(limit),
                        "now": _utc_wall(now),
                        "isolation": 1 if isolation else 0,
                        "sid": session_id,
                    },
                )
                rows = result.fetchall()
            return [(int(row[0]), float(row[1])) for row in rows]
        except Exception:
            return []


class MemoryHybridRepo:
    """Read-side helpers spanning multiple sparse sources."""

    @staticmethod
    async def get_by_ids(
        ids: list[int],
        *,
        session_id: str = "",
        isolation: bool = True,
        now: datetime | None = None,
    ) -> list[Memory]:
        if not ids:
            return []
        now = now or _utcnow()
        async with get_session() as session:
            rows = (
                await session.exec(
                    select(Memory)
                    .where(Memory.id.in_(ids))
                    .where(_active_clause(now))
                    .where(_visible_clause(session_id, isolation))
                )
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
    async def get_by_hash(
        h: str,
        *,
        session_id: str = "",
        isolation: bool = True,
    ) -> Memory | None:
        if not h:
            return None
        async with get_session() as session:
            rows = (
                await session.exec(
                    select(Memory)
                    .where(Memory.dedup_hash == h)
                    .where(_active_clause(_utcnow()))
                    .where(_visible_clause(session_id, isolation))
                    .limit(1)
                )
            ).all()
            return rows[0] if rows else None

    @staticmethod
    async def get_visible_by_id(
        memory_id: int,
        *,
        session_id: str,
        isolation: bool,
    ) -> Memory | None:
        async with get_session() as session:
            rows = (
                await session.exec(
                    select(Memory)
                    .where(Memory.id == memory_id)
                    .where(_active_clause(_utcnow()))
                    .where(_visible_clause(session_id, isolation))
                    .limit(1)
                )
            ).all()
            return rows[0] if rows else None

    @staticmethod
    async def get_visible_by_kb_doc_ids(
        doc_ids: list[str],
        *,
        session_id: str,
        isolation: bool,
    ) -> dict[str, Memory]:
        if not doc_ids:
            return {}
        async with get_session() as session:
            rows = (
                await session.exec(
                    select(Memory)
                    .where(Memory.kb_doc_id.in_(doc_ids))
                    .where(_active_clause(_utcnow()))
                    .where(_visible_clause(session_id, isolation))
                )
            ).all()
            return {str(row.kb_doc_id): row for row in rows if row.kb_doc_id}


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
            # "" matches the whole global pool; only explicit ids may clear it
            return 0
        async with get_session() as session:
            rows = (
                await session.exec(select(Memory).where(Memory.session_id == session_id))
            ).all()
            ids = [row.id for row in rows if row.id is not None]
        return await MemoryRepo.delete_by_ids(ids)

    @staticmethod
    async def list_by_session(session_id: str) -> list[Memory]:
        if not session_id:
            return []
        async with get_session() as session:
            rows = (
                await session.exec(select(Memory).where(Memory.session_id == session_id))
            ).all()
            return list(rows)


class MessageScanRepo:
    """Cross-session scans for proactive scheduling."""

    @staticmethod
    async def distinct_umos(limit: int = 50) -> list[tuple[str, float]]:
        """Sessions by most recent activity, newest first (slang mining)."""
        async with get_session() as session:
            rows = (
                await session.exec(
                    select(
                        MessageRecord.umo, func.max(MessageRecord.created_at)
                    )
                    .group_by(MessageRecord.umo)
                    .order_by(func.max(MessageRecord.created_at).desc())
                    .limit(limit)
                )
            ).all()
            return [
                (str(umo), _utc_ts(last_ts) if hasattr(last_ts, "timestamp") else 0.0)
                for umo, last_ts in rows
            ]

    @staticmethod
    async def distinct_group_umos(limit: int = 50) -> list[tuple[str, float]]:
        """Group sessions by most recent activity, quietest first (proactive)."""
        async with get_session() as session:
            rows = (
                await session.exec(
                    select(MessageRecord.umo, func.max(MessageRecord.created_at))
                    .where(MessageRecord.group_id != "")
                    .group_by(MessageRecord.umo)
                    .order_by(func.max(MessageRecord.created_at).asc())
                    .limit(limit)
                )
            ).all()
            return [
                (str(umo), _utc_ts(last_ts) if hasattr(last_ts, "timestamp") else 0.0)
                for umo, last_ts in rows
            ]


class SlangRepo:
    """Persistence for slang candidates/meanings."""

    @staticmethod
    async def add(term_row: SlangTerm) -> SlangTerm:
        async with get_session() as session:
            session.add(term_row)
            await session.commit()
            await session.refresh(term_row)
            return term_row

    @staticmethod
    async def exists_term(term: str, umo: str) -> bool:
        async with get_session() as session:
            result = await session.exec(
                select(SlangTerm.id)
                .where(SlangTerm.term == term)
                .where(SlangTerm.umo == umo)
                .limit(1)
            )
            return result.first() is not None

    @staticmethod
    async def list_by_status(status: str, limit: int = 50) -> list[SlangTerm]:
        async with get_session() as session:
            rows = (
                await session.exec(
                    select(SlangTerm)
                    .where(SlangTerm.status == status)
                    .order_by(SlangTerm.count.desc())
                    .limit(limit)
                )
            ).all()
            return list(rows)

    @staticmethod
    async def confirmed_all() -> list[SlangTerm]:
        async with get_session() as session:
            rows = (
                await session.exec(
                    select(SlangTerm).where(SlangTerm.status == "confirmed")
                )
            ).all()
            return list(rows)

    @staticmethod
    async def confirmed_for_umo(umo: str, limit: int = 100) -> list[SlangTerm]:
        if not umo:
            return []
        async with get_session() as session:
            rows = (
                await session.exec(
                    select(SlangTerm)
                    .where(SlangTerm.status == "confirmed")
                    .where(SlangTerm.umo == umo)
                    .limit(limit)
                )
            ).all()
            return list(rows)

    @staticmethod
    async def set_status(term_id: int, status: str) -> None:
        async with get_session() as session:
            row = await session.get(SlangTerm, term_id)
            if row is not None:
                row.status = status
                session.add(row)
                await session.commit()

    @staticmethod
    async def set_meaning(term_id: int, meaning: str) -> None:
        async with get_session() as session:
            row = await session.get(SlangTerm, term_id)
            if row is not None:
                row.meaning = meaning[:512]
                session.add(row)
                await session.commit()


class AffinityRepo:
    """Per-session-user affinity scores."""

    BASELINE = 50.0

    @staticmethod
    async def bump(umo: str, user_id: str, delta: float = 1.0) -> float:
        async with get_session() as session:
            await session.exec(
                text(
                    "INSERT INTO user_affinity(umo, user_id, score, updated_at) "
                    "VALUES (:umo, :uid, MAX(0, MIN(100, 50 + :delta)), CURRENT_TIMESTAMP) "
                    "ON CONFLICT(umo, user_id) DO UPDATE SET "
                    "score = MAX(0, MIN(100, user_affinity.score + :delta)), "
                    "updated_at = CURRENT_TIMESTAMP"
                ),
                params={"umo": umo, "uid": user_id, "delta": float(delta)},
            )
            await session.commit()
            result = await session.exec(
                text(
                    "SELECT score FROM user_affinity "
                    "WHERE umo = :umo AND user_id = :uid"
                ),
                params={"umo": umo, "uid": user_id},
            )
            return float(result.scalar_one())

    @staticmethod
    async def all_rows(limit: int = 500) -> list[UserAffinity]:
        async with get_session() as session:
            rows = (await session.exec(select(UserAffinity).limit(limit))).all()
            return list(rows)

    @staticmethod
    async def save_score(row: UserAffinity) -> None:
        async with get_session() as session:
            session.add(row)
            await session.commit()

    @staticmethod
    def band(score: float) -> str:
        if score > 70:
            return "warm"
        if score < 30:
            return "cool"
        return "neutral"


class AffinityLookupRepo:
    """Single-row affinity reads."""

    @staticmethod
    async def get_score(umo: str, user_id: str) -> float | None:
        async with get_session() as session:
            rows = (
                await session.exec(
                    select(UserAffinity)
                    .where(UserAffinity.umo == umo)
                    .where(UserAffinity.user_id == user_id)
                    .limit(1)
                )
            ).all()
            return float(rows[0].score) if rows else None
