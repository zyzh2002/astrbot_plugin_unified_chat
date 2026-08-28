"""Memory service: importance scoring, KB-backed storage and retrieval."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

from ..native import score_importance
from ..storage import repo as repos
from ..storage.models import Memory
from .chat_service import ChatService
from .memory_classifier import classify_memory
from .memory_ttls import ttl_for


class MemoryService:
    """Per-plugin-instance memory domain (no global singletons)."""

    MIN_MEMORY_CHARS = 20

    def __init__(self, context: Any, config: Any):
        self.context = context
        self.config = config
        self._kb_helper: Any | None = None
        from .memory_summarizer import MemorySummarizer

        self.summarizer = MemorySummarizer(context, config, self.store_atom)

    def session_id_for(self, event: Any) -> str:
        if not getattr(self.config, "memory_session_isolation", True):
            return ""
        return getattr(event, "unified_msg_origin", "") or ""

    async def maybe_summarize(self, event: Any) -> int:
        umo = getattr(event, "unified_msg_origin", "") or ""
        if not umo:
            return 0
        return await self.summarizer.maybe_summarize(umo)

    async def memorize_text(
        self,
        text: str,
        source: str = "agent",
        mtype: str | None = None,
        session_id: str | None = None,
    ) -> int | None:
        """Explicitly store one durable atom; returns its id."""
        memory, _created = await self.store_atom(
            text,
            source=source,
            importance=0.7,
            session_id=session_id,
            mtype=mtype,
        )
        return memory.id

    async def store_atom(
        self,
        text: str,
        *,
        source: str,
        importance: float,
        session_id: str | None,
        mtype: str | None = None,
    ) -> tuple[Memory, bool]:
        """Normalize and store one atom through the single invariant path."""
        resolved_type = mtype or classify_memory(text)
        isolation = getattr(self.config, "memory_session_isolation", True)
        sid = (session_id or "") if isolation else ""
        dedup = ChatService.hash_of(text)
        existing = await repos.MemoryLookupRepo.get_by_hash(
            dedup,
            session_id=sid,
            isolation=isolation,
        )
        if existing is not None:
            return existing, False
        memory, created = await repos.MemoryRepo.add_unique(
            Memory(
                content=text,
                importance=max(0.0, min(1.0, importance)),
                source=source,
                dedup_hash=dedup,
                memory_type=resolved_type,
                session_id=sid,
                expires_at=datetime.now(UTC) + timedelta(days=ttl_for(resolved_type)),
            )
        )
        if not created:
            return memory, False
        await repos.MemoryFts.index_add(memory.id, text, sid)
        if importance >= self.config.importance_threshold and self._kb_helper is not None:
            with contextlib.suppress(Exception):
                doc = await self._kb_helper.upload_document(
                    file_name=f"memory_{memory.id}.txt",
                    file_content=None,
                    file_type="txt",
                    pre_chunked_text=[text],
                )
                if memory.id is not None:
                    await repos.MemoryRepo.update_kb_doc_id(memory.id, doc.doc_id)
        return memory, True

    async def ensure_memory_kb(self) -> None:
        """Bind the memory KB helper; SQLite-only mode on any failure."""
        if not self.config.enable_persistent_memory or not self.config.embedding_provider_id:
            self._kb_helper = None
            return
        kb_manager = getattr(self.context, "kb_manager", None)
        if kb_manager is None:
            self._kb_helper = None
            return
        try:
            helper = await kb_manager.get_kb_by_name(self.config.memory_kb_name)
            if helper is None:
                helper = await kb_manager.create_kb(
                    kb_name=self.config.memory_kb_name,
                    description="UnifiedChat persistent memories",
                    embedding_provider_id=self.config.embedding_provider_id,
                    rerank_provider_id=self.config.rerank_provider_id or None,
                    chunk_size=512,
                    chunk_overlap=50,
                )
            self._kb_helper = helper
        except Exception:
            self._kb_helper = None
            self._log_error("ensure_memory_kb")

    @staticmethod
    def _aware(dt: datetime) -> datetime:
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

    def compute_importance(self, content: str, sender_id: str, existing: list[Memory]) -> float:
        now = datetime.now(UTC)
        freq = 0
        newest: datetime | None = None
        for m in existing:
            created = self._aware(m.created_at)
            if m.source == sender_id and (now - created).days < 7:
                freq += 1
                if newest is None or created > newest:
                    newest = created
        recency_hours = 0.0 if newest is None else max(0.0, (now - newest).total_seconds() / 3600.0)
        return score_importance(len(content), recency_hours, freq)

    def should_store(self, event: Any) -> bool:
        if not self.config.enable_persistent_memory:
            return False
        text = getattr(event, "message_str", "")
        if ChatService.is_command(text):
            return False
        return len(text) >= self.MIN_MEMORY_CHARS

    async def maybe_store(self, event: Any, sender_id: str) -> None:
        try:
            if not self.should_store(event):
                return
            text = event.message_str
            existing = await repos.MemoryRepo.list_all()
            importance = self.compute_importance(text, sender_id, existing)
            session_id = self.session_id_for(event)
            await self.store_atom(
                text,
                source=sender_id,
                importance=importance,
                session_id=session_id,
            )
        except Exception:
            self._log_error("maybe_store")

    async def retrieve(self, query: str, session_id: str | None = None) -> str:
        kb_manager = getattr(self.context, "kb_manager", None)
        if self._kb_helper is not None and kb_manager is not None:
            with contextlib.suppress(Exception):
                result = await kb_manager.retrieve(
                    query=query,
                    kb_names=[self.config.memory_kb_name],
                    top_k_fusion=20,
                    top_m_final=20,
                )
                if result and isinstance(result, dict):
                    raw_results = result.get("results") or []
                    doc_ids = [
                        str(item.get("doc_id"))
                        for item in raw_results
                        if isinstance(item, dict) and item.get("doc_id")
                    ]
                    visible = await repos.MemoryLookupRepo.get_visible_by_kb_doc_ids(
                        doc_ids,
                        session_id=session_id or "",
                        isolation=getattr(
                            self.config,
                            "memory_session_isolation",
                            True,
                        ),
                    )
                    lines = [
                        f"- {visible[doc_id].content}"
                        for doc_id in doc_ids
                        if doc_id in visible
                    ]
                    if lines:
                        return "\n".join(lines)
        hits = await self.retrieve_hybrid(query, session_id=session_id)
        if hits:
            return "\n".join(f"- {m.content}" for m in hits)
        return ""

    async def retrieve_hybrid(
        self, query: str, session_id: str | None = None, top_k: int = 5
    ) -> list[Memory]:
        """RRF-fuse sparse sources (FTS5 + LIKE keyword); never raises."""
        scores: dict[int, float] = {}
        try:
            isolation = getattr(self.config, "memory_session_isolation", True)
            sid = session_id or ""
            now = datetime.now(UTC)
            fts_hits = await repos.MemoryFts.search(
                query,
                limit=10,
                session_id=sid,
                isolation=isolation,
                now=now,
            )
        except Exception:
            fts_hits = []
        try:
            kw_rows = await repos.MemoryRepo.search_by_keyword(
                query,
                limit=10,
                session_id=sid,
                isolation=isolation,
                now=now,
            )
        except Exception:
            kw_rows = []

        for source in (
            [(mid, pos) for pos, (mid, _rank) in enumerate(fts_hits)],
            [(m.id, pos) for pos, m in enumerate(kw_rows)],
        ):
            for mid, position in source:
                if mid is None:
                    continue
                scores[mid] = scores.get(mid, 0.0) + 1.0 / (60.0 + position + 1)
        if not scores:
            return []
        rows = await repos.MemoryHybridRepo.get_by_ids(
            list(scores),
            session_id=sid,
            isolation=isolation,
            now=now,
        )
        rows.sort(key=lambda m: (-scores.get(m.id, 0.0), -m.reinforce_count, m.id))
        return rows[:top_k]

    async def delete_expired_memories(self) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=self.config.memory_cleanup_days)
        expired = await repos.MemoryRepo.list_expired(self.config.importance_threshold, cutoff)
        return await self._delete_memories(expired)

    async def forget(self, memory_id: int, session_id: str | None) -> int:
        row = await repos.MemoryLookupRepo.get_visible_by_id(
            memory_id,
            session_id=session_id or "",
            isolation=getattr(self.config, "memory_session_isolation", True),
        )
        return await self._delete_memories([row] if row is not None else [])

    async def forget_session(self, session_id: str | None) -> int:
        if not session_id:
            # isolation off / unknown origin: refusing would still mean wiping
            # the whole shared pool, so this is a hard no-op
            return 0
        rows = await repos.MemoryAdminRepo.list_by_session(session_id)
        return await self._delete_memories(rows)

    async def _delete_memories(self, rows: list[Memory]) -> int:
        for m in rows:
            if m.kb_doc_id and self._kb_helper is not None:
                with contextlib.suppress(Exception):
                    await self._kb_helper.delete_document(m.kb_doc_id)
            if m.id is not None:
                await repos.MemoryFts.index_remove(m.id)
        return await repos.MemoryRepo.delete_by_ids(
            [m.id for m in rows if m.id is not None]
        )

    @staticmethod
    def _log_error(msg: str) -> None:
        with contextlib.suppress(Exception):
            from astrbot.api import logger  # type: ignore

            logger.error(f"[unified_chat] memory {msg}", exc_info=True)
