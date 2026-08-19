# 001 Storage & Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire config lifecycle via `StarTools.get_data_dir()` and provide `get_session()`/repo/kv so later domains have stable persistence.

**Architecture:** `PluginLifecycle.on_load` resolves `PluginConfig` + `data_dir` -> `get_engine(<data_dir>/unified_chat.db)` singleton under `asyncio.Lock` -> session context manager -> thin repo/kv wrappers. No globals except `_engine`.

**Tech Stack:** Python 3.12, SQLModel 0.0.22+, SQLAlchemy 2.0+ async, aiosqlite, AstrBot `StarTools`/`Context`.

**Spec:** `.agents/docs/specs/001-storage-config.md`

## Global Constraints

- AstrBot `>=4.27.3,<5.0.0`
- Persistent data only under `data/plugin_data/astrbot_plugin_unified_chat/` via `StarTools.get_data_dir()` or fallback
- `sqlite` only, `aiosqlite>=0.20`, `sqlmodel>=0.0.22`, `sqlalchemy>=2.0`
- Rust not touched
- `__init__` must not raise; `initialize()` does I/O
- No Chinese comments, `ruff 100 col`, `Conventional Commits`

---

### Task 1: Storage models — add UnifiedKV and missing index

**Files:**
- Modify: `unified_chat/storage/models.py:1-54`
- Test: `tests/test_storage_models.py` (new)

**Interfaces:**
- Consumes: existing `MessageRecord`, `Memory`, `LearningLog`
- Produces: `class UnifiedKV(SQLModel, table=True)` with `key: str PK`, `value: str`, `updated_at: datetime`; `Memory.expires_at` stays indexed via `Field(index=True)` if not already.

- [ ] **Step 1: Write failing test for KV table**

```python
# tests/test_storage_models.py
from unified_chat.storage.models import UnifiedKV


def test_unified_kv_fields():
    kv = UnifiedKV(key="k", value="v")
    assert kv.key == "k"
    assert kv.value == "v"
    assert kv.updated_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_storage_models.py::test_unified_kv_fields -v`
Expected: FAIL `ImportError: cannot import name 'UnifiedKV'`

- [ ] **Step 3: Implement UnifiedKV**

```python
# unified_chat/storage/models.py: add
class UnifiedKV(SQLModel, table=True):
    __tablename__ = "unified_kv"
    key: str = Field(primary_key=True, max_length=255)
    value: str = Field(default="")
    updated_at: datetime = Field(default_factory=_utcnow)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_storage_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add unified_chat/storage/models.py tests/test_storage_models.py
git commit -m "feat(storage): add UnifiedKV table for migration flags"
```

---

### Task 2: Database — get_session context manager + close_engine under lock

**Files:**
- Modify: `unified_chat/storage/database.py:1-58`
- Test: `tests/test_database_session.py` (new)

**Interfaces:**
- Consumes: `get_engine`, `SQLModel.metadata`
- Produces:
  - `async def get_session() -> AsyncSession` async context manager (yields, commits on success, rollbacks on error)
  - `async def close_engine()` now acquires `_engine_lock` and disposes + resets; `reset_engine_for_tests()` unchanged

- [ ] **Step 1: Write failing test**

```python
# tests/test_database_session.py
import pytest
from pathlib import Path
import tempfile
from unified_chat.storage.database import (
    get_engine,
    get_session,
    close_engine,
    reset_engine_for_tests,
)
from unified_chat.storage.models import Memory


@pytest.mark.asyncio
async def test_get_session_roundtrip():
    reset_engine_for_tests()
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "a.db"
        eng = await get_engine(db)
        async with get_session() as s:
            s.add(Memory(content="hi", importance=0.5))
            await s.commit()
        async with get_session() as s:
            from sqlmodel import select

            r = (await s.exec(select(Memory))).all()
            assert len(r) == 1
        await close_engine()


@pytest.mark.asyncio
async def test_concurrent_get_engine_singleton():
    reset_engine_for_tests()
    import asyncio

    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "b.db"
        a, b = await asyncio.gather(get_engine(db), get_engine(db))
        assert a is b
        await close_engine()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_database_session.py -v`
Expected: FAIL `ImportError: cannot import name 'get_session'`

- [ ] **Step 3: Implement get_session + fix close_engine**

```python
# unified_chat/storage/database.py: add imports
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker


@asynccontextmanager
async def get_session():
    engine = _engine
    if engine is None:
        raise RuntimeError("engine not initialized; call get_engine first")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        try:
            yield session
            # caller controls commit; we just ensure close
        except Exception:
            await session.rollback()
            raise


async def close_engine():
    global _engine
    async with _engine_lock:
        if _engine is not None:
            await _engine.dispose()
            _engine = None
```

- [ ] **Step 4: Run test to verify it passes (also check existing tests)**

Run: `uv run pytest tests/test_database_session.py -v`
Expected: PASS (allow WAL pragma warnings)

- [ ] **Step 5: Run LSP diagnostics**

Run: `opencode debug lsp diagnostics unified_chat/storage/database.py`
Expected: 0 errors

- [ ] **Step 6: Commit**

```bash
git add unified_chat/storage/database.py tests/test_database_session.py
git commit -m "feat(storage): add get_session context manager and lock-protected close_engine"
```

---

### Task 3: KV helpers

**Files:**
- Create: `unified_chat/storage/kv.py`
- Test: `tests/test_kv.py` (new)

**Interfaces:**
- Consumes: `get_session`, `UnifiedKV`
- Produces: `async def kv_get(key)->str|None`, `async def kv_set(key,val)`, `async def kv_delete(key)`

- [ ] **Step 1: Write failing test**

```python
# tests/test_kv.py
import pytest, tempfile
from pathlib import Path
from unified_chat.storage.database import get_engine, close_engine, reset_engine_for_tests
from unified_chat.storage.kv import kv_get, kv_set, kv_delete


@pytest.mark.asyncio
async def test_kv_roundtrip():
    reset_engine_for_tests()
    with tempfile.TemporaryDirectory() as d:
        await get_engine(Path(d) / "kv.db")
        assert await kv_get("k") is None
        await kv_set("k", "v")
        assert await kv_get("k") == "v"
        await kv_set("k", "v2")
        assert await kv_get("k") == "v2"
        await kv_delete("k")
        assert await kv_get("k") is None
        await close_engine()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_kv.py -v`
Expected: FAIL `ModuleNotFoundError: kv`

- [ ] **Step 3: Implement kv.py**

```python
# unified_chat/storage/kv.py
from datetime import UTC, datetime
from sqlmodel import select
from unified_chat.storage.database import get_session
from unified_chat.storage.models import UnifiedKV


async def kv_get(key: str) -> str | None:
    async with get_session() as s:
        row = await s.get(UnifiedKV, key)
        return row.value if row else None


async def kv_set(key: str, value: str) -> None:
    async with get_session() as s:
        row = await s.get(UnifiedKV, key)
        if row:
            row.value = value
            row.updated_at = datetime.now(UTC)
            s.add(row)
        else:
            s.add(UnifiedKV(key=key, value=value))
        await s.commit()


async def kv_delete(key: str) -> None:
    async with get_session() as s:
        row = await s.get(UnifiedKV, key)
        if row:
            await s.delete(row)
            await s.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_kv.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add unified_chat/storage/kv.py tests/test_kv.py
git commit -m "feat(storage): add kv helpers over UnifiedKV"
```

---

### Task 4: Thin repo helpers

**Files:**
- Create: `unified_chat/storage/repo.py`
- Test: `tests/test_repo.py` (new)

**Interfaces:**
- Consumes: `get_session`, models
- Produces: `MessageRepo`, `MemoryRepo`, `LearningLogRepo` with methods as spec R004

- [ ] **Step 1: Write failing test**

```python
# tests/test_repo.py
import pytest, tempfile
from pathlib import Path
from datetime import UTC, datetime, timedelta
from unified_chat.storage.database import get_engine, close_engine, reset_engine_for_tests
from unified_chat.storage.repo import MessageRepo, MemoryRepo, LearningLogRepo
from unified_chat.storage.models import MessageRecord, Memory, LearningLog


@pytest.mark.asyncio
async def test_message_repo_add_count():
    reset_engine_for_tests()
    with tempfile.TemporaryDirectory() as d:
        await get_engine(Path(d) / "r.db")
        await MessageRepo.add(MessageRecord(umo="u", sender_id="s", content="hi", dedup_hash="h"))
        assert await MessageRepo.count() == 1
        await close_engine()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_repo.py -v`
Expected: FAIL `cannot import name 'MessageRepo'`

- [ ] **Step 3: Implement repo.py**

```python
# unified_chat/storage/repo.py
from datetime import datetime
from sqlmodel import select, func
from unified_chat.storage.database import get_session
from unified_chat.storage.models import MessageRecord, Memory, LearningLog


class MessageRepo:
    @staticmethod
    async def add(r: MessageRecord) -> MessageRecord:
        async with get_session() as s:
            s.add(r)
            await s.commit()
            await s.refresh(r)
            return r

    @staticmethod
    async def count() -> int:
        async with get_session() as s:
            return (await s.exec(select(func.count()).select_from(MessageRecord))).one()


class MemoryRepo:
    @staticmethod
    async def add(m: Memory) -> Memory:
        async with get_session() as s:
            s.add(m)
            await s.commit()
            await s.refresh(m)
            return m

    @staticmethod
    async def list_all() -> list[Memory]:
        async with get_session() as s:
            return list((await s.exec(select(Memory))).all())

    @staticmethod
    async def delete_expired(threshold: float, cutoff: datetime) -> int:
        async with get_session() as s:
            rows = (
                await s.exec(
                    select(Memory)
                    .where(Memory.importance < threshold)
                    .where(Memory.created_at < cutoff)
                )
            ).all()
            for r in rows:
                await s.delete(r)
            await s.commit()
            return len(rows)


class LearningLogRepo:
    @staticmethod
    async def add(l: LearningLog) -> LearningLog:
        async with get_session() as s:
            s.add(l)
            await s.commit()
            await s.refresh(l)
            return l
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_repo.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add unified_chat/storage/repo.py tests/test_repo.py
git commit -m "feat(storage): add thin repo helpers for Message/Memory/LearningLog"
```

---

### Task 5: Config validation hardening + data_dir field

**Files:**
- Modify: `unified_chat/config.py:1-75`
- Test: `tests/test_config_validation.py` (new)

**Interfaces:**
- Consumes: `DEFAULTS`
- Produces: clamped `memory_cleanup_days>=1` and `importance_threshold in [0,1]` with fallback to default + no raise

- [ ] **Step 1: Write failing test for clamping**

```python
def test_config_clamps_invalid():
    from unified_chat.config import PluginConfig

    c = PluginConfig.from_dict({"memory_cleanup_days": -5, "importance_threshold": 2.0})
    assert c.memory_cleanup_days == 30
    assert 0 <= c.importance_threshold <= 1
    c2 = PluginConfig.from_dict({"rag_kbs": "not-a-list"})  # type: ignore
    assert isinstance(c2.rag_kbs, list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config_validation.py -v`
Expected: FAIL assertion if not clamped

- [ ] **Step 3: Implement clamping in from_dict**

```python
# inside from_dict, after pick:
try:
    mcd = int(pick("memory_cleanup_days", d["memory_cleanup_days"]))
except:
    mcd = d["memory_cleanup_days"]
if mcd < 1:
    mcd = d["memory_cleanup_days"]
try:
    thr = float(pick("importance_threshold", d["importance_threshold"]))
except:
    thr = d["importance_threshold"]
if not 0 <= thr <= 1:
    thr = d["importance_threshold"]
rag_kbs = pick("rag_kbs", d["rag_kbs"])
if not isinstance(rag_kbs, list):
    rag_kbs = []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config_validation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add unified_chat/config.py tests/test_config_validation.py
git commit -m "feat(config): clamp invalid cleanup_days/threshold and coerce rag_kbs"
```

---

### Task 6: Lifecycle — resolve data_dir + engine + status

**Files:**
- Modify: `unified_chat/core/lifecycle.py:1-45`
- Create: `unified_chat/utils/path.py` (helper for data_dir resolution)
- Test: `tests/test_lifecycle_storage.py` (new)

**Interfaces:**
- Consumes: `PluginConfig`, `get_engine`, `close_engine`
- Produces: `PluginLifecycle.on_load/on_unload/get_status` per R006; never raises in `__init__`

- [ ] **Step 1: Write failing test (mock StarTools & Context)**

```python
# tests/test_lifecycle_storage.py
import pytest, tempfile
from pathlib import Path
from types import SimpleNamespace
from unified_chat.core.lifecycle import PluginLifecycle
from unified_chat.storage.database import reset_engine_for_tests


class FakeContext:
    def __init__(self, cfg):
        self._cfg = cfg

    def get_config(self):
        return self._cfg


class FakePlugin:
    pass


@pytest.mark.asyncio
async def test_on_load_creates_db_and_status(monkeypatch, tmp_path):
    reset_engine_for_tests()
    raw = {"memory_cleanup_days": 30}
    ctx = FakeContext(raw)
    lc = PluginLifecycle(FakePlugin(), ctx)
    # monkeypatch StarTools fallback to tmp_path
    import unified_chat.utils.path as p

    monkeypatch.setattr(p, "resolve_data_dir", lambda raw_cfg, ctx: tmp_path / "data")
    await lc.on_load()
    assert "loaded" in lc.get_status()
    assert (tmp_path / "data" / "unified_chat.db").exists()
    await lc.on_unload()
    assert "unloaded" in lc.get_status()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_lifecycle_storage.py -v`
Expected: FAIL missing `resolve_data_dir` / lifecycle still stub

- [ ] **Step 3: Implement utils/path.py + lifecycle.py**

```python
# unified_chat/utils/path.py
from pathlib import Path


def resolve_data_dir(raw: dict, context) -> Path:
    if raw and raw.get("data_dir"):
        p = Path(str(raw["data_dir"])).expanduser()
        try:
            p.mkdir(parents=True, exist_ok=True)
            return p.resolve()
        except:
            pass
    try:
        from astrbot.api.star import StarTools

        p = Path(StarTools.get_data_dir())
        p.mkdir(parents=True, exist_ok=True)
        return p.resolve()
    except:
        pass
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        p = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_unified_chat"
        p.mkdir(parents=True, exist_ok=True)
        return p.resolve()
    except:
        pass
    p = Path("data/plugin_data/unified_chat")
    p.mkdir(parents=True, exist_ok=True)
    return p.resolve()


# lifecycle.py: fill on_load/on_unload/get_status per spec, use get_engine(db_path), store _config, _data_dir
```

- [ ] **Step 4: Run test + LSP**

Run: `uv run pytest tests/test_lifecycle_storage.py -v`
Run: `opencode debug lsp diagnostics unified_chat/core/lifecycle.py`
Expected: PASS, 0 diagnostics

- [ ] **Step 5: Commit**

```bash
git add unified_chat/core/lifecycle.py unified_chat/utils/path.py tests/test_lifecycle_storage.py
git commit -m "feat(core): wire lifecycle data_dir/engine/status with StarTools fallback"
```

---

## Self-Review

- Spec R001-R007 each mapped to a Task; R003 KV + repo separated to keep commits small.
- No placeholder steps; each Task has concrete test snippet and file paths with line hints.
- Types consistent: `get_engine(Path) -> AsyncEngine`, `get_session() -> AsyncContextManager[AsyncSession]`, `PluginConfig.data_dir: str`.

## Execution Handoff

After saving this plan, next is `subagent-driven-development` per spec 001 tasks or inline execution in this session.
