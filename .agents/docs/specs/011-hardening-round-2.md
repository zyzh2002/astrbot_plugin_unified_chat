# Spec 011: Hardening Round 2 — Full Defect Remediation

Status: approved 2026-08-28. Target release: v0.3.0.
Source: full-codebase defect review (3 parallel review passes, findings verified against source).

## Problem Summary

A defect-focused review of the whole plugin confirmed 4 high, ~15 medium and ~15 low
severity issues. All are in scope for this cycle (user decision: fix everything, including
low; include the Rust build-profile changes; ship as v0.3.0).

## Requirements

### R1 Timezone consistency (high)

SQLite DATETIME columns store aware-UTC wall-clock strings and read back **naive**
datetimes. Every comparison must therefore use aware-UTC now, and every
`.timestamp()` conversion must first attach UTC.

- `repo.py` `get_by_hash` / `get_visible_by_id` / `get_visible_by_kb_doc_ids` compare
  `expires_at` against naive local `datetime.now()` — on UTC+X hosts memories vanish
  early (forget / KB-retrieval / visibility), on UTC−X hosts dead memories win the dedup
  gate. All three sites plus the `now: datetime | None = None` defaults
  (`search_by_keyword`, `MemoryFts.search`, `get_by_ids`) must use `datetime.now(UTC)`.
- `MemoryFts.search` binds a raw `datetime` through a `text()` param, relying on the
  deprecated sqlite3 default adapter. Bind a preformatted UTC wall-clock string instead
  (`"%Y-%m-%d %H:%M:%S.%f"`).
- `MessageScanRepo.distinct_umos` / `distinct_group_umos` call `.timestamp()` on the
  naive UTC wall-clock value, skewing by the host UTC offset. The proactive silence gate
  fires into active groups (UTC+X) or never fires (UTC−X). Convert with
  `.replace(tzinfo=UTC)` when naive. Add explicit ordering: `distinct_umos` orders by
  `max(created_at) DESC` (slang mining wants recently active sessions);
  `distinct_group_umos` orders ASC (proactive wants the quietest sessions) — the LIMIT
  subset becomes deterministic.
- Add `PRAGMA busy_timeout=5000` on connect.

### R2 KB migration safety (high)

`run_migration` snapshots chunks in RAM, deletes ALL documents, then re-uploads; any
upload failure leaves the KB emptied with no rollback. Redesign:

- Per-document swap: for each snapshotted doc, delete it, then upload its replacement;
  if the upload fails, best-effort re-upload the original chunks under an
  `__orphan_<n>_` name and abort. Failure scope = one document, not the whole KB.
- The `migration:<kb>:running` KV flag survives process crashes forever. Store
  `{"started": <epoch>}`; flags older than 6 h are stale and get cleared by
  `is_running`. Additionally `on_load` sweeps all `migration:%:running` keys
  (new `kv_delete_prefix` helper).
- Migration outcomes are currently discarded. Persist
  `migration:<kb>:last_result` KV on success AND failure, write a `migration`
  LearningLog row on failure too, surface the last result in `get_status_async`, and
  log `task.result()` in `_log_migration_done` (guarding `cancelled()`).
- The embedding-provider snapshot KV is overwritten at every `on_load` while the
  migration signal (`_needs_migration`) is RAM-only and manual — a restart erases the
  drift signal. Write the snapshot only on first boot (missing key); update it only
  after a successful memory-KB migration.

### R3 Filter decoupling and destructive-command guards (high)

- `blacklist_users` / `blocked_keywords` are only enforced inside the
  `humanize_enable` branch of `handle_message`, but `_conf_schema.json` documents them
  as unconditional. Move a pre-filter (blacklist + blocked keywords, commands exempt —
  matching the gate's ordering) to the top of `handle_message`, independent of
  `humanize_enable`; hits stop the event before recording/learning.
- `memory_service.forget_session("")` (isolation off) selects the whole global pool:
  one `/umem reset` wipes all shared memories. Guard: empty `session_id` is a no-op in
  `forget_session` and in `MemoryAdminRepo.list_by_session` / `delete_by_session`;
  `/umem reset` explains that reset requires session isolation when it is off.
- `HumanizeService.process` drains the unreplied cache for ANY allowed decision,
  including `command` / `private` / `disabled` — a `/help` discards accumulated chatter
  context. Drain only for reasons that produce an actual reply (`trigger_keyword`,
  `wake`, `probability` passed + air passed).

### R4 Data retention and async backup (medium)

- `messages` and `learning_logs` grow forever (one row per chat message each).
  New config keys `message_retention_days` (default 90) and
  `learning_log_retention_days` (default 30), `0` = keep forever; wire through
  DEFAULTS / PluginConfig / from_dict (clamped >= 0) / to_dict / `_conf_schema.json`.
  The daily 03:00 cron purges rows older than the cutoffs.
- `run_backup` (sqlite backup API + pruning) runs synchronously on the event loop in
  the daily tick and `/umem backup`. Wrap both call sites with `asyncio.to_thread`.

### R5 Humanize gate race (medium)

AstrBot dispatches every event as its own task; `process()` separates the sync
`decide()` from `commit_reply()` by an await (air reader, up to 8 s), so concurrent
group messages compute probability from stale state and can double-reply, and both may
drain the unreplied cache. Serialize `decide -> air -> commit` per session with a
per-session `asyncio.Lock` inside `HumanizeService`.

### R6 Learning loop fixes (medium/low)

- Slang inference never advances status: inferred terms stay `candidate` and are
  re-inferred daily (recurring LLM spend; terms beyond top-50 starve). On successful
  `set_meaning`, transition the term to `inferred`. `/uslang list` shows candidates AND
  inferred terms, with FULL meanings (no `[:40]` truncation — the truncation is a
  prompt-injection review blind spot). Injected meanings are quoted.
- `mine_terms` counts single CJK characters (regex `[\u4e00-\u9fff]{1,}`), flooding
  hits with common chars. Require `{2,}` for CJK tokens.
- `inject_composer` appends slang first and blind-truncates the tail, silently dropping
  the affinity/mood lines. Compose budget-aware: tone and mood first, slang lines added
  while the block fits `MAX_BLOCK_CHARS`, no trailing truncation.
- Affinity decay loops over an arbitrary 500-row LIMIT with read-modify-write clobbering
  concurrent bumps. Replace with one SQL UPDATE
  (`score = 50 + (score-50)*0.9` clamped, WHERE the change >= 0.01).
- `learning_jobs.run` and the proactive `_run` loop swallow ALL exceptions silently
  (the existing `_log_error` helpers are never called). Replace with try/except +
  `_log_error`.
- `kv_set` is get-then-insert; a race raises IntegrityError, and the proactive opener
  sends BEFORE `_remember_sent`, so the loser loses its cooldown. Make `kv_set` an
  atomic `INSERT ... ON CONFLICT(key) DO UPDATE` upsert.
- `set_mood` passes a float into the str-typed KV; store `str(round(scalar, 6))`.

### R7 Per-message full-table scan (medium)

`maybe_store` hydrates every Memory row per qualifying message just to compute a
per-sender frequency. Add `MemoryRepo.sender_stats(source, since)` returning
`(count, max_created_at)`; `compute_importance(content, freq, newest)` takes the stats
directly.

### R8 FTS desync governance (medium/low)

- `MemoryFts.index_add/index_remove` swallow all failures silently and permanently.
  Log a warning on failure.
- `MemoryRepo.delete_expired` deletes rows without FTS cleanup and has no callers —
  remove it.
- Add `MemoryFts.reconcile()`: delete FTS rows whose memory_id is gone, insert rows
  missing from FTS; run in the daily cron.

### R9 Lifecycle and task hygiene (medium/low)

- `main.py` sets `_initialized = True` even when `on_load` internally failed
  (`load_failed: ...`). Only mark initialized when lifecycle status is `loaded`.
- `task.exception()` on a cancelled task raises `CancelledError` (not caught by
  `suppress(Exception)`): guard with `task.cancelled()` in `pipeline._log_done` and
  `lifecycle._log_migration_done`. Prune done tasks from `self._migration_tasks`.

### R10 Unbounded in-memory state (low)

`ReplyGate._states` (plus per-user attention), `UnrepliedCache._data`,
`ChatService._buffers/_seen`, `ProactiveService._last_sent`,
`MemorySummarizer._counters` grow one entry per session forever. Give each a
`sweep()` method that evicts entries beyond its own horizon; the cron calls
`sweep()` on injected targets daily.

### R11 Native extension and build profile (medium/low)

- Remove `panic = "abort"` from the release profile: PyO3 needs unwind to convert
  panics into Python exceptions; with abort, any Rust panic kills the whole bot.
- Remove `target-cpu=x86-64-v3` from the manylinux wheel config: AVX2 codegen SIGILLs
  on pre-Haswell CPUs and cannot be caught by the Python fallback. Update AGENTS.md
  accordingly.
- `fallback.chunk_text` diverges from Rust on invalid input (negative args silently
  skip instead of raising). Raise `ValueError` on negative `chunk_overlap` (matching
  Rust's OverflowError path); keep `chunk_size <= 0 -> []` (matches Rust). Add a
  cross-implementation parity test (runs when the compiled module is importable).
- `try_load_cached` loads any binary under `data/native/<version>/` without verifying
  integrity; write a `.sha256` sidecar at download time and verify before import.
- `resolve_data_dir`: the `StarTools.get_data_dir()` tier never resolves (caller-frame
  resolution) — pass `plugin_name="astrbot_plugin_unified_chat"`. Tier-4 fallback dir
  name aligns to `data/plugin_data/astrbot_plugin_unified_chat`.
- Message capture dedup is global: identical text from different sessions records once.
  Scope `MessageRepo.exists_hash` by umo, add a partial unique index on
  `(dedup_hash, umo)` (schema version 3 -> 4, dedup legacy rows keeping min id), use
  `INSERT OR IGNORE`, and remove the dead duplicate-add block in `maybe_learn`.
- Fix the stale `requirements.txt` comment (dev deps live in `[dependency-groups]`).

### R12 Docs and release

- `.agents/docs/plan.md`: add Phase 9 (hardening round 2) as DONE.
- Human docs (Chinese, `docs/`): CONFIG.md new retention keys; OPERATION.md migration
  behavior + stale-flag recovery; ARCHITECTURE.md UTC convention note.
- Version bump to 0.3.0 in `metadata.yaml` + `pyproject.toml` after all tasks pass.

## Out of scope / deferred

- Module-level `asyncio.Lock` in `database.py`: harmless under AstrBot's single event
  loop; a clarifying comment is added instead of a lazy-per-loop redesign.
- Admin-only gating for destructive commands: VERIFIED ALREADY PRESENT — `main.py`
  decorates `/umem`, `/uslang`, `/upersona`, `/unified_migrate` with
  `filter.permission_type(filter.PermissionType.ADMIN)`. No work needed.
- Rust `unwrap`-surface and integer-overflow audit beyond the build-profile change
  (current panic surface verified unreachable for valid inputs).

## Constraints

Inherited from AGENTS.md: relative imports only; flat config schema with valid types;
`tables=_PLUGIN_TABLES` on create_all; no Chinese in code/comments/commits; `__init__`
must not raise; a single message must never crash the plugin; per-task verification
`uv run pytest -q` + `uv run ruff check .`; Rust tasks additionally `cargo test` and
`uv run maturin develop --release` smoke import.
