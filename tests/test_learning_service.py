"""Tests for LearningService."""

import pathlib

import pytest

from unified_chat.config import PluginConfig
from unified_chat.services.learning_service import LearningService
from unified_chat.storage.database import get_engine, reset_engine_for_tests


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


@pytest.fixture(autouse=True)
def _fresh_engine(tmp_path):
    reset_engine_for_tests()
    yield tmp_path
    reset_engine_for_tests()


def _db(tmp_path):
    return get_engine(pathlib.Path(tmp_path) / "l.db")


def test_should_learn_gates():
    svc = LearningService(FakeContext(), PluginConfig())
    assert not svc.should_learn(FakeEvent("/cmd"))
    assert not svc.should_learn(FakeEvent("short"))
    assert svc.should_learn(FakeEvent("this is long enough"))


@pytest.mark.asyncio
async def test_refine_calls_llm(tmp_path):
    await _db(tmp_path)
    ctx = FakeContext(provider_id="p1")
    svc = LearningService(ctx, PluginConfig(chat_provider_id="p1"))
    out = await svc.refine("hello world message")
    assert out == "durable fact about alice"
    assert ctx.calls[0]["prompt"] == "hello world message"
    assert ctx.calls[0]["chat_provider_id"] == "p1"


@pytest.mark.asyncio
async def test_refine_missing_provider(tmp_path):
    await _db(tmp_path)
    svc = LearningService(FakeContext(), PluginConfig(chat_provider_id="p1"))
    assert await svc.refine("hello") == ""


@pytest.mark.asyncio
async def test_maybe_learn_degrade_mode(tmp_path):
    await _db(tmp_path)
    from unified_chat.storage.models import MessageRecord
    from unified_chat.storage.repo import MemoryRepo, MessageRepo
    from unified_chat.utils.hashing import dedup_hash

    # capture is owned by the pipeline; learning sees the message already stored
    svc = LearningService(FakeContext(), PluginConfig(chat_provider_id=""))
    text = "raw message long enough"
    await MessageRepo.add(
        MessageRecord(umo="p:m:1", sender_id="alice", content=text, dedup_hash=dedup_hash(text))
    )
    await svc.maybe_learn(FakeEvent(text), "alice")
    assert await MessageRepo.count() == 1  # no duplicate capture
    assert await MemoryRepo.list_all() == []
    await svc.maybe_learn(FakeEvent(text), "alice")
    assert await MessageRepo.count() == 1


@pytest.mark.asyncio
async def test_maybe_learn_full_pipeline(tmp_path):
    await _db(tmp_path)
    from unified_chat.storage.models import MessageRecord
    from unified_chat.storage.repo import MemoryRepo, MessageRepo
    from unified_chat.utils.hashing import dedup_hash

    svc = LearningService(FakeContext(provider_id="p1"), PluginConfig(chat_provider_id="p1"))
    text = "hello world long message"
    await MessageRepo.add(
        MessageRecord(umo="p:m:1", sender_id="alice", content=text, dedup_hash=dedup_hash(text))
    )
    await svc.maybe_learn(FakeEvent(text), "alice")
    mems = await MemoryRepo.list_all()
    assert len(mems) == 1
    assert mems[0].source == "learning"
    assert mems[0].content == "durable fact about alice"
    assert await MessageRepo.count() == 1
    await svc.maybe_learn(FakeEvent(text), "alice")
    assert len(await MemoryRepo.list_all()) == 1


@pytest.mark.asyncio
async def test_refine_failure_keeps_pipeline(tmp_path):
    await _db(tmp_path)
    from unified_chat.storage.models import MessageRecord
    from unified_chat.storage.repo import MemoryRepo, MessageRepo
    from unified_chat.utils.hashing import dedup_hash

    svc = LearningService(FakeContext(), PluginConfig(chat_provider_id="p1"))
    text = "hello world long message"
    await MessageRepo.add(
        MessageRecord(umo="p:m:1", sender_id="alice", content=text, dedup_hash=dedup_hash(text))
    )
    await svc.maybe_learn(FakeEvent(text), "alice")
    assert await MemoryRepo.list_all() == []
    assert await MessageRepo.count() == 1


@pytest.mark.asyncio
async def test_learning_uses_atom_writer_with_event_session(tmp_path):
    await _db(tmp_path)
    calls = []

    async def writer(text, **kwargs):
        calls.append((text, kwargs))
        return object(), True

    svc = LearningService(
        FakeContext(provider_id="p1"),
        PluginConfig(chat_provider_id="p1"),
        writer,
    )
    await svc.maybe_learn(
        FakeEvent("I prefer quiet mornings very much", umo="group:GroupMessage:g1"),
        "user-id",
    )
    assert calls == [
        (
            "durable fact about alice",
            {
                "source": "learning",
                "importance": 0.5,
                "session_id": "group:GroupMessage:g1",
            },
        )
    ]
