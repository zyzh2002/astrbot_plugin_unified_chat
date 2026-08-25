"""Tests for lifecycle RAG wiring."""

from unittest.mock import patch

import pytest

from unified_chat.core.lifecycle import PluginLifecycle
from unified_chat.storage.database import reset_engine_for_tests


class FakeContext:
    def __init__(self, cfg, kb_manager):
        self._cfg = cfg
        self.kb_manager = kb_manager

    def get_config(self):
        return self._cfg


class FakeKbManager:
    async def retrieve(self, **kw):
        return {"context_text": "C"}


class FakeReq:
    def __init__(self):
        self.func_tool = None


@pytest.mark.asyncio
async def test_handle_llm_request_injects_tool(tmp_path):
    reset_engine_for_tests()
    with patch("unified_chat.utils.path.resolve_data_dir", lambda raw, ctx: tmp_path / "data"):
        ctx = FakeContext({"rag_kbs": ["kb1"]}, FakeKbManager())
        lc = PluginLifecycle(None, ctx)
        await lc.on_load()
        req = FakeReq()
        await lc.handle_llm_request(None, req)
        assert req.func_tool is not None
        assert req.func_tool.get_tool("unified_chat_kb_query") is not None
        await lc.on_unload()


@pytest.mark.asyncio
async def test_handle_llm_request_agentic_off(tmp_path):
    reset_engine_for_tests()
    with patch("unified_chat.utils.path.resolve_data_dir", lambda raw, ctx: tmp_path / "data"):
        ctx = FakeContext({"rag_kbs": ["kb1"], "rag_agentic": False}, FakeKbManager())
        lc = PluginLifecycle(None, ctx)
        await lc.on_load()
        req = FakeReq()
        await lc.handle_llm_request(None, req)
        kb_tool = (
            req.func_tool.get_tool("unified_chat_kb_query") if req.func_tool is not None else None
        )
        assert kb_tool is None
        await lc.on_unload()
