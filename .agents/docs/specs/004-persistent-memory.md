# Spec 004 — Persistent Memory (Scoring, KB-backed Retrieval, Cleanup Cron)

## Goal
Turn eligible chat messages into persistent memories: importance scoring (native), storage in SQLite, vector indexing through a plugin-managed AstrBot knowledge base (FAISS/FTS5/RRF/Rerank reuse), retrieval injection at `on_llm_request`, and a daily 03:00 cleanup cron enforcing the 30-day low-importance retention policy.

## Context
- Verified against AstrBot `v4.27.3`:
  - `Context.kb_manager` (`astrbot/core/knowledge_base/kb_mgr.py`): `create_kb(kb_name, description, emoji, embedding_provider_id, rerank_provider_id, chunk_size, ...) -> KBHelper` (raises if embedding_provider_id missing or name exists), `get_kb_by_name(name) -> KBHelper | None`, `retrieve(query, kb_names, top_k_fusion, top_m_final) -> dict | None`
  - `KBHelper` (`astrbot/core/knowledge_base/kb_helper.py`): `upload_document(file_name, file_content, file_type, ..., pre_chunked_text: list[str] | None) -> KBDocument` (supports raw pre-chunked text, no file parsing), `delete_document(doc_id)`
- `Memory` model exists (001) with `content, importance, source, dedup_hash, access_count, created_at, last_accessed_at, expires_at`.
- Native `score_importance(char_len, recency_hours, freq)` exists (placeholder formula) with Python fallback.
- Config: `enable_persistent_memory` (default true), `embedding_provider_id`, `memory_cleanup_days=30`, `importance_threshold=0.3` (001).

## Requirements

### R001 — Config additions
- Add to `_conf_schema.json` and `PluginConfig` (+`DEFAULTS`, `from_dict`, `to_dict`):
  - `memory_kb_name: str = "unified_chat_memories"` — KB used for memory vector indexing.
- No schema changes to existing keys.

### R002 — Memory model extension
- `Memory` gains `kb_doc_id: str | None = Field(default=None, index=True, max_length=64)` (nullable; empty for non-indexed memories).

### R003 — Importance scoring policy
- `memory_service.compute_importance(content, sender_id, existing: list[Memory]) -> float` uses native `score_importance(char_len, recency_hours, freq)`:
  - `char_len = len(content)`
  - `recency_hours` = hours since the sender's most recent memory (0.0 when none exists)
  - `freq` = number of the sender's memories created in the last 7 days
- Result clamped to [0,1]; ties break by length.

### R004 — Memory candidate policy
- `memory_service.should_store(event) -> bool`: `enable_persistent_memory` on, message not a command (`ChatService.is_command`), `len(message_str) >= MIN_MEMORY_CHARS = 20`, dedup hash not equal to any recently stored memory hash (use existing `dedup_hash`).

### R005 — Storage pipeline (background)
- `memory_service.maybe_store(event, sender_id)` (runs in pipeline background stage):
  1. compute importance
  2. `MemoryRepo.add(Memory(content=..., importance=..., source="auto", dedup_hash=..., expires_at=None))`
  3. if `importance >= config.importance_threshold` and KB available → upload: `kb_helper.upload_document(file_name=f"memory_{mem.id}.txt", file_content=None, file_type="txt", pre_chunked_text=[content])`, then persist `kb_doc_id=doc.doc_id` on the Memory row
- Any KB failure logs and continues (memory stays SQLite-only, no raise).

### R006 — KB lifecycle for memories
- `memory_service.ensure_memory_kb()` (called from lifecycle on_load, inside try):
  - if `embedding_provider_id` empty → mark KB unavailable, skip (SQLite-only mode)
  - `get_kb_by_name(memory_kb_name)` → reuse
  - else `create_kb(kb_name=memory_kb_name, description="...", embedding_provider_id=..., rerank_provider_id=config.rerank_provider_id or None, chunk_size=512, chunk_overlap=50)` — all within try/except; failure → SQLite-only mode + log
- Never raises out of on_load.

### R007 — Memory retrieval injection
- `core/hooks.py` adds `async def inject_memories(event, req, config, memory_service) -> None`:
  - gate `enable_persistent_memory`; query = `event.message_str`
  - KB mode: `kb_manager.retrieve(query, [memory_kb_name], top_k_fusion=20, top_m_final=5)` → `context_text` → append `{"role":"system","content":"Relevant memories:\n<text>"}` to `req.contexts` (None-safe, like 003)
  - SQLite fallback: `MemoryRepo.search_by_keyword(query, limit=5)` (LIKE `%kw%` on content, ordered by importance desc) → join into same system message
  - empty results or errors → return silently (log debug)
- `handle_llm_request` calls it after social context injection, guarded.

### R008 — Cleanup cron (daily 03:00)
- `core/cron.py` provides `class MemoryCleanupCron`:
  - `__init__(self, memory_service)`; `start() -> None` spawns `asyncio.create_task(self._run())` named `unified_chat_cron`
  - `_run()`: loop — sleep until next 03:00 local time (`_seconds_until_next_03()`, pure helper, testable), then `await self._tick()`
  - `_tick()`: `cutoff = now - timedelta(days=memory_cleanup_days)`; `expired = MemoryRepo.list_expired(importance_threshold, cutoff)`; for each: if `kb_doc_id` → `kb_helper.delete_document(doc_id)` (suppressed); then `MemoryRepo.delete_by_ids([...])`; log summary
  - `stop()` cancels task (idempotent, suppress CancelledError)
- Lifecycle `on_load` starts cron when `enable_persistent_memory`; `on_unload` stops it.

### R009 — Repo additions
- `MemoryRepo` gains:
  - `list_expired(threshold: float, cutoff: datetime) -> list[Memory]`
  - `delete_by_ids(ids: list[int]) -> int`
  - `search_by_keyword(keyword: str, limit: int = 5) -> list[Memory]` (escape LIKE wildcards)
  - `update_kb_doc_id(memory_id: int, kb_doc_id: str) -> Memory | None`

### R010 — Pipeline integration
- `MessagePipeline._after_stages` calls `await self.memory_service.maybe_store(event)` when memory enabled (pipeline gains optional `memory_service` constructor arg, default None).
- Lifecycle passes configured MemoryService into the pipeline.

### R011 — Error isolation
- All of the above wrapped: single failure logs and continues; cron tick failures don't kill the loop (try/except per tick).

## Non-Goals
- Per-user memory scoping (memories are bot-global in 004), memory editing commands, embedding migration (006), learning pipeline (005).

## Acceptance Criteria
- Unit tests (mocked KB manager/helper, temp sqlite): scoring clamps and uses sender history; `should_store` gates (command/min-length/dup); maybe_store writes SQLite row and (high importance) uploads pre-chunked doc + persists `kb_doc_id`; KB failure → row without doc; ensure_memory_kb reuse/create/skip paths; inject_memories KB + fallback + empty + disabled; cron helper `_seconds_until_next_03` and `_tick` delete low-importance old rows (+ KB doc deletion), high-importance rows survive; repo search escapes `%`/`_`.
- `opencode debug lsp diagnostics` clean; `uv run ruff check .` clean; `uv run pytest -q` green.
- Cron task cancels cleanly on `stop()`.

## Constraints
- AstrBot `>=4.27.3,<5.0.0`; KB reuse only, no vendor vector DB; SQLite `Memory` table owns retention policy.
- No global singletons; MemoryService per plugin instance.
- English comments; ruff 100 col.

## Out of Scope
- Memory export/import, UI for memory browsing.

## References
- `docs/ARCHITECTURE.md`, `docs/CONFIG.md` (memory_cleanup_days/importance_threshold/迁移), specs 001/002/003.
