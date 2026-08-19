"""Tests for core.hooks.inject_kb_tool."""

import pytest

from unified_chat.config import PluginConfig
from unified_chat.core.hooks import inject_kb_tool
from unified_chat.services.rag_service import RagService


class FakeReq:
    def __init__(self):
        self.func_tool = None


class FakeContext:
    def __init__(self, kb_manager=None):
        self.kb_manager = kb_manager


class FakeKbManager:
    async def retrieve(self, **kw):
        return {"context_text": "C"}


@pytest.mark.asyncio
async def test_no_inject_when_agentic_off():
    req = FakeReq()
    cfg = PluginConfig(rag_agentic=False, rag_kbs=["kb1"])
    await inject_kb_tool(None, req, cfg, RagService(FakeContext(FakeKbManager())))
    assert req.func_tool is None


@pytest.mark.asyncio
async def test_no_inject_when_no_kbs():
    req = FakeReq()
    cfg = PluginConfig(rag_agentic=True, rag_kbs=[])
    await inject_kb_tool(None, req, cfg, RagService(FakeContext(FakeKbManager())))
    assert req.func_tool is None


@pytest.mark.asyncio
async def test_inject_creates_toolset():
    req = FakeReq()
    cfg = PluginConfig(rag_agentic=True, rag_kbs=["kb1"])
    await inject_kb_tool(None, req, cfg, RagService(FakeContext(FakeKbManager())))
    assert req.func_tool is not None
    assert req.func_tool.get_tool("unified_chat_kb_query") is not None


@pytest.mark.asyncio
async def test_inject_preserves_existing():
    from astrbot.core.agent.tool import FunctionTool, ToolSet

    req = FakeReq()
    req.func_tool = ToolSet()
    req.func_tool.add_tool(FunctionTool(name="other", description="x", parameters={}))
    cfg = PluginConfig(rag_agentic=True, rag_kbs=["kb1"])
    await inject_kb_tool(None, req, cfg, RagService(FakeContext(FakeKbManager())))
    assert len(req.func_tool.tools) == 2


@pytest.mark.asyncio
async def test_no_duplicate():
    req = FakeReq()
    cfg = PluginConfig(rag_agentic=True, rag_kbs=["kb1"])
    svc = RagService(FakeContext(FakeKbManager()))
    await inject_kb_tool(None, req, cfg, svc)
    await inject_kb_tool(None, req, cfg, svc)
    assert len(req.func_tool.tools) == 1
