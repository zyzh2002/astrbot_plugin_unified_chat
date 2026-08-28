"""Tests for MigrationService."""

import pathlib

import pytest

from unified_chat.config import PluginConfig
from unified_chat.services.migration_service import MigrationService
from unified_chat.storage.database import get_engine, reset_engine_for_tests


class FakeDoc:
    def __init__(self, doc_id, file_name):
        self.doc_id = doc_id
        self.file_name = file_name


class FakeUploadedDoc:
    def __init__(self, doc_id):
        self.doc_id = doc_id


class FakeKbHelper:
    def __init__(self, docs, chunks_by_doc, new_doc_ids):
        self.docs = docs
        self.chunks_by_doc = chunks_by_doc
        self.deleted = []
        self.uploads = []
        self._new = iter(new_doc_ids)

    async def list_documents(self, offset=0, limit=100, search=None):
        return self.docs[offset : offset + limit]

    async def get_chunks_by_doc_id(self, doc_id, offset=0, limit=100):
        return self.chunks_by_doc.get(doc_id, [])[offset : offset + limit]

    async def delete_document(self, doc_id):
        self.deleted.append(doc_id)

    async def upload_document(
        self, file_name, file_content, file_type, pre_chunked_text=None, **kw
    ):
        self.uploads.append((file_name, pre_chunked_text))
        return FakeUploadedDoc(next(self._new))


class FakeKbManager:
    def __init__(self, helper):
        self.helper = helper

    async def get_kb_by_name(self, name):
        return self.helper


class FakeContext:
    def __init__(self, helper):
        self.kb_manager = FakeKbManager(helper)


@pytest.fixture(autouse=True)
def _fresh_engine(tmp_path):
    reset_engine_for_tests()
    yield tmp_path
    reset_engine_for_tests()


def _db(tmp_path):
    return get_engine(pathlib.Path(tmp_path) / "m.db")


@pytest.mark.asyncio
async def test_migration_rebuilds(tmp_path):
    await _db(tmp_path)
    docs = [FakeDoc("d1", "d1.txt"), FakeDoc("d2", "d2.txt")]
    chunks = {"d1": [{"content": "c1"}], "d2": [{"content": "c2"}, {"content": "c3"}]}
    helper = FakeKbHelper(docs, chunks, ["n1", "n2"])
    svc = MigrationService(FakeContext(helper), PluginConfig())
    result = await svc.run_migration("kb1")
    assert "2" in result
    assert helper.deleted == ["d1", "d2"]
    assert [u[1] for u in helper.uploads] == [["c1"], ["c2", "c3"]]


@pytest.mark.asyncio
async def test_migration_missing_kb(tmp_path):
    await _db(tmp_path)
    svc = MigrationService(FakeContext(None), PluginConfig())
    result = await svc.run_migration("kb1")
    assert "not found" in result
    assert not await svc.is_running("kb1")


@pytest.mark.asyncio
async def test_migration_flag_lifecycle(tmp_path):
    await _db(tmp_path)
    helper = FakeKbHelper([], {}, [])
    svc = MigrationService(FakeContext(helper), PluginConfig())
    assert not await svc.is_running("kb1")
    await svc.run_migration("kb1")
    assert not await svc.is_running("kb1")


@pytest.mark.asyncio
async def test_migration_failure_clears_flag(tmp_path):
    await _db(tmp_path)

    class BoomHelper(FakeKbHelper):
        async def list_documents(self, offset=0, limit=100, search=None):
            raise RuntimeError("boom")

    svc = MigrationService(FakeContext(BoomHelper([], {}, [])), PluginConfig())
    result = await svc.run_migration("kb1")
    assert "failed" in result
    assert not await svc.is_running("kb1")


@pytest.mark.asyncio
async def test_memory_kb_migration_clears_links(tmp_path):
    await _db(tmp_path)
    from unified_chat.storage.models import Memory
    from unified_chat.storage.repo import MemoryRepo

    await MemoryRepo.add(Memory(content="m", dedup_hash="h1", kb_doc_id="old-doc"))
    docs = [FakeDoc("d1", "memory_1.txt")]
    helper = FakeKbHelper(docs, {"d1": [{"content": "m"}]}, ["n1"])
    svc = MigrationService(FakeContext(helper), PluginConfig())
    result = await svc.run_migration("unified_chat_memories")
    assert "1" in result
    all_mems = await MemoryRepo.list_all()
    assert all(m.kb_doc_id is None for m in all_mems)


@pytest.mark.asyncio
async def test_migration_upload_failure_contained(tmp_path):
    await _db(tmp_path)
    from unified_chat.storage import kv as kv_store

    docs = [FakeDoc("d1", "d1.txt"), FakeDoc("d2", "d2.txt"), FakeDoc("d3", "d3.txt")]
    chunks = {
        "d1": [{"content": "c1"}],
        "d2": [{"content": "c2"}],
        "d3": [{"content": "c3"}],
    }

    class FlakyHelper(FakeKbHelper):
        async def upload_document(
            self, file_name, file_content, file_type, pre_chunked_text=None, **kw
        ):
            if file_name == "d2.txt":
                raise RuntimeError("provider down")
            return await super().upload_document(
                file_name, file_content, file_type, pre_chunked_text=pre_chunked_text, **kw
            )

    helper = FlakyHelper(docs, chunks, ["n1", "n2", "n3", "n4"])
    svc = MigrationService(FakeContext(helper), PluginConfig())
    result = await svc.run_migration("kb1")
    assert "failed" in result
    # untouched docs survive; the failing doc gets a best-effort orphan restore
    assert "d3" not in helper.deleted
    assert any(name.startswith("__orphan_") for name, _ in helper.uploads)
    last = await kv_store.kv_get("migration:kb1:last_result")
    assert last is not None and "failed" in last


@pytest.mark.asyncio
async def test_stale_running_flag_recovers(tmp_path):
    await _db(tmp_path)
    import json
    import time as _time

    from unified_chat.storage import kv as kv_store

    await kv_store.kv_set(
        "migration:kb1:running", json.dumps({"started": _time.time() - 7 * 3600})
    )
    svc = MigrationService(FakeContext(FakeKbHelper([], {}, [])), PluginConfig())
    assert not await svc.is_running("kb1")
    assert await kv_store.kv_get("migration:kb1:running") is None


@pytest.mark.asyncio
async def test_migration_result_persisted_on_success(tmp_path):
    await _db(tmp_path)
    from unified_chat.storage import kv as kv_store
    from unified_chat.storage.repo import LearningLogRepo

    helper = FakeKbHelper([FakeDoc("d1", "d1.txt")], {"d1": [{"content": "c1"}]}, ["n1"])
    svc = MigrationService(FakeContext(helper), PluginConfig())
    result = await svc.run_migration("kb1")
    assert "done" in result
    last = await kv_store.kv_get("migration:kb1:last_result")
    assert last is not None and "done" in last
    assert await LearningLogRepo.count_by_stage("migration") == 1


@pytest.mark.asyncio
async def test_memory_kb_success_updates_snapshot(tmp_path):
    await _db(tmp_path)
    from unified_chat.storage import kv as kv_store

    cfg = PluginConfig(
        memory_kb_name="unified_chat_memories", embedding_provider_id="ep9"
    )
    docs = [FakeDoc("d1", "memory_1.txt")]
    helper = FakeKbHelper(docs, {"d1": [{"content": "m"}]}, ["n1"])
    svc = MigrationService(FakeContext(helper), cfg)
    await svc.run_migration("unified_chat_memories")
    assert await kv_store.kv_get("embedding_provider_snapshot") == "ep9"


@pytest.mark.asyncio
async def test_memory_kb_failure_keeps_snapshot(tmp_path):
    await _db(tmp_path)
    from unified_chat.storage import kv as kv_store

    class BoomHelper(FakeKbHelper):
        async def list_documents(self, offset=0, limit=100, search=None):
            raise RuntimeError("boom")

    cfg = PluginConfig(
        memory_kb_name="unified_chat_memories", embedding_provider_id="ep9"
    )
    svc = MigrationService(FakeContext(BoomHelper([], {}, [])), cfg)
    await svc.run_migration("unified_chat_memories")
    assert await kv_store.kv_get("embedding_provider_snapshot") is None
