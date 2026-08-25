# Spec 008 — Memory Depth (Atomization, Hybrid Retrieval, Isolation, Agent Tools, Backup)

## Goal
Upgrade flat memories into typed, TTL-scoped atoms with session isolation,
add FTS5-based sparse retrieval fused with keyword search (RRF), an
LLM summarization pipeline, agent-facing recall/memorize tools, DB backups,
and a `/umem` management command group — all without new runtime deps.

## Context
- `Memory` model: content/importance/source/dedup_hash/kb_doc_id/access_count/
  created_at/last_accessed_at/expires_at (`unified_chat/storage/models.py`).
- Retrieval today: KB vector search when embedding provider configured, else
  LIKE keyword fallback (`memory_service.retrieve`).
- LLM calls go through `context.llm_generate(chat_provider_id=..., prompt=...,
  system_prompt=...)` (pattern from `learning_service.refine`).
- Agent tool pattern: pydantic dataclass subclassing astrbot FunctionTool with
  lazy guarded imports (`rag_service.build_kb_tool`).
- SQLModel metadata is process-global; tables created via explicit
  `tables=[...]` list in `storage/database.py`.
- Config schema is FLAT; zero runtime dependencies.

## Requirements

### R001 — Atomized model extension
- `Memory` gains:
  - `memory_type: str = Field(default="FACTUAL", max_length=16, index=True)`
  - `session_id: str = Field(default="", max_length=255, index=True)`
    ("" = global memory, visible in all sessions)
  - `reinforce_count: int = Field(default=0)`
- Per-type default TTL days (constants): EPISODIC 14, PLANNED 30, FACTUAL 90,
  RELATIONAL 180, PREFERENCE 365. Applied on store as `expires_at` when unset.
  Unknown types → FACTUAL.

### R002 — Rule-based type classifier
- `unified_chat/services/memory_classifier.py`: `classify_memory(text) -> str`.
- Pure regex/keyword rules over EN+ZH markers:
  - PREFERENCE: 喜欢/讨厌/偏好/最爱/不喜欢/prefer/like|love|hate/favorite
  - PLANNED: 明天/下周/打算/计划/约定/tomorrow/next week/plan to/will
  - RELATIONAL: 是我的/我的朋友/同事/同学/室友/is my (friend|colleague|...)
  - EPISODIC: 今天/昨天/刚才/刚才/today/yesterday/just now + narrative verbs
  - else FACTUAL. First match wins in that priority order.

### R003 — FTS5 sparse index
- `MemoryRepo` maintains FTS5 virtual table `memory_fts(memory_id UNINDEXED,
  content, session_id)` (created lazily, idempotent):
  - `fts_index_add(memory_id, content, session_id)` / `fts_index_remove(id)`
    called from add/delete paths.
  - `fts_search(query, limit=10) -> list[tuple[int, float]]` (memory_id,
    bm25 rank); tokens quoted defensively; empty result on any error.
- Existing LIKE `search_by_keyword` stays as secondary source.

### R004 — Hybrid retrieval with RRF fusion
- `MemoryService.retrieve_hybrid(query, session_id=None, top_k=5) ->
  list[Memory]`: fuses sparse sources by Reciprocal Rank Fusion
  `score += 1/(60 + rank)` per source; dedupes by memory id; applies session
  filter `(session_id == sid) | (session_id == "")`; boosts reinforce_count.
- `retrieve(query, session_id=None)` keeps its str-return contract but routes
  through hybrid ranking (KB context_text injection remains unchanged).

### R005 — Session isolation
- `maybe_store` stamps `session_id=event.unified_msg_origin`, classifies type,
  assigns TTL expiry.
- Config key `memory_session_isolation: bool = True`; when False all rows
  stored with session_id="".

### R006 — Summarization pipeline
- After every N captured messages per session (config `summary_batch_size`,
  default 10; 0 disables), build window text from MessageRepo and call
  llm_generate asking for JSON array `[{"content": ..., "type": ...}]`;
  parse defensively (extract first [...] block), store each item as Memory
  with source="summary", classified type fallback, session stamp, dedup hash.
- Never raises; failures logged once.

### R007 — Agent memory tools
- `unified_chat_memory_recall(query, k=5)`: returns hybrid-retrieved memory
  lines (respecting isolation).
- `unified_chat_memory_memorize(text)`: stores one memory atom, returns id.
- Registered like kb tool during on_llm_request when agentic enabled;
  both no-op gracefully if tool API unavailable.

### R008 — Backup service
- `services/backup_service.py`: sqlite backup-API copy of the live DB into
  `<data_dir>/backups/<reason>-<YYYYmmdd-HHMMSS>/unified_chat.db`;
  retention keep-last 10 (config `backup_keep_last`).
- Triggers: plugin version change (KV `last_backup_version`) on load; daily
  cron tick; `/umem backup`.

### R009 — `/umem` command group
- `/umem status` (counts by type), `/umem search <q>` (top 5 hybrid),
  `/umem forget <id>`, `/umem backup`, `/umem reset` (delete current-session
  memories). All reply plain text; unknown subcommand prints usage.

### R010 — Tests
- Classifier table-driven cases; TTL mapping; RRF fusion ordering incl. ties;
  FTS roundtrip + defensive quoting; isolation filters; summarizer parse
  robustness (garbage JSON → 0 stores); backup create/retention; command
  handlers via existing lifecycle test patterns.
- fullboot: after two chats, recall tool/hybrid path injects earlier message
  content into later prompt without embedding provider.

## Non-goals
- Vector-graph memory, 3D visualization, persona dual-channel summaries
  (Phase 9 candidates).
