# 004 Persistent Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Memory scoring, SQLite storage, KB-backed vector indexing (reuse FAISS/FTS5/RRF/Rerank), retrieval injection, daily 03:00 cleanup cron.

**Architecture:** `MemoryService` owns policy + KB handle; pipeline background stage calls `maybe_store`; hooks inject retrieved memories; `MemoryCleanupCron` enforces 30-day low-importance retention.

**Tech Stack:** Python 3.12, SQLModel repos (001), native `score_importance`, AstrBot KB APIs (verified v4.27.3: `create_kb/get_kb_by_name/upload_document(pre_chunked_text=)/delete_document/retrieve`).

**Spec:** `.agents/docs/specs/004-persistent-memory.md`

## Global Constraints

- KB reuse only; SQLite `Memory` table owns retention; SQLite-only mode when embedding provider missing
- Never raise from pipeline/hook/cron; per-tick try/except; `stop()` idempotent
- LSP clean + ruff + pytest green before commit; English comments, 100 col

---

### Task 1: Config + model extension

**Files:**
- Modify: `_conf_schema.json`, `unified_chat/config.py`, `unified_chat/storage/models.py`
- Test: `tests/test_config_validation.py` (extend), `tests/test_storage_models.py` (extend)

**Interfaces:**
- Produces: `PluginConfig.memory_kb_name` (default `"unified_chat_memories"`), `Memory.kb_doc_id: str | None`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_config_validation.py add
def test_memory_kb_name_default_and_override():
    from unified_chat.config import PluginConfig
    assert PluginConfig().memory_kb_name == "unified_chat_memories"
    c = PluginConfig.from_dict({"memory_kb_name": "my_mem"})
    assert c.memory_kb_name == "my_mem"

# tests/test_storage_models.py add
def test_memory_has_kb_doc_id():
    from unified_chat.storage.models import Memory
    m = Memory(content="x", kb_doc_id="doc1")
    assert m.kb_doc_id == "doc1"
```

- [ ] **Step 2: Verify failures** → FAIL (missing field)

- [ ] **Step 3: Implement**

```python
# _conf_schema.json properties add:
"memory_kb_name": {"type": "string", "description": "KB name for memory vector indexing", "default": "unified_chat_memories"}

# config.py: DEFAULTS + dataclass field + from_dict str() + to_dict

# models.py Memory add:
kb_doc_id: str | None = Field(default=None, index=True, max_length=64)
```

- [ ] **Step 4: Verify + LSP**

Run: `uv run pytest tests/test_config_validation.py tests/test_storage_models.py -q` → PASS
Run: `opencode debug lsp diagnostics unified_chat/config.py unified_chat/storage/models.py` → `[]`

- [ ] **Step 5: Commit**

```bash
git add _conf_schema.json unified_chat/config.py unified_chat/storage/models.py tests/test_config_validation.py tests/test_storage_models.py
git commit -m "feat(memory): add memory_kb_name config and Memory.kb_doc_id column"
```

---

### Task 2: Repo additions

**Files:**
- Modify: `unified_chat/storage/repo.py`
- Test: `tests/test_repo.py` (extend)

**Interfaces:**
- Produces: `MemoryRepo.list_expired(threshold, cutoff) -> list[Memory]`, `delete_by_ids(ids) -> int`, `search_by_keyword(keyword, limit=5) -> list[Memory]`, `update_kb_doc_id(memory_id, kb_doc_id) -> Memory | None`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_repo.py add
@pytest.mark.asyncio
async def test_memory_repo_search_and_expired():
    reset_engine_for_tests()
    from datetime import UTC, datetime, timedelta
    with tempfile.TemporaryDirectory() as d:
        await get_engine(Path(d)/"r2.db")
        await MemoryRepo.add(Memory(content="apple pie recipe", importance=0.9))
        await MemoryRepo.add(Memory(content="banana bread", importance=0.1))
        await MemoryRepo.add(Memory(content="carrot cake", importance=0.2))
        hits = await MemoryRepo.search_by_keyword("apple", limit=5)
        assert [m.content for m in hits] == ["apple pie recipe"]
        # LIKE escaping
        await MemoryRepo.add(Memory(content="100% sure", importance=0.5))
        assert len(await MemoryRepo.search_by_keyword("100% sure", limit=5)) == 1
        expired = await MemoryRepo.list_expired(0.5, datetime.now(UTC) - timedelta(days=1))
        assert [m.content for m in expired] == ["banana bread", "carrot cake"]
        assert await MemoryRepo.delete_by_ids([m.id for m in expired]) == 2
        updated = await MemoryRepo.update_kb_doc_id(3, "doc3")
        assert updated is not None and updated.kb_doc_id == "doc3"
        await close_engine()
```

- [ ] **Step 2: Verify failures** → FAIL (methods missing)

- [ ] **Step 3: Implement repo methods**

```python
class MemoryRepo:
    ...
    @staticmethod
    async def list_expired(threshold: float, cutoff: datetime) -> list[Memory]:
        async with get_session() as session:
            rows = (await session.exec(
                select(Memory).where(Memory.importance < threshold).where(Memory.created_at < cutoff)
            )).all()
            return list(rows)

    @staticmethod
    async def delete_by_ids(ids: list[int]) -> int:
        if not ids:
            return 0
        async with get_session() as session:
            rows = (await session.exec(select(Memory).where(Memory.id.in_(ids)))).all()
            for r in rows:
                await session.delete(r)
            await session.commit()
            return len(rows)

    @staticmethod
    async def search_by_keyword(keyword: str, limit: int = 5) -> list[Memory]:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        async with get_session() as session:
            rows = (await session.exec(
                select(Memory).where(Memory.content.like(f"%{escaped}%", escape="\\"))
                .order_by(Memory.importance.desc()).limit(limit)
            )).all()
            return list(rows)

    @staticmethod
    async def update_kb_doc_id(memory_id: int, kb_doc_id: str) -> Memory | None:
        async with get_session() as session:
            m = await session.get(Memory, memory_id)
            if m is None:
                return None
            m.kb_doc_id = kb_doc_id
            session.add(m)
            await session.commit()
            await session.refresh(m)
            return m
```

(Keep existing `delete_expired` — still used by older tests; optionally reimplement on top of list_expired+delete_by_ids.)

- [ ] **Step 4: Verify + LSP** → PASS, `[]`

- [ ] **Step 5: Commit**

```bash
git add unified_chat/storage/repo.py tests/test_repo.py
git commit -m "feat(memory): add repo search, expired listing, delete and kb_doc_id update"
```

---

### Task 3: MemoryService (scoring, storage, KB lifecycle)

**Files:**
- Create: `unified_chat/services/memory_service.py`
- Test: `tests/test_memory_service.py`

**Interfaces:**
- Consumes: `ChatService`, `MemoryRepo`, native `score_importance`, kb_manager
- Produces: `class MemoryService(context, config)`, `MIN_MEMORY_CHARS=20`, `compute_importance(content, sender_id, existing) -> float`, `should_store(event) -> bool`, `ensure_memory_kb() -> None` (sets `self._kb_helper|None`), `maybe_store(event, sender_id) -> None`, `retrieve(query) -> str` (KB → fallback keyword search), `delete_expired_memories() -> int`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_memory_service.py
import pytest
from datetime import UTC, datetime, timedelta
from unified_chat.config import PluginConfig
from unified_chat.services.memory_service import MemoryService
from unified_chat.services.chat_service import ChatService

class FakeEvent:
    def __init__(self, text, umo="p:m:1", sender="alice"):
        self.message_str = text; self.unified_msg_origin = umo; self._sender = sender
    def get_sender_name(self): return self._sender
    def is_private_chat(self): return False

class FakeDoc:
    doc_id = "doc9"

class FakeKbHelper:
    def __init__(self): self.uploads=[]; self.deletes=[]
    async def upload_document(self, file_name, file_content, file_type, pre_chunked_text=None, **kw):
        self.uploads.append((file_name, pre_chunked_text)); return FakeDoc()
    async def delete_document(self, doc_id): self.deletes.append(doc_id)

class FakeKbManager:
    def __init__(self, helper): self.helper = helper; self.created=[]; self.retrieves=[]
    async def get_kb_by_name(self, name): return self.helper if self.helper else None
    async def create_kb(self, kb_name, **kw): self.created.append(kb_name); return self.helper
    async def retrieve(self, query, kb_names, **kw):
        self.retrieves.append((query, kb_names)); return {"context_text": "MEM"}

class FakeContext:
    def __init__(self, kb_manager): self.kb_manager = kb_manager

def test_compute_importance_bounds():
    svc = MemoryService(FakeContext(None), PluginConfig())
    v = svc.compute_importance("hello world", "alice", [])
    assert 0.0 <= v <= 1.0

def test_should_store_gates():
    svc = MemoryService(FakeContext(None), PluginConfig())
    assert not svc.should_store(FakeEvent("/cmd"))
    assert not svc.should_store(FakeEvent("short"))
    assert svc.should_store(FakeEvent("this is a long enough memory candidate message"))
```

- [ ] **Step 2: Verify failures** → FAIL missing module

- [ ] **Step 3: Implement memory_service.py**

```python
import contextlib
from datetime import UTC, datetime, timedelta

from unified_chat.native import score_importance
from unified_chat.services.chat_service import ChatService
from unified_chat.storage import repo as repos
from unified_chat.storage.models import Memory

class MemoryService:
    MIN_MEMORY_CHARS = 20

    def __init__(self, context, config):
        self.context = context
        self.config = config
        self._kb_helper = None

    async def ensure_memory_kb(self) -> None:
        if not self.config.enable_persistent_memory or not self.config.embedding_provider_id:
            self._kb_helper = None
            return
        kb_manager = getattr(self.context, "kb_manager", None)
        if kb_manager is None:
            self._kb_helper = None
            return
        try:
            helper = await kb_manager.get_kb_by_name(self.config.memory_kb_name)
            if helper is None:
                helper = await kb_manager.create_kb(
                    kb_name=self.config.memory_kb_name,
                    description="UnifiedChat persistent memories",
                    embedding_provider_id=self.config.embedding_provider_id,
                    rerank_provider_id=self.config.rerank_provider_id or None,
                    chunk_size=512,
                    chunk_overlap=50,
                )
            self._kb_helper = helper
        except Exception:
            self._kb_helper = None
            self._log_error("ensure_memory_kb")

    def compute_importance(self, content: str, sender_id: str, existing: list[Memory]) -> float:
        now = datetime.now(UTC)
        freq = 0
        newest: datetime | None = None
        for m in existing:
            if m.source == sender_id and (now - m.created_at).days < 7:
                freq += 1
                if newest is None or m.created_at > newest:
                    newest = m.created_at
        recency_hours = 0.0 if newest is None else max(0.0, (now - newest).total_seconds() / 3600.0)
        return score_importance(len(content), recency_hours, freq)

    def should_store(self, event) -> bool:
        if not self.config.enable_persistent_memory:
            return False
        text = getattr(event, "message_str", "")
        if ChatService.is_command(text):
            return False
        return len(text) >= self.MIN_MEMORY_CHARS

    async def maybe_store(self, event, sender_id: str) -> None:
        try:
            if not self.should_store(event):
                return
            text = event.message_str
            existing = await repos.MemoryRepo.list_all()  # 004: global memories, keep bounded via LIMIT later
            importance = self.compute_importance(text, sender_id, existing)
            mem = await repos.MemoryRepo.add(Memory(
                content=text, importance=importance, source=sender_id,
                dedup_hash=ChatService.hash_of(text),
            ))
            if importance >= self.config.importance_threshold and self._kb_helper is not None:
                with contextlib.suppress(Exception):
                    doc = await self._kb_helper.upload_document(
                        file_name=f"memory_{mem.id}.txt", file_content=None,
                        file_type="txt", pre_chunked_text=[text],
                    )
                    await repos.MemoryRepo.update_kb_doc_id(mem.id, doc.doc_id)
        except Exception:
            self._log_error("maybe_store")

    async def retrieve(self, query: str) -> str:
        kb_manager = getattr(self.context, "kb_manager", None)
        if self._kb_helper is not None and kb_manager is not None:
            with contextlib.suppress(Exception):
                result = await kb_manager.retrieve(
                    query=query, kb_names=[self.config.memory_kb_name],
                    top_k_fusion=20, top_m_final=5,
                )
                if result and isinstance(result, dict) and result.get("context_text"):
                    return str(result["context_text"])
        with contextlib.suppress(Exception):
            hits = await repos.MemoryRepo.search_by_keyword(query, limit=5)
            if hits:
                return "\n".join(f"- {m.content}" for m in hits)
        return ""

    async def delete_expired_memories(self) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=self.config.memory_cleanup_days)
        expired = await repos.MemoryRepo.list_expired(self.config.importance_threshold, cutoff)
        for m in expired:
            if m.kb_doc_id and self._kb_helper is not None:
                with contextlib.suppress(Exception):
                    await self._kb_helper.delete_document(m.kb_doc_id)
        return await repos.MemoryRepo.delete_by_ids([m.id for m in expired if m.id is not None])

    @staticmethod
    def _log_error(msg: str) -> None:
        with contextlib.suppress(Exception):
            from astrbot.api import logger
            logger.error(f"[unified_chat] memory {msg}", exc_info=True)
```

- [ ] **Step 4: Verify + LSP** → PASS, clean

- [ ] **Step 5: Commit**

```bash
git add unified_chat/services/memory_service.py tests/test_memory_service.py
git commit -m "feat(memory): add MemoryService with scoring, KB-backed storage and retrieval"
```

---

### Task 4: Cron

**Files:**
- Create: `unified_chat/core/cron.py`
- Test: `tests/test_cron.py`

**Interfaces:**
- Produces: `class MemoryCleanupCron(memory_service)` with `start()`, `stop()`, `_seconds_until_next_03(now: datetime) -> float`, `_tick() -> int`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_cron.py
import pytest
from datetime import datetime
from unified_chat.core.cron import MemoryCleanupCron

class FakeMemoryService:
    def __init__(self): self.ticks = 0
    async def delete_expired_memories(self): self.ticks += 1; return 3

def test_seconds_until_next_03():
    cron = MemoryCleanupCron(FakeMemoryService())
    # 04:00 -> next 03:00 is 23 hours later
    assert abs(cron._seconds_until_next_03(datetime(2026, 8, 20, 4, 0, 0)) - 23*3600) < 1
    # 01:00 -> 2 hours later
    assert abs(cron._seconds_until_next_03(datetime(2026, 8, 20, 1, 0, 0)) - 2*3600) < 1
    # exactly 03:00 -> next day
    assert abs(cron._seconds_until_next_03(datetime(2026, 8, 20, 3, 0, 0)) - 24*3600) < 1

@pytest.mark.asyncio
async def test_tick_delegates():
    svc = FakeMemoryService()
    cron = MemoryCleanupCron(svc)
    assert await cron._tick() == 3
    assert svc.ticks == 1

@pytest.mark.asyncio
async def test_stop_idempotent_and_cancels():
    import asyncio
    svc = FakeMemoryService()
    cron = MemoryCleanupCron(svc)
    cron.start()
    await asyncio.sleep(0.01)
    cron.stop()
    cron.stop()  # idempotent
```

- [ ] **Step 2: Verify failures** → FAIL missing module

- [ ] **Step 3: Implement cron.py**

```python
import asyncio, contextlib
from datetime import datetime, timedelta

class MemoryCleanupCron:
    def __init__(self, memory_service):
        self.memory_service = memory_service
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="unified_chat_cron")

    def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    @staticmethod
    def _seconds_until_next_03(now: datetime) -> float:
        nxt = (now + timedelta(days=1)).replace(hour=3, minute=0, second=0, microsecond=0)
        if now.hour < 3:
            nxt = now.replace(hour=3, minute=0, second=0, microsecond=0)
        return (nxt - now).total_seconds()

    async def _run(self) -> None:
        try:
            while True:
                delay = self._seconds_until_next_03(datetime.now())
                await asyncio.sleep(delay)
                await self._tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._log_error("cron loop")

    async def _tick(self) -> int:
        try:
            return await self.memory_service.delete_expired_memories()
        except Exception:
            self._log_error("tick")
            return 0

    @staticmethod
    def _log_error(msg: str) -> None:
        with contextlib.suppress(Exception):
            from astrbot.api import logger
            logger.error(f"[unified_chat] cron {msg}", exc_info=True)
```

- [ ] **Step 4: Verify + LSP** → PASS, clean

- [ ] **Step 5: Commit**

```bash
git add unified_chat/core/cron.py tests/test_cron.py
git commit -m "feat(memory): add daily 03:00 cleanup cron"
```

---

### Task 5: Hooks + pipeline + lifecycle wiring

**Files:**
- Modify: `unified_chat/core/hooks.py`, `unified_chat/core/pipeline.py`, `unified_chat/core/lifecycle.py`
- Test: `tests/test_hooks_memory.py`, `tests/test_lifecycle_memory.py`

**Interfaces:**
- Consumes: `MemoryService`
- Produces: `inject_memories(event, req, config, memory_service) -> None`; `MessagePipeline(config, chat_service, memory_service=None)` with `_after_stages` calling `maybe_store`; lifecycle `self._memory_service`, cron start/stop

- [ ] **Step 1: Write failing tests**

```python
# tests/test_hooks_memory.py
@pytest.mark.asyncio
async def test_inject_memories_gate_off():
    ...  # enable_persistent_memory=False -> req.contexts untouched
@pytest.mark.asyncio
async def test_inject_memories_kb_mode():
    ...  # memory_service.retrieve mocked -> system message appended
@pytest.mark.asyncio
async def test_inject_memories_empty():
    ...  # retrieve returns "" -> no append
```

```python
# tests/test_lifecycle_memory.py
@pytest.mark.asyncio
async def test_lifecycle_creates_memory_service_and_cron(tmp_path):
    # on_load with embedding_provider_id="" -> SQLite-only; assert _memory_service not None
    # handle_message("long enough memory candidate") -> eventually stored (await sleep 0.05 for bg task)
    # on_unload stops cron
```

- [ ] **Step 2: Verify failures** → FAIL

- [ ] **Step 3: Implement wiring**

```python
# hooks.py
async def inject_memories(event, req, config, memory_service) -> None:
    if not config.enable_persistent_memory:
        return
    try:
        text = getattr(event, "message_str", "")
        if not text:
            return
        memories = await memory_service.retrieve(text)
        if not memories:
            return
        contexts = getattr(req, "contexts", None)
        if contexts is None:
            contexts = []
            req.contexts = contexts
        contexts.append({"role": "system", "content": f"Relevant memories:\n{memories}"})
    except Exception:
        with contextlib.suppress(Exception):
            from astrbot.api import logger
            logger.error("[unified_chat] inject_memories failed", exc_info=True)

# pipeline.py: constructor gains memory_service=None; _after_stages:
async def _after_stages(self, event) -> None:
    if self.memory_service is not None:
        await self.memory_service.maybe_store(event, self._sender_of(event))
# _sender_of: get_sender_name guarded

# lifecycle.py on_load (inside try, after pipeline creation):
from unified_chat.services.memory_service import MemoryService
self._memory_service = MemoryService(self.context, config)
await self._memory_service.ensure_memory_kb()
self._pipeline = MessagePipeline(config, self._chat_service, self._memory_service)
from unified_chat.core.cron import MemoryCleanupCron
self._cron = MemoryCleanupCron(self._memory_service)
self._cron.start()

# on_unload: self._cron.stop() guarded; handle_llm_request: inject_memories after social
```

- [ ] **Step 4: Verify + LSP + ruff + full pytest**

- [ ] **Step 5: Commit**

```bash
git add unified_chat/core/hooks.py unified_chat/core/pipeline.py unified_chat/core/lifecycle.py tests/test_hooks_memory.py tests/test_lifecycle_memory.py
git commit -m "feat(memory): wire memory service, cron and injection into lifecycle"
```

---

## Self-Review

- Spec R001→T1, R002→T1, R003/R004/R005/R006→T3, R007→T5, R008→T4, R009→T2, R010/R011→T5/T4.
- Types consistent: `maybe_store(event, sender_id)`, `retrieve(query) -> str`, `delete_expired_memories() -> int`, `_seconds_until_next_03(datetime) -> float`, `_tick() -> int`.

## Execution Handoff

Inline execution in this session.
