# Spec 006 — Hardening, Migration & Distribution

## Goal
Finish the plugin: embedding-dimension migration via full index rebuild (`/unified_migrate`), enriched `/unified_status`, verified terminate cleanup, manylinux wheel build, CI pipeline, and Docker e2e contract checks.

## Context
- Verified against AstrBot `v4.27.3`:
  - `kb_manager.get_kb_by_name(name) -> KBHelper | None`
  - `KBHelper.list_documents(offset, limit, search) -> list[KBDocument]` (metadata in global kb db; `KBDocument` has `doc_id`, `file_name`)
  - `KBHelper.get_chunks_by_doc_id(doc_id, offset, limit) -> list[dict]` with `content` per chunk (chunks live in vec db `doc.db`)
  - `KBHelper.delete_document(doc_id)` (chunks + metadata), `upload_document(..., pre_chunked_text=[...])` (re-embeds with current provider)
  - Changing `embedding_provider_id` on an existing KB does NOT re-embed existing chunks — hence rebuild required.
- `UnifiedKV` KV table exists (001); `Memory` rows track `kb_doc_id` (004).
- AstrBot provides `pytest` inside Docker for e2e; dev venv has no `astrbot`.

## Requirements

### R001 — needs_migration detection
- On `on_load` (after config resolve): read KV `embedding_provider_snapshot`; if non-empty and != current `config.embedding_provider_id` → set in-memory `self._needs_migration = True` and log warning; then write snapshot = current id. First run (no snapshot) → no flag.

### R002 — Migration background task (full index rebuild)
- `migrate_kb(event, kb_name)` in lifecycle (already exposed by `/unified_migrate`):
  - empty kb_name → usage string (existing behavior)
  - start `asyncio.create_task(self._run_migration(kb_name), name="unified_chat_migration")` if not already running (KV flag `migration:<kb_name>:running`); return `"Migration for '<kb>' started in background. Check /unified_status."`
  - if already running → return running message
- `_run_migration(kb_name) -> str` (service `services/migration_service.py`):
  1. set KV running flag
  2. `helper = kb_manager.get_kb_by_name(kb_name)`; missing → clear flag, log, return error
  3. snapshot: page `list_documents` (offset step 100) → for each doc `get_chunks_by_doc_id` (page step 100) → `(file_name, [contents])`
  4. `delete_document(doc.doc_id)` for every doc (removes old-dimension chunks + metadata)
  5. re-upload each snapshot entry via `upload_document(file_name=..., file_content=None, file_type="txt", pre_chunked_text=chunks)`
  6. if kb_name == `config.memory_kb_name`: rewrite `Memory.kb_doc_id` values (doc_id → new doc_id map) via `MemoryRepo.update_kb_doc_id`; source of truth stays SQLite
  7. clear KV flag; write `LearningLog(stage="migration", input_text=kb_name, output_text=f"{n} docs")`; log summary
- Any exception mid-run: clear flag, log error, leave KB in best-effort state (already re-uploaded docs are valid with new dims; log warns of partial migration). Never raises into the command handler.
- Only one migration runs at a time globally (KV flag `migration:<kb_name>:running` checked per KB; `asyncio.Lock` per service for global serialization).

### R003 — Enriched status
- `get_status()` extended: include memory count (`MemoryRepo.count` cached at load + refreshed lazily is NOT async — keep static info), so instead:
  - `unified_status` command (main.py) calls new `async def get_status_async()` returning string with: status, data_dir, rag_kbs, agentic, memory_cleanup_days, `needs_migration: yes/no`, migration running flag, counts: memories, messages, learning logs per stage (queried via repo count helpers, failures → "n/a")
- Keep sync `get_status()` for backward compat (basic string).

### R004 — Repo count helpers
- `MemoryRepo.count() -> int`, `LearningLogRepo.count_by_stage(stage) -> int` (stage counts for filter/refine/reinforce), reuse `MessageRepo.count`.

### R005 — Terminate cleanup verification
- e2e-style unit test: after `on_unload`, cron task cancelled, engine disposed (get_session raises RuntimeError).

### R006 — Manylinux wheel + CI
- `.github/workflows/ci.yml`: jobs on ubuntu-latest — `uv sync`, `uv run ruff check .`, `uv run pytest -q`, `cargo test -p unified_chat_native`, `uv run maturin build --release --strip --manylinux 2_28` (artifact upload).
- Local verification command documented: `uv run maturin build --release --strip --manylinux 2_28` produces wheel.
- `requirements.txt` already minimal (runtime deps by AstrBot).

### R007 — Docker e2e contract
- `tests/e2e/test_migration_e2e.py` (importorskip astrbot): constructs MigrationService against real `Context` stubs is out of scope; instead verify API contract used by migration: `KBHelper` has `list_documents/get_chunks_by_doc_id/delete_document/upload_document` attributes and `kb_manager.get_kb_by_name` exists; `LLMResponse.completion_text` property exists (guards 005).
- `tests/e2e/test_rag_e2e.py` already exists.

## Non-Goals
- Incremental migration, per-document retry, UI dashboard, marketplace listing automation.

## Acceptance Criteria
- Unit tests: needs_migration snapshot KV logic (first run / same id / changed id); migration with mocked kb manager/helper — snapshot paging, delete+re-upload calls, memory kb_doc_id rewrite, KV flag lifecycle, failure path clears flag; concurrent start returns running message; status_async string contains counts and flags; repo count helpers correct.
- `opencode debug lsp diagnostics` clean; `uv run ruff check .` clean; `uv run pytest -q` green; `cargo test` green; `uv run maturin build --release --strip --manylinux 2_28` succeeds (documented; run when docker available).
- CI workflow file valid YAML.

## Constraints
- AstrBot `>=4.27.3,<5.0.0`; no vendor vector DB; migration never raises into command path.
- No global singletons (MigrationService per lifecycle, internal `asyncio.Lock`).
- English comments; ruff 100 col.

## Out of Scope
- Windows/macOS wheels; aarch64.

## References
- `docs/OPERATION.md` (migration note), `docs/CONFIG.md` (迁移), specs 001/004/005.
