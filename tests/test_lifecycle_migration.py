"""Tests for lifecycle migration wiring."""

import asyncio
from unittest.mock import patch

import pytest

from unified_chat.core.lifecycle import PluginLifecycle
from unified_chat.storage import kv as kv_store
from unified_chat.storage.database import reset_engine_for_tests


class FakeContext:
    def __init__(self, cfg=None, kb_manager=None):
        self._cfg = cfg or {}
        self.kb_manager = kb_manager

    def get_config(self):
        return self._cfg


class FakeDoc:
    def __init__(self, doc_id, file_name):
        self.doc_id = doc_id
        self.file_name = file_name


class FakeUploadedDoc:
    doc_id = "n1"


class FakeKbHelper:
    def __init__(self):
        self.deleted = []
        self.uploads = []

    async def list_documents(self, offset=0, limit=100, search=None):
        if offset == 0:
            return [FakeDoc("d1", "d1.txt")]
        return []

    async def get_chunks_by_doc_id(self, doc_id, offset=0, limit=100):
        if offset == 0:
            return [{"content": "c1"}]
        return []

    async def delete_document(self, doc_id):
        self.deleted.append(doc_id)

    async def upload_document(
        self, file_name, file_content, file_type, pre_chunked_text=None, **kw
    ):
        self.uploads.append((file_name, pre_chunked_text))
        return FakeUploadedDoc()


class FakeKbManager:
    def __init__(self, helper):
        self.helper = helper

    async def get_kb_by_name(self, name):
        return self.helper


@pytest.fixture(autouse=True)
def _fresh_engine(tmp_path):
    reset_engine_for_tests()
    yield tmp_path
    reset_engine_for_tests()


async def _load(tmp_path, cfg=None):
    with patch("unified_chat.utils.path.resolve_data_dir", lambda raw, ctx: tmp_path / "data"):
        lc = PluginLifecycle(None, FakeContext(cfg or {}))
        await lc.on_load()
        return lc


@pytest.mark.asyncio
async def test_needs_migration_first_run(tmp_path):
    lc = await _load(tmp_path, {"embedding_provider_id": "ep1"})
    assert lc._needs_migration is False
    await lc.on_unload()


@pytest.mark.asyncio
async def test_needs_migration_on_change(tmp_path):
    from unified_chat.storage.database import get_engine

    await get_engine(tmp_path / "data" / "unified_chat.db")
    await kv_store.kv_set("embedding_provider_snapshot", "ep1")
    lc = await _load(tmp_path, {"embedding_provider_id": "ep2"})
    assert lc._needs_migration is True
    await lc.on_unload()


@pytest.mark.asyncio
async def test_migrate_kb_usage_and_start(tmp_path):
    helper = FakeKbHelper()
    lc = PluginLifecycle(None, FakeContext(kb_manager=FakeKbManager(helper)))
    lc._migration_service = None
    assert (await lc.migrate_kb(None, "")) == "Usage: /unified_migrate <kb_name>"
    assert (await lc.migrate_kb(None, "kb1")) == "Plugin not initialized"

    lc2 = await _load(tmp_path, {"embedding_provider_id": "ep1"})
    lc2._migration_service = None  # avoid needing kb manager
    from unified_chat.services.migration_service import MigrationService

    lc2._migration_service = MigrationService(
        FakeContext(kb_manager=FakeKbManager(helper)), lc2._config
    )
    result = await lc2.migrate_kb(None, "kb1")
    assert "started" in result
    await asyncio.sleep(0.05)
    # second call while running flag exists? run completed quickly; check task finished
    assert helper.deleted == ["d1"]
    assert helper.uploads == [("d1.txt", ["c1"])]
    await lc2.on_unload()


@pytest.mark.asyncio
async def test_status_async_counts(tmp_path):
    lc = await _load(tmp_path)
    status = await lc.get_status_async()
    assert "memories=" in status
    assert "messages=" in status
    assert "needs_migration=no" in status
    await lc.on_unload()
