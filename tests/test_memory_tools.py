"""Tests for agent-facing recall/memorize tools."""

from pathlib import Path

import pytest

from unified_chat.config import PluginConfig
from unified_chat.services.memory_service import MemoryService
from unified_chat.services.memory_tools import build_memory_tools, inject_memory_tools
from unified_chat.storage.database import close_engine, get_engine, reset_engine_for_tests
from unified_chat.storage.repo import MemoryRepo


class FakeEvent:
    message_str = "remember"
    unified_msg_origin = "group:GroupMessage:test-group"


class FakeReq:
    func_tool = None


@pytest.mark.asyncio
async def test_memorize_tool_stores_atom_and_dedupes(tmp_path: Path):
    reset_engine_for_tests()
    await get_engine(tmp_path / "tool.db")
    try:
        service = MemoryService(None, PluginConfig())
        tools = {tool.name: tool for tool in build_memory_tools(service, "session-1")}
        tool = tools["unified_chat_memory_memorize"]
        result1 = await tool.call(None, text="the user prefers dark mode")
        result2 = await tool.call(None, text="the user prefers dark mode")
        assert "memorized as id=" in result1
        assert result1 == result2
        assert await MemoryRepo.count() == 1
    finally:
        await close_engine()
        reset_engine_for_tests()


@pytest.mark.asyncio
async def test_memory_tools_require_agentic_mode():
    req = FakeReq()
    cfg = PluginConfig(enable_persistent_memory=True, rag_agentic=False)
    await inject_memory_tools(FakeEvent(), req, cfg, object())
    assert req.func_tool is None
