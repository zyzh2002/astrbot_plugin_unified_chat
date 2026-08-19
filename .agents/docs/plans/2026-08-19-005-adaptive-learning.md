# 005 Adaptive Learning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Background filter→refine→reinforce pipeline with raw MessageRecord capture, LLM refinement via `context.llm_generate`, Memory reinforcement with hash dedup, and LearningLog audit.

**Architecture:** `LearningService.maybe_learn` runs in the pipeline background task, guarded by `Semaphore(2)`; degrade mode (no provider) captures raw records only.

**Tech Stack:** Python 3.12, SQLModel repos, `context.llm_generate` (verified v4.27.3), native dedup hash.

**Spec:** `.agents/docs/specs/005-adaptive-learning.md`

## Global Constraints

- Only `llm_generate` API; degrade to raw capture without provider; never raise into pipeline
- LSP clean + ruff + pytest green before commit; English comments, 100 col

---

### Task 1: Repo additions (exists_hash)

**Files:**
- Modify: `unified_chat/storage/repo.py`
- Test: `tests/test_repo.py` (extend)

**Interfaces:**
- Produces: `MessageRepo.exists_hash(h) -> bool`, `MemoryRepo.exists_hash(h) -> bool`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_repo.py add
@pytest.mark.asyncio
async def test_exists_hash():
    reset_engine_for_tests()
    with tempfile.TemporaryDirectory() as d:
        await get_engine(Path(d) / "r4.db")
        assert not await MessageRepo.exists_hash("h1")
        await MessageRepo.add(MessageRecord(umo="u", sender_id="s", content="c", dedup_hash="h1"))
        assert await MessageRepo.exists_hash("h1")
        assert not await MemoryRepo.exists_hash("h1")
        await MemoryRepo.add(Memory(content="c", dedup_hash="h2"))
        assert await MemoryRepo.exists_hash("h2")
        await close_engine()
```

- [ ] **Step 2: Verify failure** → FAIL (missing methods)

- [ ] **Step 3: Implement**

```python
# MessageRepo
@staticmethod
async def exists_hash(h: str) -> bool:
    async with get_session() as session:
        result = await session.exec(
            select(MessageRecord.id).where(MessageRecord.dedup_hash == h).limit(1)
        )
        return result.first() is not None


# MemoryRepo
@staticmethod
async def exists_hash(h: str) -> bool:
    async with get_session() as session:
        result = await session.exec(select(Memory.id).where(Memory.dedup_hash == h).limit(1))
        return result.first() is not None
```

- [ ] **Step 4: Verify + LSP** → PASS, `[]`

- [ ] **Step 5: Commit**

```bash
git add unified_chat/storage/repo.py tests/test_repo.py
git commit -m "feat(learning): add exists_hash to MessageRepo and MemoryRepo"
```

---

### Task 2: LearningService

**Files:**
- Create: `unified_chat/services/learning_service.py`
- Test: `tests/test_learning_service.py`

**Interfaces:**
- Consumes: `context.llm_generate`, `MessageRepo/MemoryRepo/LearningLogRepo`, `ChatService`
- Produces: `class LearningService(context, config)`, `MIN_LEARN_CHARS=8`, `REFINE_SYSTEM_PROMPT`, `should_learn(event) -> bool`, `refine(text) -> str`, `maybe_learn(event, sender_id) -> None`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_learning_service.py
import asyncio
import pathlib
import pytest
from unified_chat.config import PluginConfig
from unified_chat.services.learning_service import LearningService
from unified_chat.storage.database import close_engine, get_engine, reset_engine_for_tests


class FakeEvent:
    def __init__(self, text, umo="p:m:1", sender="alice"):
        self.message_str = text
        self.unified_msg_origin = umo
        self._sender = sender

    def get_sender_name(self):
        return self._sender

    def is_private_chat(self):
        return False


class FakeResp:
    completion_text = "  durable fact about alice  "


class FakeContext:
    def __init__(self, provider_id=None):
        self.calls = []
        self._provider_id = provider_id

    async def llm_generate(self, **kw):
        self.calls.append(kw)
        if self._provider_id is None:
            raise ValueError("no provider")
        return FakeResp()


@pytest.fixture
def ctx_db(tmp_path):
    reset_engine_for_tests()
    yield tmp_path
    asyncio.get_event_loop_policy()  # keep simple


async def _init_db(p):
    await get_engine(pathlib.Path(p) / "l.db")


def test_should_learn_gates():
    svc = LearningService(FakeContext(), PluginConfig())
    assert not svc.should_learn(FakeEvent("/cmd"))
    assert not svc.should_learn(FakeEvent("short"))
    assert svc.should_learn(FakeEvent("this is long enough"))


@pytest.mark.asyncio
async def test_refine_calls_llm(tmp_path):
    reset_engine_for_tests()
    await _init_db(tmp_path)
    ctx = FakeContext(provider_id="p1")
    svc = LearningService(ctx, PluginConfig(chat_provider_id="p1"))
    out = await svc.refine("hello world message")
    assert out == "durable fact about alice"
    assert ctx.calls[0]["prompt"] == "hello world message"
    assert ctx.calls[0]["chat_provider_id"] == "p1"


@pytest.mark.asyncio
async def test_refine_missing_provider(tmp_path):
    reset_engine_for_tests()
    await _init_db(tmp_path)
    svc = LearningService(FakeContext(), PluginConfig(chat_provider_id="p1"))
    assert await svc.refine("hello") == ""


@pytest.mark.asyncio
async def test_maybe_learn_degrade_mode(tmp_path):
    reset_engine_for_tests()
    await _init_db(tmp_path)
    from unified_chat.storage.repo import MemoryRepo, MessageRepo

    svc = LearningService(FakeContext(), PluginConfig(chat_provider_id=""))
    text = "raw message long enough"
    await svc.maybe_learn(FakeEvent(text), "alice")
    assert await MessageRepo.count() == 1
    assert await MemoryRepo.list_all() == []
    # dup suppressed
    await svc.maybe_learn(FakeEvent(text), "alice")
    assert await MessageRepo.count() == 1


@pytest.mark.asyncio
async def test_maybe_learn_full_pipeline(tmp_path):
    reset_engine_for_tests()
    await _init_db(tmp_path)
    from unified_chat.storage.repo import MemoryRepo, MessageRepo

    svc = LearningService(FakeContext(provider_id="p1"), PluginConfig(chat_provider_id="p1"))
    await svc.maybe_learn(FakeEvent("hello world long message"), "alice")
    mems = await MemoryRepo.list_all()
    assert len(mems) == 1
    assert mems[0].source == "learning"
    assert mems[0].content == "durable fact about alice"
    assert await MessageRepo.count() == 1
    # dup suppressed by memory hash
    await svc.maybe_learn(FakeEvent("hello world long message"), "alice")
    assert len(await MemoryRepo.list_all()) == 1


@pytest.mark.asyncio
async def test_refine_failure_keeps_pipeline(tmp_path):
    reset_engine_for_tests()
    await _init_db(tmp_path)
    from unified_chat.storage.repo import MemoryRepo, MessageRepo

    svc = LearningService(FakeContext(), PluginConfig(chat_provider_id="p1"))
    await svc.maybe_learn(FakeEvent("hello world long message"), "alice")
    assert await MemoryRepo.list_all() == []
    assert await MessageRepo.count() == 1
```

- [ ] **Step 2: Verify failures** → FAIL missing module

- [ ] **Step 3: Implement learning_service.py**

```python
import asyncio
import contextlib

from unified_chat.services.chat_service import ChatService
from unified_chat.storage import repo as repos
from unified_chat.storage.models import LearningLog, Memory, MessageRecord
from unified_chat.utils.hashing import dedup_hash

REFINE_SYSTEM_PROMPT = (
    "Distill the following chat message into ONE concise, durable fact or "
    "preference statement about the user, in the message's language. "
    "Reply with exactly one line. If nothing durable can be extracted, "
    "reply with an empty line."
)


class LearningService:
    MIN_LEARN_CHARS = 8

    def __init__(self, context, config):
        self.context = context
        self.config = config
        self._semaphore = asyncio.Semaphore(2)

    def should_learn(self, event) -> bool:
        if not self.config.enable_adaptive_learning:
            return False
        text = getattr(event, "message_str", "")
        if ChatService.is_command(text):
            return False
        return len(text.strip()) >= self.MIN_LEARN_CHARS

    async def refine(self, text: str) -> str:
        if not self.config.chat_provider_id:
            return ""
        llm_generate = getattr(self.context, "llm_generate", None)
        if llm_generate is None:
            return ""
        async with self._semaphore:
            try:
                resp = await llm_generate(
                    chat_provider_id=self.config.chat_provider_id,
                    prompt=text,
                    system_prompt=REFINE_SYSTEM_PROMPT,
                )
                out = (getattr(resp, "completion_text", "") or "").strip()
                return out
            except Exception:
                self._log_error("refine")
                return ""

    async def maybe_learn(self, event, sender_id: str) -> None:
        try:
            if not self.should_learn(event):
                return
            text = event.message_str
            h = dedup_hash(text)
            if await repos.MessageRepo.exists_hash(h):
                return
            await repos.MessageRepo.add(
                MessageRecord(
                    umo=event.unified_msg_origin,
                    sender_id=sender_id,
                    group_id=str(getattr(event, "get_group_id", lambda: "")() or ""),
                    content=text,
                    dedup_hash=h,
                )
            )
            await repos.LearningLogRepo.add(
                LearningLog(stage="filter", input_text=text, output_text="", provider_id="")
            )
            if not self.config.chat_provider_id:
                return
            refined = await self.refine(text)
            if not refined:
                return
            await repos.LearningLogRepo.add(
                LearningLog(
                    stage="refine",
                    input_text=text,
                    output_text=refined,
                    provider_id=self.config.chat_provider_id,
                )
            )
            if len(refined) < self.MIN_LEARN_CHARS:
                return
            rh = dedup_hash(refined)
            if await repos.MemoryRepo.exists_hash(rh):
                return
            await repos.MemoryRepo.add(
                Memory(content=refined, importance=0.5, source="learning", dedup_hash=rh)
            )
            await repos.LearningLogRepo.add(
                LearningLog(stage="reinforce", input_text=refined, output_text="", provider_id="")
            )
        except Exception:
            self._log_error("maybe_learn")

    @staticmethod
    def _log_error(msg: str) -> None:
        with contextlib.suppress(Exception):
            from astrbot.api import logger

            logger.error(f"[unified_chat] learning {msg}", exc_info=True)
```

- [ ] **Step 4: Verify + LSP** → PASS, clean

- [ ] **Step 5: Commit**

```bash
git add unified_chat/services/learning_service.py tests/test_learning_service.py
git commit -m "feat(learning): add LearningService with filter-refine-reinforce pipeline"
```

---

### Task 3: Pipeline + lifecycle wiring

**Files:**
- Modify: `unified_chat/core/pipeline.py`, `unified_chat/core/lifecycle.py`
- Test: `tests/test_lifecycle_learning.py`

**Interfaces:**
- Consumes: `LearningService`
- Produces: `MessagePipeline(config, chat_service, memory_service=None, learning_service=None)`; `_after_stages` runs memory then learning; lifecycle creates and passes LearningService

- [ ] **Step 1: Write failing tests**

```python
# tests/test_lifecycle_learning.py
import asyncio
import pytest
from unittest.mock import patch
from unified_chat.core.lifecycle import PluginLifecycle
from unified_chat.storage.database import close_engine, reset_engine_for_tests


class FakeContext:
    def __init__(self, cfg=None):
        self._cfg = cfg or {}

    def get_config(self):
        return self._cfg


class FakeEvent:
    def __init__(self, text, umo="p:m:1"):
        self.message_str = text
        self.unified_msg_origin = umo

    def get_sender_name(self):
        return "alice"

    def is_private_chat(self):
        return False


@pytest.mark.asyncio
async def test_handle_message_learns_in_background(tmp_path):
    reset_engine_for_tests()
    with patch("unified_chat.utils.path.resolve_data_dir", lambda raw, ctx: tmp_path / "data"):
        lc = PluginLifecycle(None, FakeContext())
        await lc.on_load()
        await lc.handle_message(FakeEvent("hello world learning message"))
        await asyncio.sleep(0.1)
        from unified_chat.storage.repo import MessageRepo

        assert await MessageRepo.count() == 1
        await lc.on_unload()
        await close_engine()
```

- [ ] **Step 2: Verify failure** → FAIL (no learning in pipeline)

- [ ] **Step 3: Implement wiring**

```python
# pipeline.py: __init__(..., learning_service=None); self.learning_service = learning_service
# _after_stages: after memory stage:
if self.learning_service is not None:
    await self.learning_service.maybe_learn(event, sender_id)

# lifecycle.py on_load after memory service:
from unified_chat.services.learning_service import LearningService

self._learning_service = LearningService(self.context, config)
self._pipeline = MessagePipeline(
    config, self._chat_service, self._memory_service, self._learning_service
)
```

- [ ] **Step 4: Verify + LSP + ruff + full pytest**

- [ ] **Step 5: Commit**

```bash
git add unified_chat/core/pipeline.py unified_chat/core/lifecycle.py tests/test_lifecycle_learning.py
git commit -m "feat(learning): wire LearningService into pipeline background stages"
```

---

## Self-Review

- Spec R001→T2 (degrade), R002/R003/R004→T2, R005→T2 (semaphore), R006→T1, R007→T3, R008→T2/T3.
- Types consistent: `refine(str) -> str`, `maybe_learn(event, sender_id) -> None`, `exists_hash(str) -> bool`.

## Execution Handoff

Inline execution in this session.
