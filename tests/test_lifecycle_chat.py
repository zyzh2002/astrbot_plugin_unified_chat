"""Tests for lifecycle chat/pipeline wiring."""

from unittest.mock import patch

import pytest

from unified_chat.core.lifecycle import PluginLifecycle
from unified_chat.storage.database import reset_engine_for_tests


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
async def test_handle_message_records_via_pipeline(tmp_path):
    reset_engine_for_tests()
    with patch("unified_chat.utils.path.resolve_data_dir", lambda raw, ctx: tmp_path / "data"):
        lc = PluginLifecycle(None, FakeContext())
        await lc.on_load()
        await lc.handle_message(FakeEvent("hello"))
        assert lc._chat_service is not None
        assert len(lc._chat_service._buffers["p:m:1"]) == 1  # type: ignore[reportOptionalMemberAccess]
        await lc.on_unload()


@pytest.mark.asyncio
async def test_handle_message_skips_command(tmp_path):
    reset_engine_for_tests()
    with patch("unified_chat.utils.path.resolve_data_dir", lambda raw, ctx: tmp_path / "data"):
        lc = PluginLifecycle(None, FakeContext())
        await lc.on_load()
        await lc.handle_message(FakeEvent("/cmd"))
        assert lc._chat_service is not None
        assert lc._chat_service._buffers.get("p:m:1") is None
        await lc.on_unload()


@pytest.mark.asyncio
async def test_handle_llm_request_injects_social(tmp_path):
    reset_engine_for_tests()
    with patch("unified_chat.utils.path.resolve_data_dir", lambda raw, ctx: tmp_path / "data"):
        lc = PluginLifecycle(None, FakeContext())
        await lc.on_load()
        ev = FakeEvent("hello")
        await lc.handle_message(ev)

        class FakeReq:
            def __init__(self):
                self.contexts = []
                self.func_tool = None

        req = FakeReq()
        await lc.handle_llm_request(ev, req)
        assert any(c["role"] == "system" for c in req.contexts)
        await lc.on_unload()
