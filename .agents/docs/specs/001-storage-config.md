# Spec 001 — Storage & Config Foundation

## Goal
Establish persistent storage and configuration lifecycle so all later domains (RAG, chat, memory, learning) can rely on a stable SQLite + config substrate.

## Context
- Plugin must run on AstrBot `>=4.27.3,<5.0.0`, linux `manylinux_2_28 x86_64` only, `sqlite` only.
- Current scaffold has `PluginConfig.from_dict`, `storage/models.py`, `storage/database.py:get_engine` with WAL, but no `StarTools.get_data_dir()` wiring, no session/repo helpers, no KV helper, no lifecycle binding.
- Spec must precede code per project rule.

## Requirements

### R001 — Config loading
- `PluginConfig` is the single typed view over WebUI `_conf_schema.json`.
- On `PluginLifecycle.on_load()`, resolve `data_dir` as:
  1. `raw["data_dir"]` if non-empty and directory exists/creatable, else
  2. `StarTools.get_data_dir()` (preferred), else
  3. `get_astrbot_data_path() / "plugin_data" / "astrbot_plugin_unified_chat"`, else
  4. temp fallback `./data/plugin_data/unified_chat` (never write into plugin source dir).
- Obtain raw config via `context.get_config()` if available, otherwise empty dict (defensive).
- Validate `memory_cleanup_days >= 1`, `importance_threshold in [0,1]`, coerce types; invalid values fall back to defaults and log warning.
- Expose `PluginConfig.to_dict()` round-trip and `config.data_dir` as resolved absolute path string.

### R002 — Storage path & SQLite
- DB file: `<data_dir>/unified_chat.db`.
- `get_engine(db_path)` is idempotent, process-wide singleton guarded by `asyncio.Lock`; concurrent callers share the same engine.
- `PRAGMA journal_mode=WAL`, `synchronous=NORMAL`, `cache_size=20000` on connect.
- `SQLModel.metadata.create_all` on first connect.
- Provide `async def get_session() -> AsyncSession` async context manager yielding a session bound to the singleton engine. Session commits on success, rollbacks on exception, always closes.
- Provide `async def close_engine()` that disposes engine and resets singleton to `None` under lock (test helper `reset_engine_for_tests()` resets `_engine` without dispose for in-memory tests).

### R003 — Models (no change to existing columns, add missing indices/fields)
- Keep `MessageRecord`, `Memory`, `LearningLog` exactly as in `storage/models.py` except:
  - `Memory.expires_at` already exists; ensure index on it for cleanup.
  - Add optional `UnifiedKV` table for generic key-value (migration flags, etc.).
- All `created_at` default to UTC now, indexed.

### R004 — Repository helpers
- Provide `storage/repo.py` with thin async helpers using `get_session()`:
  - `MessageRepo.add(record) -> MessageRecord`, `count() -> int`
  - `MemoryRepo.add(memory) -> Memory`, `list_all() -> list[Memory]`, `delete_expired(threshold, cutoff) -> int`
  - `LearningLogRepo.add(log) -> LearningLog`
- No business logic inside repo; pure persistence.

### R005 — KV wrapper
- `UnifiedKV(SQLModel, table=True)` with `key: str PK`, `value: str`, `updated_at: datetime`.
- `storage/kv.py` helpers: `async def kv_get(key) -> str|None`, `kv_set(key, value)`, `kv_delete(key)`.

### R006 — Lifecycle integration
- `PluginLifecycle.__init__` stores `plugin`, `context`, init `_config: PluginConfig|None`, `_data_dir: Path|None`, `_engine: AsyncEngine|None`, `_status`.
- `on_load()` does: resolve config -> resolve data_dir -> `get_engine(db_path)` -> set `status="loaded"`. Never raise; log error and keep `status="load_failed"` on exception.
- `on_unload()` calls `close_engine()` (suppressed exceptions), sets `status="unloaded"`.
- `get_status()` returns human string including `data_dir` and `config` summary after load.
- `migrate_kb` remains stub in this spec (deferred to later spec).

### R007 — Isolation & error handling
- No global singletons except `_engine` in `database.py` as defined.
- All storage calls log and propagate only via explicit error handling in lifecycle; single repo failure must not crash plugin (future pipeline will wrap).

## Non-Goals
- RAG, chat, memory, learning business logic.
- Embedding migration rebuild (deferred).
- Rust changes.

## Acceptance Criteria
- `uv run pytest tests/test_storage_config* -q` passes with mocked `Context` and `StarTools`.
- `opencode debug lsp diagnostics unified_chat/config.py|storage/*.py|core/lifecycle.py` is clean.
- `uv run ruff check .` clean.
- Concurrent `get_engine` returns same instance.
- `kv_set/get/delete` round-trip persists across sessions.

## Constraints
- AstrBot `>=4.27.3,<5.0.0`, `sqlite+aiosqlite`, `sqlmodel>=0.0.22`.
- Persistent data only under `data/plugin_data/<plugin_name>/`.
- `__init__` must not raise.

## Out of Scope
- `manylinux` wheel, cron, RAG tool injection.

## References
- `docs/ARCHITECTURE.md` module list, `docs/CONFIG.md` config keys, `docs/OPERATION.md` backup path.
- `.agents/docs/plan.md` Phase 1.
