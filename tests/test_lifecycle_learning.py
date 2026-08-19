"""Tests for lifecycle learning wiring."""

import asyncio
from unittest.mock import patch

import pytest

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
