# Spec 005 — Adaptive Learning (Filter → Refine → Reinforce)

## Goal
Turn every eligible chat message into durable knowledge through a background filter→refine→reinforce pipeline: raw capture, optional LLM refinement into a memory-worthy statement, and reinforcement into the persistent `Memory` store, with full `LearningLog` audit.

## Context
- Verified against AstrBot `v4.27.3`:
  - `Context.llm_generate(*, chat_provider_id, prompt, image_urls, audio_urls, tools, system_prompt, contexts, **kwargs) -> LLMResponse` (raises `ProviderNotFoundError` / `ChatProviderNotFoundError` on bad id)
  - `LLMResponse.completion_text: str` (property)
- Config: `enable_adaptive_learning` (default true), `chat_provider_id` (empty default → degrade mode).
- Pipeline background stage (003) already runs after dedup/filter; memory stage exists (004).
- Models: `MessageRecord` (raw capture), `Memory` (reinforce target), `LearningLog` (audit).

## Requirements

### R001 — Degrade mode (no provider)
- When `chat_provider_id` is empty or `context.llm_generate` missing, the pipeline only captures `MessageRecord` rows (raw text) for messages that pass the filter; no refine, no reinforce, no Memory writes. Logged once at first use (debug).

### R002 — Filter stage
- `learning_service.should_learn(event) -> bool`: `enable_adaptive_learning` on, text non-empty after strip, not a command (`ChatService.is_command`), `len(text) >= MIN_LEARN_CHARS = 8`, and dedup hash not already stored as `MessageRecord` (checked via repo).
- Every captured message is stored as `MessageRecord(umo, sender_id, group_id, content, dedup_hash)` and `LearningLog(stage="filter", input_text, output_text="", provider_id="")`.

### R003 — Refine stage
- Prompt constant `REFINE_SYSTEM_PROMPT`: instruct LLM to distill the message into ONE concise durable fact/preference statement in the message's language; reply exactly one line, or empty if nothing durable.
- `refine(text) -> str`: `context.llm_generate(chat_provider_id=config.chat_provider_id, prompt=text, system_prompt=REFINE_SYSTEM_PROMPT)` → `completion_text.strip()`. Empty/whitespace → `""`.
- Audit: `LearningLog(stage="refine", input_text=text, output_text=refined, provider_id=...)`.
- Failures (provider missing, network, timeout) → log error, return `""`, pipeline continues.

### R004 — Reinforce stage
- If refined non-empty and len(refined) >= MIN_LEARN_CHARS:
  - store `Memory(content=refined, importance=0.5, source="learning", dedup_hash=dedup_hash(refined))` unless a Memory with the same hash already exists (`exists_hash`).
  - audit `LearningLog(stage="reinforce", input_text=refined, output_text="", provider_id="")`.
- No KB upload for learned memories in 005 (004 handles KB only for auto memories; learned memories stay SQLite-only).

### R005 — Concurrency & ordering
- `LearningService` owns `asyncio.Semaphore(2)` guarding refine calls.
- All learning work runs in the existing pipeline background task (per message); no additional queue (KISS); each message independent.
- Never raise into the pipeline: every stage wrapped, errors logged.

### R006 — Repo additions
- `MessageRepo.exists_hash(h) -> bool` and `MessageRepo.add` already exist (count/add).
- `MemoryRepo.exists_hash(h) -> bool`.
- `LearningLogRepo.add` exists (001).

### R007 — Pipeline & lifecycle wiring
- `MessagePipeline` gains `learning_service` (optional, default None); `_after_stages` calls `await learning_service.maybe_learn(event, sender_id)` after memory stage.
- Lifecycle `on_load` creates `LearningService(self.context, config)` when `enable_adaptive_learning` (or unconditionally; service self-gates), passes into pipeline.

### R008 — Error isolation
- Single message failures never crash the plugin; `LearningLog` audit rows are best-effort.

## Non-Goals
- Retry/backoff, batch refinement, reinforcement learning on top of KB, per-user learning budgets, memory importance recalibration.

## Acceptance Criteria
- Unit tests (mocked `llm_generate`, temp sqlite):
  - degrade mode stores MessageRecord only, no Memory
  - filter rejects commands/short/empty; stores MessageRecord + filter log
  - refine calls llm_generate with system prompt and prompt text; strips output
  - reinforce stores Memory with source="learning" and hash dedup (`exists_hash` prevents duplicate Memory)
  - refine failure → no Memory, pipeline completes
  - semaphore limits concurrency (assert max concurrent mocked calls <= 2)
  - pipeline `_after_stages` calls both memory and learning services
- `opencode debug lsp diagnostics` clean; `uv run ruff check .` clean; `uv run pytest -q` green.

## Constraints
- AstrBot `>=4.27.3,<5.0.0`; only `llm_generate` API; no provider SDK imports.
- No global singletons; per-plugin-instance service; English comments; ruff 100 col.

## Out of Scope
- Learning from bot's own replies (only user messages flow through `on_message` pipeline).

## References
- `docs/ARCHITECTURE.md` (learning pipeline), `docs/CONFIG.md` (`chat_provider_id`), specs 001/003/004.
