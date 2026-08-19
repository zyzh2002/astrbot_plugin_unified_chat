"""Migration service: full knowledge base index rebuild."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from unified_chat.storage import kv as kv_store
from unified_chat.storage import repo as repos
from unified_chat.storage.models import LearningLog


class MigrationService:
    """Rebuilds a KB index for the current embedding provider.

    Snapshot chunks -> delete documents -> re-upload with pre-chunked text.
    The memory KB also clears stale Memory.kb_doc_id links (SQLite is the
    source of truth). Never raises into the command path.
    """

    def __init__(self, context: Any, config: Any):
        self.context = context
        self.config = config
        self._lock = asyncio.Lock()

    @staticmethod
    def _flag_key(kb_name: str) -> str:
        return f"migration:{kb_name}:running"

    async def is_running(self, kb_name: str) -> bool:
        return bool(await kv_store.kv_get(self._flag_key(kb_name)))

    async def run_migration(self, kb_name: str) -> str:
        async with self._lock:
            if await self.is_running(kb_name):
                return f"Migration for '{kb_name}' is already running."
            await kv_store.kv_set(self._flag_key(kb_name), "1")
            try:
                kb_manager = getattr(self.context, "kb_manager", None)
                if kb_manager is None:
                    return "KB manager unavailable."
                helper = await kb_manager.get_kb_by_name(kb_name)
                if helper is None:
                    return f"Knowledge base '{kb_name}' not found."
                snapshots: list[tuple[str, list[str]]] = []
                doc_ids: list[str] = []
                offset = 0
                while True:
                    docs = (await helper.list_documents(offset=offset, limit=100)) or []
                    if not docs:
                        break
                    for doc in docs:
                        chunks: list[str] = []
                        coffset = 0
                        while True:
                            batch = (
                                await helper.get_chunks_by_doc_id(
                                    doc.doc_id, offset=coffset, limit=100
                                )
                                or []
                            )
                            if not batch:
                                break
                            chunks.extend(c["content"] for c in batch)
                            coffset += 100
                        snapshots.append((doc.file_name, chunks))
                        doc_ids.append(doc.doc_id)
                    offset += 100
                for doc_id in doc_ids:
                    with contextlib.suppress(Exception):
                        await helper.delete_document(doc_id)
                for file_name, chunks in snapshots:
                    await helper.upload_document(
                        file_name=file_name,
                        file_content=None,
                        file_type="txt",
                        pre_chunked_text=chunks,
                    )
                if kb_name == self.config.memory_kb_name:
                    await repos.MemoryRepo.clear_kb_doc_ids()
                await repos.LearningLogRepo.add(
                    LearningLog(
                        stage="migration",
                        input_text=kb_name,
                        output_text=f"{len(snapshots)} docs rebuilt",
                    )
                )
                return f"Migration for '{kb_name}' done: {len(snapshots)} docs rebuilt."
            except Exception as e:
                return f"Migration for '{kb_name}' failed: {e}"
            finally:
                await kv_store.kv_delete(self._flag_key(kb_name))
