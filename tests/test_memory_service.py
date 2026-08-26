"""Tests for MemoryService."""

import pytest

from unified_chat.config import PluginConfig
from unified_chat.services.memory_service import MemoryService
from unified_chat.storage.database import close_engine, reset_engine_for_tests


class FakeEvent:
    def __init__(self, text, umo="p:m:1", sender="alice"):
        self.message_str = text
        self.unified_msg_origin = umo
        self._sender = sender

    def get_sender_name(self):
        return self._sender

    def is_private_chat(self):
        return False


class FakeDoc:
    doc_id = "doc9"


class FakeKbHelper:
    def __init__(self):
        self.uploads = []
        self.deletes = []

    async def upload_document(
        self, file_name, file_content, file_type, pre_chunked_text=None, **kw
    ):
        self.uploads.append((file_name, pre_chunked_text))
        return FakeDoc()

    async def delete_document(self, doc_id):
        self.deletes.append(doc_id)


class FakeKbManager:
    def __init__(self, helper=None):
        self.helper = helper
        self.created = []
        self.retrieves = []

    async def get_kb_by_name(self, name):
        return self.helper

    async def create_kb(self, kb_name, **kw):
        self.created.append(kb_name)
        self.helper = FakeKbHelper()
        return self.helper

    async def retrieve(self, query, kb_names, **kw):
        self.retrieves.append((query, kb_names))
        return {"context_text": "MEM", "results": []}


class FakeContext:
    def __init__(self, kb_manager):
        self.kb_manager = kb_manager


def test_compute_importance_bounds():
    svc = MemoryService(FakeContext(None), PluginConfig())
    v = svc.compute_importance("hello world", "alice", [])
    assert 0.0 <= v <= 1.0


def test_should_store_gates():
    svc = MemoryService(FakeContext(None), PluginConfig())
    assert not svc.should_store(FakeEvent("/cmd"))
    assert not svc.should_store(FakeEvent("short"))
    assert svc.should_store(FakeEvent("this is a long enough memory candidate message"))


@pytest.mark.asyncio
async def test_ensure_memory_kb_skip_without_embedding():
    svc = MemoryService(FakeContext(FakeKbManager()), PluginConfig())
    await svc.ensure_memory_kb()
    assert svc._kb_helper is None


@pytest.mark.asyncio
async def test_ensure_memory_kb_creates():
    mgr = FakeKbManager()
    cfg = PluginConfig(embedding_provider_id="ep1")
    svc = MemoryService(FakeContext(mgr), cfg)
    await svc.ensure_memory_kb()
    assert mgr.created == ["unified_chat_memories"]
    assert svc._kb_helper is mgr.helper


@pytest.mark.asyncio
async def test_ensure_memory_kb_reuses():
    helper = FakeKbHelper()
    mgr = FakeKbManager(helper)
    svc = MemoryService(FakeContext(mgr), PluginConfig(embedding_provider_id="ep1"))
    await svc.ensure_memory_kb()
    assert mgr.created == []
    assert svc._kb_helper is helper


@pytest.mark.asyncio
async def test_maybe_store_sqlite_only(tmp_path):
    import pathlib

    from unified_chat.storage.database import get_engine

    reset_engine_for_tests()
    await get_engine(pathlib.Path(tmp_path) / "m.db")
    svc = MemoryService(FakeContext(None), PluginConfig())
    svc._kb_helper = None
    await svc.maybe_store(FakeEvent("this is a long enough memory candidate message"), "alice")
    from unified_chat.storage.repo import MemoryRepo

    all_mems = await MemoryRepo.list_all()
    assert len(all_mems) == 1
    assert all_mems[0].source == "alice"
    assert all_mems[0].kb_doc_id is None
    await close_engine()


@pytest.mark.asyncio
async def test_maybe_store_uploads_high_importance(tmp_path):
    import pathlib

    from unified_chat.storage.database import get_engine

    reset_engine_for_tests()
    await get_engine(pathlib.Path(tmp_path) / "m2.db")
    helper = FakeKbHelper()
    svc = MemoryService(FakeContext(FakeKbManager(helper)), PluginConfig())
    svc._kb_helper = helper
    text = "this is a long enough memory candidate message for kb upload"
    await svc.maybe_store(FakeEvent(text), "alice")
    assert len(helper.uploads) == 1
    assert helper.uploads[0][1] == [text]
    from unified_chat.storage.repo import MemoryRepo

    all_mems = await MemoryRepo.list_all()
    assert all_mems[0].kb_doc_id == "doc9"
    await close_engine()


@pytest.mark.asyncio
async def test_retrieve_kb_and_fallback(tmp_path):
    import pathlib

    from unified_chat.storage.database import get_engine

    reset_engine_for_tests()
    await get_engine(pathlib.Path(tmp_path) / "m3.db")
    mgr = FakeKbManager(FakeKbHelper())
    svc = MemoryService(FakeContext(mgr), PluginConfig(embedding_provider_id="ep1"))
    svc._kb_helper = mgr.helper
    assert await svc.retrieve("hello") == ""
    svc._kb_helper = None
    mgr.retrieves.clear()
    from unified_chat.storage.repo import MemoryRepo

    await MemoryRepo.add(
        __import__("unified_chat.storage.models", fromlist=["Memory"]).Memory(
            content="hello memory world", importance=0.9
        )
    )
    result = await svc.retrieve("hello")
    assert "hello memory world" in result
    await close_engine()


@pytest.mark.asyncio
async def test_kb_results_are_filtered_by_session_and_ttl(tmp_path):
    import pathlib
    from datetime import UTC, datetime, timedelta

    from unified_chat.storage.database import get_engine
    from unified_chat.storage.models import Memory
    from unified_chat.storage.repo import MemoryRepo

    reset_engine_for_tests()
    await get_engine(pathlib.Path(tmp_path) / "kb-filter.db")

    class Manager(FakeKbManager):
        async def retrieve(self, **kw):
            return {
                "context_text": "LEAK-MARKER",
                "results": [
                    {"doc_id": "s1"},
                    {"doc_id": "s2"},
                    {"doc_id": "global"},
                    {"doc_id": "expired"},
                ],
            }

    mgr = Manager(FakeKbHelper())
    svc = MemoryService(FakeContext(mgr), PluginConfig(embedding_provider_id="ep1"))
    svc._kb_helper = mgr.helper
    rows = [
        Memory(content="session one", session_id="one", kb_doc_id="s1"),
        Memory(content="session two", session_id="two", kb_doc_id="s2"),
        Memory(content="global visible", session_id="", kb_doc_id="global"),
        Memory(
            content="expired hidden",
            session_id="one",
            kb_doc_id="expired",
            expires_at=datetime.now(UTC) - timedelta(days=1),
        ),
    ]
    for row in rows:
        await MemoryRepo.add(row)
    result = await svc.retrieve("query", session_id="one")
    assert "session one" in result and "global visible" in result
    assert "session two" not in result and "expired hidden" not in result
    assert "LEAK-MARKER" not in result
    await close_engine()
