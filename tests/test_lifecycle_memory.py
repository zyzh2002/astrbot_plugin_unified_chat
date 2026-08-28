"""Tests for lifecycle memory wiring."""

import asyncio
from unittest.mock import patch

import pytest

from unified_chat.core.lifecycle import PluginLifecycle
from unified_chat.storage import repo as repos
from unified_chat.storage.database import close_engine, reset_engine_for_tests


class FakeContext:
    def __init__(self, cfg=None, kb_manager=None):
        self._cfg = cfg or {}
        self.kb_manager = kb_manager

    def get_config(self):
        return self._cfg


class FakeEvent:
    def __init__(self, text, umo="p:m:1"):
        self.message_str = text
        self.unified_msg_origin = umo

    def get_sender_name(self):
        return "alice"

    def get_sender_id(self):
        return "alice-id"

    def is_private_chat(self):
        return False


@pytest.mark.asyncio
async def test_lifecycle_creates_memory_service_and_cron(tmp_path):
    reset_engine_for_tests()
    with patch("unified_chat.utils.path.resolve_data_dir", lambda raw, ctx: tmp_path / "data"):
        lc = PluginLifecycle(None, FakeContext())
        await lc.on_load()
        assert lc._memory_service is not None
        assert lc._cron is not None
        assert lc._cron._task is not None
        assert lc._cron.backup_service is lc._backup_service
        await lc.on_unload()
        assert lc._cron._task is None


@pytest.mark.asyncio
async def test_lifecycle_uses_injected_plugin_config(tmp_path):
    reset_engine_for_tests()
    global_cfg = {"humanize_enable": False, "native_autodownload": True}
    plugin_cfg = {"humanize_enable": True, "native_autodownload": False}
    with patch("unified_chat.utils.path.resolve_data_dir", lambda raw, ctx: tmp_path / "data"):
        lc = PluginLifecycle(None, FakeContext(global_cfg), plugin_cfg)
        await lc.on_load()
        assert lc._config is not None
        assert lc._config.humanize_enable is True
        assert lc._config.native_autodownload is False
        await lc.on_unload()


@pytest.mark.asyncio
async def test_handle_message_stores_memory_in_background(tmp_path):
    reset_engine_for_tests()
    with patch("unified_chat.utils.path.resolve_data_dir", lambda raw, ctx: tmp_path / "data"):
        lc = PluginLifecycle(None, FakeContext())
        await lc.on_load()
        text = "this is a long enough memory candidate message"
        await lc.handle_message(FakeEvent(text))
        await asyncio.sleep(0.1)  # background task
        from unified_chat.storage.repo import MemoryRepo

        memories = await MemoryRepo.list_all()
        assert len(memories) == 1
        assert memories[0].content == text
        await lc.on_unload()
        await close_engine()


@pytest.mark.asyncio
async def test_umem_forget_cannot_delete_other_session(tmp_path):
    reset_engine_for_tests()
    with patch("unified_chat.utils.path.resolve_data_dir", lambda raw, ctx: tmp_path / "data"):
        lc = PluginLifecycle(None, FakeContext())
        await lc.on_load()
        own = await lc._memory_service.memorize_text(
            "own session durable fact",
            session_id="p:m:1",
        )
        other = await lc._memory_service.memorize_text(
            "other session durable fact",
            session_id="p:m:2",
        )
        event = FakeEvent("/umem", umo="p:m:1")
        assert "deleted 0" in await lc.umem(event, "forget", str(other))
        assert "deleted 1" in await lc.umem(event, "forget", str(own))
        await lc.on_unload()


@pytest.mark.asyncio
async def test_umem_reset_refuses_global_scope_when_isolation_off(tmp_path):
    # spec 011 R3: with isolation off, session_id is "" and reset used to
    # wipe the ENTIRE shared pool; it must refuse instead.
    reset_engine_for_tests()
    cfg = {"memory_session_isolation": False}
    with patch("unified_chat.utils.path.resolve_data_dir", lambda raw, ctx: tmp_path / "data"):
        lc = PluginLifecycle(None, FakeContext(cfg), cfg)
        await lc.on_load()
        await lc._memory_service.memorize_text("global shared durable fact")
        event = FakeEvent("/umem", umo="p:m:1")
        reply = await lc.umem(event, "reset", "")
        assert "isolation" in reply
        assert await repos.MemoryRepo.count() == 1  # untouched
        await lc.on_unload()
