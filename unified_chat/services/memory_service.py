"""Memory service: importance scoring, KB-backed storage and retrieval."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

from unified_chat.native import score_importance
from unified_chat.services.chat_service import ChatService
from unified_chat.storage import repo as repos
from unified_chat.storage.models import Memory


class MemoryService:
    """Per-plugin-instance memory domain (no global singletons)."""

    MIN_MEMORY_CHARS = 20

    def __init__(self, context: Any, config: Any):
        self.context = context
        self.config = config
        self._kb_helper: Any | None = None

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

    def compute_importance(
        self, content: str, sender_id: str, existing: list[Memory]
    ) -> float:
        now = datetime.now(UTC)
        freq = 0
        newest: datetime | None = None
        for m in existing:
            if m.source == sender_id and (now - m.created_at).days < 7:
                freq += 1
                if newest is None or m.created_at > newest:
                    newest = m.created_at
        recency_hours = (
            0.0 if newest is None else max(0.0, (now - newest).total_seconds() / 3600.0)
        )
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
            mem = await repos.MemoryRepo.add(
                Memory(
                    content=text,
                    importance=importance,
                    source=sender_id,
                    dedup_hash=ChatService.hash_of(text),
                )
            )
            if importance >= self.config.importance_threshold and self._kb_helper is not None:
                with contextlib.suppress(Exception):
                    doc = await self._kb_helper.upload_document(
                        file_name=f"memory_{mem.id}.txt",
                        file_content=None,
                        file_type="txt",
                        pre_chunked_text=[text],
                    )
                    if mem.id is not None:
                        await repos.MemoryRepo.update_kb_doc_id(mem.id, doc.doc_id)
        except Exception:
            self._log_error("maybe_store")

    async def retrieve(self, query: str) -> str:
        kb_manager = getattr(self.context, "kb_manager", None)
        if self._kb_helper is not None and kb_manager is not None:
            with contextlib.suppress(Exception):
                result = await kb_manager.retrieve(
                    query=query,
                    kb_names=[self.config.memory_kb_name],
                    top_k_fusion=20,
                    top_m_final=5,
                )
                if result and isinstance(result, dict) and result.get("context_text"):
                    return str(result["context_text"])
        with contextlib.suppress(Exception):
            hits = await repos.MemoryRepo.search_by_keyword(query, limit=5)
            if hits:
                return "\n".join(f"- {m.content}" for m in hits)
        return ""

    async def delete_expired_memories(self) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=self.config.memory_cleanup_days)
        expired = await repos.MemoryRepo.list_expired(
            self.config.importance_threshold, cutoff
        )
        for m in expired:
            if m.kb_doc_id and self._kb_helper is not None:
                with contextlib.suppress(Exception):
                    await self._kb_helper.delete_document(m.kb_doc_id)
        return await repos.MemoryRepo.delete_by_ids(
            [m.id for m in expired if m.id is not None]
        )

    @staticmethod
    def _log_error(msg: str) -> None:
        with contextlib.suppress(Exception):
            from astrbot.api import logger  # type: ignore

            logger.error(f"[unified_chat] memory {msg}", exc_info=True)
