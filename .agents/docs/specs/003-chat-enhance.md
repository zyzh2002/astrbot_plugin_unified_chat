# Spec 003 — Conversation Enhancement (Filter, Dedup, Social Context)

## Goal
Add the conversation-enhancement domain: command filtering, cross-runtime dedup hashing (Rust + identical Python fallback), per-session group context buffering, and social-context injection into LLM requests. Build the unified non-blocking message pipeline that later specs (memory, learning) extend.

## Context
- Verified against AstrBot `v4.27.3`:
  - `AstrMessageEvent` (`astrbot/core/platform/astr_message_event.py:37`): `message_str`, `unified_msg_origin`, `get_sender_id()`, `get_sender_name()`, `get_group_id()`, `is_private_chat()`, `is_admin()`
  - `ProviderRequest` (`astrbot/core/provider/entities.py:91`): `contexts: list[dict]` (OpenAI format), `system_prompt: str`
- Config gate: `enable_conversation_enhance` (default true) from `PluginConfig`.

## Requirements

### R001 — Command / noise filter
- `services/chat_service.py` provides:
  - `is_command(text: str) -> bool` — true when text stripped starts with `/` (AstrBot command convention) or is empty/whitespace.
  - `should_process(event) -> bool` — false for commands and for empty `message_str` (pure media/CQ messages); true otherwise. Uses only public event API (`message_str`), no platform internals.

### R002 — Dedup hash (native + fallback parity)
- `rust/src/lib.rs` adds `hash_dedup(text: &str) -> String`: FNV-1a 64-bit, lowercase hex 16 chars, deterministic across runs and platforms.
- `unified_chat/native/fallback.py` adds `hash_dedup` producing IDENTICAL values (same FNV-1a 64).
- `unified_chat/native/__init__.py` exposes `hash_dedup` (native first, fallback on import failure).
- `unified_chat/utils/hashing.py` provides `dedup_hash(text) -> str` delegating to `unified_chat.native.hash_dedup`.

### R003 — Per-session group buffer + social context
- `ChatService` (instance per plugin lifecycle, no global state) keeps a bounded per-session deque:
  - `MAX_SESSION_HISTORY = 50` entries per session, `MAX_CONTEXT_CHARS = 4000`.
  - `record(event)` appends `(sender_name, snippet)` for non-command messages; snippet truncated to 120 chars via native `chunk_text(text, 120, 0)` first chunk semantics (or direct slice if empty).
  - `social_context(event) -> str` returns a compact summary for group chats (private chat → `""`): distinct recent senders (last N=10) plus last 5 message snippets. Empty buffer → `""`.
  - Buffer key = `event.unified_msg_origin`.
- Native `chunk_text` is used for snippet truncation; if chunking returns empty (edge), fall back to raw `text[:120]`.

### R004 — Social context injection at on_llm_request
- `core/hooks.py` adds `async def inject_social_context(event, req, config, chat_service) -> None`:
  - gate: `config.enable_conversation_enhance` true, otherwise return.
  - `social = chat_service.social_context(event)`; empty → return.
  - append `{"role": "system", "content": social}` to `req.contexts` (if `req.contexts` is None → assign `[]` first; use getattr defensively). Never raise.

### R005 — Unified non-blocking pipeline
- `core/pipeline.py` provides `class MessagePipeline`:
  - `__init__(self, config, chat_service)` — no I/O, must not raise.
  - `async def process(self, event) -> None`:
    1. if not `config.enable_conversation_enhance` and learning/memory later gates are off, return early (003: only chat gate matters).
    2. `should_process(event)` false → return.
    3. dedup: `dedup_hash(event.message_str)`; skip if hash seen recently (per-session LRU of last `DEDUP_WINDOW = 20` hashes, stored on ChatService).
    4. `chat_service.record(event)`.
    5. run `self._after_stages(event)` via `asyncio.create_task(..., name="unified_chat_pipeline")` with `add_done_callback` that logs exceptions — non-blocking; 004/005 attach memory/learning stages there.
  - `async def _after_stages(self, event) -> None: ...` (empty in 003, extension point).
- No DB writes in 003 (messages table populated by spec 005).

### R006 — Lifecycle wiring
- `PluginLifecycle.on_load` (inside existing try): create `ChatService` + `MessagePipeline` from config.
- `PluginLifecycle.handle_message(event)` → `await self._pipeline.process(event)` wrapped in try/except (never raise to AstrBot).
- `on_llm_request` also calls `inject_social_context` (in addition to RAG injection from 002), each guarded.

### R007 — Error isolation
- Every stage catches exceptions, logs via guarded `astrbot.api.logger`, continues.

## Non-Goals
- Memory scoring/vector retrieval (004), learning LLM refine (005), KB migration (006).

## Acceptance Criteria
- `cargo test -p unified_chat_native` passes; `hash_dedup` Rust == fallback for a corpus of texts (property test in Python).
- Unit tests: command filter cases (`/cmd`, empty, whitespace, media-like empty, normal); dedup skips repeated message within window; buffer caps at 50 entries and 4000 chars; social context lists senders/snippets, empty for private chat; injection gates (off, empty, normal) and appends to `req.contexts`; pipeline returns early for commands and duplicate messages; background task not awaited (mock `asyncio.create_task` to verify non-blocking).
- `opencode debug lsp diagnostics` clean; `uv run ruff check .` clean; `uv run pytest -q` green.
- `uv run maturin develop --release` builds and `from unified_chat.native import hash_dedup` works.

## Constraints
- Python 3.12+, Rust edition 2021, no new Rust deps (FNV-1a hand-rolled).
- No global singletons; ChatService/pipeline per plugin instance.
- English comments; `ruff` 100 col; deterministic hash values across native/fallback.

## Out of Scope
- Context manager integration with AstrBot's own conversation truncator/compressor.

## References
- `docs/ARCHITECTURE.md` (pipeline), `docs/README.md` (对话增强), `docs/CONFIG.md` (`enable_conversation_enhance`), specs 001/002.
