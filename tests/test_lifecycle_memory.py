"""Tests for lifecycle memory wiring."""

import asyncio
from unittest.mock import patch

import pytest

from unified_chat.core.lifecycle import PluginLifecycle
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
        await lc.on_unload()
        assert lc._cron._task is None


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
