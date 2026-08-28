# Hardening Round 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 34 review-confirmed defects (4 high, ~15 medium, ~15 low) and prepare v0.3.0.

**Architecture:** Sequenced TDD tasks on branch `fix/hardening-round-2`, one conventional commit per task. Storage-layer fixes first (timezone), then migration safety, filtering, retention, humanize/learning behavior, then low-severity hygiene, docs, and release prep.

**Tech Stack:** Python 3.12+ (asyncio, SQLModel/SQLAlchemy/aiosqlite, SQLite FTS5), Rust 2021 (PyO3/maturin), pytest-asyncio.

**Spec:** `.agents/docs/specs/011-hardening-round-2.md`

## Global Constraints

- Package-relative imports everywhere inside `unified_chat/`; `main.py` try-relative/except-absolute only.
- `_conf_schema.json` stays FLAT `{key: {type, description, default, ...}}`; types from `int float bool string text list file object template_list dict` only.
- `SQLModel.metadata.create_all(..., tables=list(_PLUGIN_TABLES))` — never bare.
- All code/comments/commits in English; `docs/` human docs in Chinese.
- `__init__` must not raise; one message must never crash the plugin.
- Per task: `uv run pytest -q` and `uv run ruff check .` green before committing. Rust tasks add `cargo test -p unified_chat_native` and `uv run maturin develop --release` smoke import.
- SQLite DATETIME columns store aware-UTC wall clock and read back naive; every new comparison uses `datetime.now(UTC)` and every `.timestamp()` attaches UTC to naive values.

---

### Task 1: Timezone unification

**Files:**
- Modify: `unified_chat/storage/repo.py` (lines ~404, ~423, ~444; defaults at ~182, ~306, ~347; `distinct_umos` ~488, `distinct_group_umos` ~508)
- Modify: `unified_chat/storage/database.py` (connect pragmas ~141)
- Test: `tests/test_repo.py`

**Interfaces:**
- Produces: module helper `_utcnow() -> datetime` and `_utc_wall(now: datetime) -> str` in `repo.py`; `MessageScanRepo.distinct_umos/distinct_group_umos` return `(umo, float_epoch)` with explicit ordering.

- [ ] **Step 1: Failing tests** — expiry visibility is offset-independent; epoch has no local skew.

```python
def test_distinct_group_umos_epoch_is_utc(make_db):
    # insert a MessageRecord with created_at = now(UTC); returned epoch
    # must be within 5s of time.time() regardless of host TZ
    ...
def test_get_visible_by_id_uses_utc_now(make_db):
    # memory with expires_at = utcnow + 1h stays visible even when the
    # host-local wall clock is past it (simulated via monkeypatched _utcnow
    # returning aware datetime one hour ahead of local naive now)
    ...
```

- [ ] **Step 2: Run** `uv run pytest tests/test_repo.py -q` — expect FAIL.
- [ ] **Step 3: Implement** — add `_utcnow = lambda: datetime.now(UTC)`; replace the three naive call sites and the three `now or datetime.now()` defaults; in `MemoryFts.search` bind `"now": _utc_wall(now)` where `_utc_wall` formats `"%Y-%m-%d %H:%M:%S.%f"` on the UTC wall clock (attach UTC to naive, convert aware to UTC first). In both scan repos:

```python
rows = (
    await session.exec(
        select(MessageRecord.umo, func.max(MessageRecord.created_at))
        .group_by(MessageRecord.umo)
        .order_by(func.max(MessageRecord.created_at).desc())  # ASC in distinct_group_umos
        .limit(limit)
    )
).all()
...
ts = last_ts if last_ts.tzinfo else last_ts.replace(tzinfo=UTC)
result.append((str(umo), float(ts.timestamp())))
```

- [ ] **Step 4: database.py** connect event adds `cursor.execute("PRAGMA busy_timeout=5000;")` next to the WAL pragmas.
- [ ] **Step 5: Run** full suite + ruff; expect PASS (existing DeprecationWarning count drops).
- [ ] **Step 6: Commit** `fix: use aware-utc consistently in storage comparisons and epoch conversion`

### Task 2: KB migration safety

**Files:**
- Modify: `unified_chat/services/migration_service.py` (rewrite `run_migration` core, flag format)
- Modify: `unified_chat/storage/kv.py` (add `kv_delete_prefix`, `kv_keys_with_prefix`), `unified_chat/core/lifecycle.py` (`on_load` snapshot logic + sweep, `get_status_async`, `_log_migration_done`)
- Test: `tests/test_migration_service.py`

**Interfaces:**
- Produces: `MigrationService.run_migration(kb_name) -> str` (same signature); KV keys `migration:<kb>:running` = `json {"started": epoch}`; `migration:<kb>:last_result` = result string; `kv_delete_prefix(prefix: str) -> int`; `kv_keys_with_prefix(prefix: str) -> list[str]`.
- `run_migration` must call `kv_set("embedding_provider_snapshot", self.config.embedding_provider_id)` ONLY on success when `kb_name == self.config.memory_kb_name`.

- [ ] **Step 1: Failing tests** — (a) upload fails on doc 2 of 3 → docs 1,3 (or their re-uploaded content) still resolvable via `list_documents`, and `last_result` KV contains "failed"; (b) flag written with old epoch (>21600s) → `is_running` returns False; (c) success writes LearningLog + snapshot KV; failure does not touch snapshot.
- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement** per-doc loop:

```python
for idx, (file_name, chunks) in enumerate(snapshots):
    doc_id = doc_ids[idx]
    await helper.delete_document(doc_id)
    try:
        await helper.upload_document(
            file_name=file_name, file_content=None,
            file_type="txt", pre_chunked_text=chunks,
        )
    except Exception as exc:
        with contextlib.suppress(Exception):
            await helper.upload_document(
                file_name=f"__orphan_{idx}_{file_name}", file_content=None,
                file_type="txt", pre_chunked_text=chunks,
            )
        raise RuntimeError(f"re-upload failed for '{file_name}': {exc}") from exc
```

Flag helpers: `_write_flag` stores `json.dumps({"started": time.time()})`; `is_running` parses and treats `time.time() - started > 21600` as stale (delete + False). Wrap body so both success and failure write `kv_set(f"migration:{kb}:last_result", result)` and a LearningLog row (`stage="migration"`, `output_text=result`). `on_load`: after creating `MigrationService`, `for key in await kv_store.kv_keys_with_prefix("migration:"): if key.endswith(":running"): await kv_store.kv_delete(key)`; snapshot block becomes `if snapshot is None: await kv_set("embedding_provider_snapshot", config.embedding_provider_id)` and `_needs_migration = bool(snapshot is not None and config.embedding_provider_id and snapshot != config.embedding_provider_id)`. `get_status_async` appends `migration_last=<kb>:<text>` from KV when present.
- [ ] **Step 4: Run** suite + ruff; PASS.
- [ ] **Step 5: Commit** `fix: make kb migration per-document with stale-flag recovery and persisted results`

### Task 3: Filter decoupling and destructive-command guards

**Files:**
- Modify: `unified_chat/core/lifecycle.py` (`handle_message` pre-filter, `umem` reset message), `unified_chat/services/humanize_service.py` (`matches_any`/`is_blacklisted` helpers + drain-only-on-reply), `unified_chat/services/memory_service.py` (`forget_session` guard), `unified_chat/storage/repo.py` (`list_by_session`/`delete_by_session` empty guards)
- Test: `tests/test_lifecycle_chat.py`, `tests/test_humanize.py`, `tests/test_memory_service.py`

**Interfaces:**
- Produces: `humanize_service.matches_any(text: str, words: list[str]) -> bool`, `humanize_service.is_blacklisted(event: Any, config: Any) -> bool`, `humanize_service.blocked_keyword_hit(event: Any, config: Any) -> bool` (all command-exempt for blocked keywords); `MemoryService.forget_session("")` returns 0.

- [ ] **Step 1: Failing tests** — humanize off + blacklisted sender → `handle_message` stops event and pipeline never records; blocked keyword in command text (`/umem search <blocked>`) is NOT stopped; `/umem reset` with isolation off returns guidance string and deletes nothing; `process()` with reason `command` leaves cache entries intact.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.** Module-level helpers in `humanize_service.py`:

```python
def matches_any(text: str, words: list[str]) -> bool:
    lowered = (text or "").lower()
    return any(str(w).strip() and str(w).lower() in lowered for w in words)

def is_blacklisted(event, config) -> bool:
    sender = ""
    with contextlib.suppress(Exception):
        sender = str(event.get_sender_id() or "")
    blacklist = {str(u) for u in (getattr(config, "blacklist_users", None) or [])}
    return bool(sender) and sender in blacklist

def blocked_keyword_hit(event, config) -> bool:
    text = getattr(event, "message_str", "") or ""
    if ChatService.is_command(text):
        return False
    return matches_any(text, list(getattr(config, "blocked_keywords", None) or []))
```

`handle_message` start:

```python
if self._config is not None:
    try:
        from ..services.humanize_service import blocked_keyword_hit, is_blacklisted
        if is_blacklisted(event, self._config) or blocked_keyword_hit(event, self._config):
            event.stop_event()
            return
    except Exception:
        <log via logger>
```

`ReplyGate.decide` and `HumanizeService.blocked_keyword_hit` delegate to these helpers (single source of truth). `process()` drain change: only drain when `decision.reason in ("trigger_keyword", "wake") or (decision.reason == "probability" and air passed)`. `forget_session`:

```python
async def forget_session(self, session_id: str | None) -> int:
    if not session_id:
        return 0
    rows = await repos.MemoryAdminRepo.list_by_session(session_id)
    return await self._delete_memories(rows)
```

`umem` reset branch: when `not session_id` return `"[umem] reset requires memory_session_isolation; refusing to clear the shared pool"`. Repo guards: `list_by_session("")` / `delete_by_session("")` return `[]` / `0`.
- [ ] **Step 4: Run** suite + ruff; PASS.
- [ ] **Step 5: Commit** `fix: enforce blacklist and blocked keywords independent of humanize and guard destructive memory reset`

### Task 4: Data retention and async backup

**Files:**
- Modify: `unified_chat/config.py` (DEFAULTS, dataclass, from_dict clamp, to_dict), `_conf_schema.json` (two flat int entries), `unified_chat/storage/repo.py` (`MessageRepo.delete_older_than`, `LearningLogRepo.delete_older_than`), `unified_chat/core/cron.py` (purge step), `unified_chat/services/backup_service.py` (`daily_tick` async), `unified_chat/core/lifecycle.py` (`/umem backup` to_thread)
- Test: `tests/test_cron.py`, `tests/test_config.py`, `tests/test_repo.py`, `tests/test_lifecycle_memory.py`

**Interfaces:**
- Consumes: `config.message_retention_days: int`, `config.learning_log_retention_days: int` (0 = keep forever).
- Produces: `MessageRepo.delete_older_than(cutoff: datetime) -> int`, `LearningLogRepo.delete_older_than(cutoff: datetime) -> int`, `BackupService.daily_tick()` stays awaitable but runs the sync backup in a thread.

- [ ] **Step 1: Failing tests** — purge removes only rows older than cutoff; cutoffs 0 days skip purge; config defaults/clamps; cron tick calls purge with computed cutoffs; `daily_tick` runs backup off-loop (assert `run_backup` called inside `asyncio.to_thread` via monkeypatch recording thread id).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.** Repo method shape:

```python
@staticmethod
async def delete_older_than(cutoff: datetime) -> int:
    async with get_session() as session:
        rows = (
            await session.exec(select(MessageRecord).where(MessageRecord.created_at < cutoff))
        ).all()
        for row in rows:
            await session.delete(row)
        await session.commit()
        return len(rows)
```

Cron `_tick` gains a purge step between memory cleanup and backup (guard `self._config is not None` — pass config into the cron constructor from lifecycle):

```python
if self._config is not None and self._repo_purge is not None:
    try:
        await self._repo_purge(self._config)
    except Exception:
        self._log_error("retention purge")
```

with a module-level `async def purge_retention(config)` in cron.py that computes cutoffs (`0` days → skip) and calls both repo methods. Backup: `daily_tick` becomes `await asyncio.to_thread(self.run_backup, "daily")`; `/umem backup` uses `await asyncio.to_thread(self._backup_service.run_backup, "manual")`.
- [ ] **Step 4: Run** suite + ruff; PASS.
- [ ] **Step 5: Commit** `feat: add message and learning-log retention with async backups`

### Task 5: Humanize per-session lock

**Files:**
- Modify: `unified_chat/services/humanize_service.py`
- Test: `tests/test_humanize.py`

**Interfaces:**
- Produces: `HumanizeService.process()` serialized per `_session_of(event)`; no signature changes.

- [ ] **Step 1: Failing test** — two events for the same session processed concurrently via `asyncio.gather`, with an air reader that awaits 50 ms; assert `gate.commit_reply` effect (`state.last_reply_ts`) updated exactly once more than baseline and only one drain/merge happened. (Before the fix both decide() calls see the same stale state.)
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** — `self._locks: dict[str, asyncio.Lock]`; `_lock(session)` creates on demand; `process()` wraps its whole body in `async with self._lock(session):`. Locks dict entries are pruned by `sweep()` in Task 10 (keep a `_lock_last_used` map or reuse entries' internal lock state — simplest: sweep removes locks not held and unused since horizon; track `time.monotonic()` last-use per session).
- [ ] **Step 4: Run** suite + ruff; PASS.
- [ ] **Step 5: Commit** `fix: serialize humanize gate decision and commit per session`

### Task 6: Learning loop fixes

**Files:**
- Modify: `unified_chat/services/slang_service.py`, `unified_chat/services/inject_composer.py`, `unified_chat/services/learning_jobs.py`, `unified_chat/services/humanize_proactive.py`, `unified_chat/storage/kv.py`, `unified_chat/storage/repo.py` (`AffinityRepo.decay_all`), `unified_chat/core/lifecycle.py` (`uslang` list)
- Test: `tests/test_learning_depth.py`, `tests/test_inject_composer.py`, `tests/test_kv.py` (new), `tests/test_learning_jobs.py` (new)

**Interfaces:**
- Produces: `AffinityRepo.decay_all(factor: float = 0.9) -> int` (single UPDATE); slang status `"inferred"` is a valid status between candidate and confirmed; `kv_set` atomic upsert.

- [ ] **Step 1: Failing tests** — (a) after `infer_pending_meanings` succeeds for a term, its status is `"inferred"` and a second run does not include it in `list_by_status("candidate")`; (b) `/uslang list` output contains the full >40-char meaning; (c) composer keeps the mood line even when slang lines overflow the budget (slang dropped instead); (d) `decay_all` updates ALL rows >500 and preserves clamping, returns changed count; (e) `kv_set` on a fresh key twice does not raise and last value wins; (f) `mine_terms` ignores single CJK chars; (g) learning_jobs failures call `_log_error` (monkeypatch logger).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement:**

  - `slang_service.infer_pending_meanings`: after `set_meaning(term_obj.id, meaning)` also `await repos.SlangRepo.set_status(term_obj.id, "inferred")`. `mine_terms` regex: `_TOKEN_RE = re.compile(r"[A-Za-z]{2,}|[\u4e00-\u9fff]{2,}")` (drop the single-char CJK branch; bigrams still mined inside the loop). `lifecycle.uslang` list: `pending = list_by_status("candidate") + list_by_status("inferred")` (limit 15 each) and print `t.meaning` untruncated.
  - `inject_composer`: build `tone` (affinity) and `mood` parts first; then append slang header + lines one by one while `len(current + line) + 1 <= MAX_BLOCK_CHARS`; drop non-fitting slang lines; no trailing `…`.
  - `AffinityRepo.decay_all`:

```python
@staticmethod
async def decay_all(factor: float = 0.9) -> int:
    baseline = AffinityRepo.BASELINE
    async with get_session() as session:
        result = await session.exec(
            text(
                "UPDATE user_affinity SET "
                "score = MAX(0, MIN(100, :baseline + (score - :baseline) * :factor)), "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE ABS(:baseline + (score - :baseline) * :factor - score) >= 0.01"
            ),
            params={"baseline": float(baseline), "factor": float(factor)},
        )
        await session.commit()
        return int(result.rowcount or 0)
```

  `_decay_affinity` calls `repos.AffinityRepo.decay_all()`. Remove `all_rows`/`save_score` if no remaining callers (grep first; `band` stays).
  - `kv_set`:

```python
async def kv_set(key: str, value: str) -> None:
    async with get_session() as session:
        await session.exec(
            text(
                "INSERT INTO unified_kv(key, value, updated_at) "
                "VALUES (:key, :value, CURRENT_TIMESTAMP) "
                "ON CONFLICT(key) DO UPDATE SET "
                "value = excluded.value, updated_at = CURRENT_TIMESTAMP"
            ),
            params={"key": key, "value": str(value)},
        )
        await session.commit()
```

  (verify `UnifiedKV` column names first — key/value/updated_at per `models.py`.) `learning_jobs.set_mood`: `kv_store.kv_set(MOOD_KEY, str(round(max(MOOD_MIN, min(MOOD_MAX, float(scalar))), 6)))`.
  - Logging: in `learning_jobs.run` replace each `with contextlib.suppress(Exception):` by `try/except Exception: _log_error("<stage>")`; in `humanize_proactive._run` replace the suppress with `except Exception: self._log_error("tick")`.
- [ ] **Step 4: Run** suite + ruff; PASS.
- [ ] **Step 5: Commit** `fix: advance slang status, budget-aware injection, atomic kv upsert and sql affinity decay`

### Task 7: sender_stats aggregate

**Files:**
- Modify: `unified_chat/storage/repo.py` (`MemoryRepo.sender_stats`), `unified_chat/services/memory_service.py` (`maybe_store`, `compute_importance`)
- Test: `tests/test_memory_service.py`

**Interfaces:**
- Produces: `MemoryRepo.sender_stats(source: str, since: datetime) -> tuple[int, datetime | None]`; `compute_importance(self, content: str, freq: int, newest: datetime | None) -> float`.

- [ ] **Step 1: Failing tests** — `sender_stats` counts rows for source within window and returns newest; `maybe_store` no longer calls `list_all` (monkeypatch asserts absent); importance value equals the old formula for the same inputs (port an existing test's expectations to the new signature).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement:**

```python
@staticmethod
async def sender_stats(source: str, since: datetime) -> tuple[int, datetime | None]:
    async with get_session() as session:
        row = (
            await session.exec(
                select(func.count(), func.max(Memory.created_at))
                .where(Memory.source == source)
                .where(Memory.created_at > since)
            )
        ).one()
        return int(row[0] or 0), row[1]
```

`maybe_store`:

```python
freq, newest = await repos.MemoryRepo.sender_stats(
    sender_id, datetime.now(UTC) - timedelta(days=7)
)
importance = self.compute_importance(text, freq, newest)
```

`compute_importance` keeps the same scoring math (`score_importance(len(content), recency_hours, freq)`) with `recency_hours` derived from `_aware(newest)`.
- [ ] **Step 4: Run** suite + ruff; PASS.
- [ ] **Step 5: Commit** `perf: replace per-message memory table scan with sql aggregate`

### Task 8: FTS reconcile

**Files:**
- Modify: `unified_chat/storage/repo.py` (`index_add`/`index_remove` logging; remove `delete_expired`; add `MemoryFts.reconcile`), `unified_chat/core/cron.py` (call reconcile in `_tick`)
- Test: `tests/test_repo.py` (or `tests/test_memory_depth.py`)

**Interfaces:**
- Produces: `MemoryFts.reconcile() -> tuple[int, int]` (orphans_removed, missing_added).

- [ ] **Step 1: Failing tests** — delete a Memory row directly (bypassing service) then `reconcile()` removes its FTS row; insert a Memory without FTS row then `reconcile()` adds it; failed `index_add` (patch get_session to raise) logs a warning and does not raise.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement:**

```python
@staticmethod
async def reconcile() -> tuple[int, int]:
    async with get_session() as session:
        await MemoryFts._ensure_table(session)
        r1 = await session.exec(text(
            "DELETE FROM memory_fts WHERE memory_id NOT IN (SELECT id FROM memories)"
        ))
        r2 = await session.exec(text(
            "INSERT INTO memory_fts(memory_id, content, session_id) "
            "SELECT id, content, session_id FROM memories "
            "WHERE content != '' AND id NOT IN (SELECT memory_id FROM memory_fts)"
        ))
        await session.commit()
        return int(r1.rowcount or 0), int(r2.rowcount or 0)
```

`index_add`/`index_remove` except blocks: `_log_fts_error("index_add", exc)` writing `logger.warning("[unified_chat] fts index_add failed: %s", exc)`. Delete `MemoryRepo.delete_expired` (verify zero callers first). Cron `_tick` calls `await repos.MemoryFts.reconcile()` inside try/except `_log_error("fts reconcile")`.
- [ ] **Step 4: Run** suite + ruff; PASS.
- [ ] **Step 5: Commit** `fix: log fts maintenance failures and add daily fts reconcile`

### Task 9: Lifecycle and task hygiene

**Files:**
- Modify: `main.py`, `unified_chat/core/pipeline.py` (`_log_done`), `unified_chat/core/lifecycle.py` (`_log_migration_done`, `_migration_tasks` pruning)
- Test: `tests/test_pipeline.py`, `tests/test_lifecycle_*.py` (pick nearest existing file), `tests/test_main.py` if present

**Interfaces:**
- Produces: `_initialized` True only on full load; done-callbacks safe on cancelled tasks.

- [ ] **Step 1: Failing tests** — cancelled task passed to `_log_done`/`_log_migration_done` raises nothing (previously CancelledError escaped into the loop's exception handler); lifecycle with forced `on_load` failure leaves `plugin._initialized` False.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement:**

```python
def _log_done(self, task: asyncio.Task) -> None:
    self._tasks.discard(task)
    if task.cancelled():
        return
    with contextlib.suppress(Exception):
        exc = task.exception()
        if exc is not None:
            self._log_error(f"background: {exc}")
```

Same guard in `_log_migration_done` plus pruning: `self._migration_tasks = [t for t in self._migration_tasks if not t.done()]` at its start. `main.py` (verify exact shape when editing): after `await self.on_load()`, set `self._initialized = getattr(self._lifecycle, "_status", "") == "loaded"`.
- [ ] **Step 4: Run** suite + ruff; PASS.
- [ ] **Step 5: Commit** `fix: detect failed startup and make task done-callbacks cancellation-safe`

### Task 10: State sweep

**Files:**
- Modify: `unified_chat/services/humanize_gate.py`, `unreplied_cache.py`, `chat_service.py`, `humanize_proactive.py`, `memory_summarizer.py`, `humanize_service.py` (lock sweep), `unified_chat/core/cron.py` (sweep targets), `unified_chat/core/lifecycle.py` (wire targets)
- Test: `tests/test_humanize.py`, `tests/test_chat.py`, `tests/test_cron.py`

**Interfaces:**
- Produces: `sweep(now: float | None = None) -> int` on `ReplyGate`, `UnrepliedCache`, `ChatService`, `ProactiveService`, `MemorySummarizer`, `HumanizeService` (delegates to gate/cache/locks); `MemoryCleanupCron(..., sweep_targets: list[Any] | None = None)`.

- [ ] **Step 1: Failing tests** — entries older than each component's horizon are evicted and live entries survive; cron tick calls `sweep()` on injected targets.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** horizons: gate 24 h (monotonic), unreplied cache 2 h idle keys, chat buffers/seen 2 h, proactive `_last_sent` 7 days, summarizer counters 24 h, humanize locks 24 h unused. Cron: `for target in self._sweep_targets or []: try: target.sweep() except Exception: self._log_error("sweep")`; lifecycle passes `[self._humanize, self._humanize.gate, self._humanize.cache, self._chat_service, self._proactive]` (skip None entries; components without the method are skipped via `hasattr`).
- [ ] **Step 4: Run** suite + ruff; PASS.
- [ ] **Step 5: Commit** `feat: evict stale per-session in-memory state via daily sweep`

### Task 11: Native extension and misc low fixes

**Files:**
- Modify: `Cargo.toml` (drop `panic = "abort"`), `pyproject.toml` (drop `[tool.maturin.target...]` section), `AGENTS.md` (target-cpu mention), `unified_chat/native/fallback.py`, `unified_chat/native/bootstrap.py` (sha256 sidecar), `unified_chat/utils/path.py`, `unified_chat/storage/models.py` (MessageRecord unique index), `unified_chat/storage/database.py` (schema v4 + dedup), `unified_chat/core/pipeline.py` (scoped dedup), `unified_chat/storage/repo.py` (`exists_hash` umo param, `add` OR IGNORE), `unified_chat/services/learning_service.py` (remove dead add), `requirements.txt`
- Test: `tests/test_native_parity.py` (new), `tests/test_utils_path.py` (new), `tests/test_schema_migration.py`, `tests/test_pipeline.py`

**Interfaces:**
- Produces: `MessageRepo.exists_hash(h: str, umo: str | None = None) -> bool`; `_SCHEMA_VERSION = 4` with partial unique index `uq_message_dedup ON messages(dedup_hash, umo) WHERE dedup_hash != ''`; `fallback.chunk_text` raises `ValueError` on negative `chunk_overlap`; `resolve_data_dir` tier-2 passes `plugin_name="astrbot_plugin_unified_chat"` and tier-4 uses `astrbot_plugin_unified_chat`.

- [ ] **Step 1: Failing tests** — capture dedup: same text in two umos records twice (fresh engine), duplicate in same umo records once; schema migration from v3 db dedups (hash, umo) pairs and creates the index; `resolve_data_dir` tier 3/4 paths end with `astrbot_plugin_unified_chat`; parity test skips cleanly when native missing, compares vectors when present; `fallback.chunk_text("abc", 2, -1)` raises ValueError.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement:**

  - `Cargo.toml`: remove `panic = "abort"` line. `pyproject.toml`: remove the `[tool.maturin.target.x86_64-unknown-linux-gnu]` block. AGENTS.md: replace the `target-cpu=x86-64-v3` mention with "baseline x86-64 (no target-cpu) for distributed wheels". Run `cargo test -p unified_chat_native` and `uv run maturin develop --release` + smoke `python -c "from unified_chat._native import hash_dedup; print(hash_dedup('hello'))"`.
  - `models.py` MessageRecord: `__table_args__ = (Index("uq_message_dedup", "dedup_hash", "umo", unique=True, sqlite_where=text("dedup_hash != ''")),)`; `database.py` `_SCHEMA_VERSION = 4`, `_needs_migration` required-columns check unchanged, `_migrate_schema` adds:

```python
sync_conn.exec_driver_sql(
    "DELETE FROM messages WHERE dedup_hash != '' AND id NOT IN ("
    "SELECT MIN(id) FROM messages WHERE dedup_hash != '' "
    "GROUP BY dedup_hash, umo)"
)
sync_conn.exec_driver_sql(
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_message_dedup "
    "ON messages(dedup_hash, umo) WHERE dedup_hash != ''"
)
```

  - `repo.exists_hash` gains optional `umo` filter; `pipeline._capture_message` passes `umo`; `MessageRepo.add` uses `INSERT OR IGNORE` semantics (check rowcount, return record anyway with id possibly None — adjust callers or keep ORM add and rely on the pre-check + unique index catching races via try/except IntegrityError → return).
  - `learning_service.maybe_learn`: delete the dead `MessageRepo.add` block (keep the dedup check if it gates learning logic).
  - `bootstrap.try_load_cached`: if `<binary>.sha256` exists, verify with `hmac.compare_digest` before `_import_extension`; mismatch → treat as load failure (fallback path). Download path writes the sidecar (it already computes sha256 for the manifest).
  - `fallback.chunk_text`: `if chunk_overlap < 0: raise ValueError("chunk_overlap must be >= 0")` (keep `chunk_size <= 0 → []`).
  - `path.py` tier 2: `StarTools.get_data_dir(plugin_name="astrbot_plugin_unified_chat")`; tier 4: `Path("data/plugin_data/astrbot_plugin_unified_chat")`.
  - `requirements.txt`: comment now says dev deps live in `[dependency-groups].dev`.
- [ ] **Step 4: Run** suite + ruff + cargo test + maturin smoke; PASS.
- [ ] **Step 5: Commit** `fix: harden native build profile, capture dedup isolation and path resolution`

### Task 12: Docs and release prep

**Files:**
- Modify: `.agents/docs/plan.md` (Phase 9), `docs/CONFIG.md`, `docs/OPERATION.md`, `docs/ARCHITECTURE.md` (Chinese), `metadata.yaml`, `pyproject.toml` (0.3.0)
- Test: none new; full verification.

- [ ] **Step 1:** plan.md gains `### Phase 9: Hardening Round 2 [DONE]` listing the twelve task areas with `Spec: specs/011-hardening-round-2.md`.
- [ ] **Step 2:** Chinese docs: CONFIG.md documents `message_retention_days` / `learning_log_retention_days`; OPERATION.md documents per-document migration rollback, stale-flag auto-recovery (6 h), `migration_last` in `/unified_status`; ARCHITECTURE.md notes the UTC convention (storage = aware-UTC wall clock; naive reads re-attached to UTC).
- [ ] **Step 3:** bump `metadata.yaml` version and `pyproject.toml` version to 0.3.0.
- [ ] **Step 4:** `uv run pytest -q`, `uv run ruff check .`, `uv run pytest --cov=unified_chat --cov-report=term-missing | tail -30`, `cargo test -p unified_chat_native`.
- [ ] **Step 5: Commit** `chore(release): prepare v0.3.0`.
- [ ] **Step 6:** STOP — push/PR only with explicit user approval (AGENTS.md git workflow).

---

## Self-Review

- Spec coverage: R1→Task 1, R2→Task 2, R3→Task 3, R4→Task 4, R5→Task 5, R6→Task 6, R7→Task 7, R8→Task 8, R9→Task 9, R10→Task 10, R11→Task 11, R12→Task 12, deferred items documented in spec. No gaps.
- Placeholder scan: no TBDs; each task names files, exact behaviors, and verification commands; code shown for non-obvious changes, mechanical edits specified precisely.
- Type consistency: `sweep(now)` signature consistent across Task 10; `sender_stats`/`compute_importance` signatures consistent between Task 7 tests and implementation; `exists_hash` optional-umo consistent with pipeline call.
