"""Migration service: full knowledge base index rebuild."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any

from ..storage import kv as kv_store
from ..storage import repo as repos
from ..storage.models import LearningLog

# A running flag older than this is a crash leftover, not a live migration.
FLAG_STALE_SECONDS = 6 * 3600


class MigrationService:
    """Rebuilds a KB index for the current embedding provider.

    Snapshot chunks -> per-document delete + re-upload (a failed upload only
    affects that one document; its original chunks are best-effort restored
    under an orphan name). The memory KB also clears stale Memory.kb_doc_id
    links (SQLite is the source of truth). Never raises into the command path.
    """

    def __init__(self, context: Any, config: Any):
        self.context = context
        self.config = config
        self._lock = asyncio.Lock()

    @staticmethod
    def _flag_key(kb_name: str) -> str:
        return f"migration:{kb_name}:running"

    @staticmethod
    def _result_key(kb_name: str) -> str:
        return f"migration:{kb_name}:last_result"

    async def is_running(self, kb_name: str) -> bool:
        raw = await kv_store.kv_get(self._flag_key(kb_name))
        if not raw:
            return False
        try:
            started = float(json.loads(raw).get("started", 0.0))
        except Exception:
            started = 0.0
        if time.time() - started > FLAG_STALE_SECONDS:
            with contextlib.suppress(Exception):
                await kv_store.kv_delete(self._flag_key(kb_name))
            return False
        return True

    async def run_migration(self, kb_name: str) -> str:
        async with self._lock:
            if await self.is_running(kb_name):
                return f"Migration for '{kb_name}' is already running."
            await kv_store.kv_set(
                self._flag_key(kb_name), json.dumps({"started": time.time()})
            )
            try:
                result = await self._run_inner(kb_name)
            except Exception as e:
                result = f"Migration for '{kb_name}' failed: {e}"
            finally:
                await kv_store.kv_delete(self._flag_key(kb_name))
            await self._persist_result(kb_name, result)
            return result

    async def _run_inner(self, kb_name: str) -> str:
        kb_manager = getattr(self.context, "kb_manager", None)
        if kb_manager is None:
            return "KB manager unavailable."
        helper = await kb_manager.get_kb_by_name(kb_name)
        if helper is None:
            return f"Knowledge base '{kb_name}' not found."
        snapshots, doc_ids = await self._snapshot_documents(helper)
        await self._rebuild_documents(helper, snapshots, doc_ids)
        if kb_name == self.config.memory_kb_name:
            await repos.MemoryRepo.clear_kb_doc_ids()
            await kv_store.kv_set(
                "embedding_provider_snapshot",
                str(getattr(self.config, "embedding_provider_id", "") or ""),
            )
        return f"Migration for '{kb_name}' done: {len(snapshots)} docs rebuilt."

    @staticmethod
    async def _snapshot_documents(
        helper: Any,
    ) -> tuple[list[tuple[str, list[str]]], list[str]]:
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
        return snapshots, doc_ids

    @staticmethod
    async def _rebuild_documents(
        helper: Any,
        snapshots: list[tuple[str, list[str]]],
        doc_ids: list[str],
    ) -> None:
        """Swap documents one at a time so a failure cannot empty the KB."""
        for idx, (file_name, chunks) in enumerate(snapshots):
            with contextlib.suppress(Exception):
                await helper.delete_document(doc_ids[idx])
            try:
                await helper.upload_document(
                    file_name=file_name,
                    file_content=None,
                    file_type="txt",
                    pre_chunked_text=chunks,
                )
            except Exception as exc:
                with contextlib.suppress(Exception):
                    await helper.upload_document(
                        file_name=f"__orphan_{idx}_{file_name}",
                        file_content=None,
                        file_type="txt",
                        pre_chunked_text=chunks,
                    )
                raise RuntimeError(f"re-upload failed for '{file_name}': {exc}") from exc

    async def _persist_result(self, kb_name: str, result: str) -> None:
        with contextlib.suppress(Exception):
            await kv_store.kv_set(self._result_key(kb_name), result)
            await repos.LearningLogRepo.add(
                LearningLog(
                    stage="migration",
                    input_text=kb_name,
                    output_text=result[:500],
                )
            )
