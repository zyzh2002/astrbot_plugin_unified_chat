"""Tests for RagService."""

import pytest

from unified_chat.services.rag_service import RagService


class FakeKbManager:
    def __init__(self):
        self.calls = []

    async def retrieve(self, query, kb_names, top_k_fusion=20, top_m_final=5):
        self.calls.append((query, list(kb_names), top_k_fusion, top_m_final))
        return {"context_text": "CTX", "results": []}


class FakeContext:
    def __init__(self, kb_manager=None):
        self.kb_manager = kb_manager


def test_build_none_when_no_kbs():
    svc = RagService(FakeContext(FakeKbManager()))
    assert svc.build_kb_tool([]) is None


def test_build_none_when_no_kb_manager():
    assert RagService(FakeContext(None)).build_kb_tool(["kb1"]) is None


def test_build_tool_fields():
    mgr = FakeKbManager()
    tool = RagService(FakeContext(mgr)).build_kb_tool(["kb1", "kb2"])
    assert tool is not None
    assert tool.name == "unified_chat_kb_query"
    assert tool.parameters["required"] == ["query"]


@pytest.mark.asyncio
async def test_call_queries_configured_kbs():
    mgr = FakeKbManager()
    tool = RagService(FakeContext(mgr)).build_kb_tool(["kb1", "kb2"])
    result = await tool.call(None, query="hello")
    assert result == "CTX"
    assert mgr.calls == [("hello", ["kb1", "kb2"], 20, 5)]


@pytest.mark.asyncio
async def test_call_empty_query():
    mgr = FakeKbManager()
    tool = RagService(FakeContext(mgr)).build_kb_tool(["kb1"])
    assert await tool.call(None, query="  ") == "error: Query parameter is empty."


@pytest.mark.asyncio
async def test_call_no_result():
    class EmptyMgr:
        async def retrieve(self, **kw):
            return None

    tool = RagService(FakeContext(EmptyMgr())).build_kb_tool(["kb1"])
    assert await tool.call(None, query="q") == "No relevant knowledge found."


@pytest.mark.asyncio
async def test_call_retrieve_error():
    class BoomMgr:
        async def retrieve(self, **kw):
            raise ValueError("all unavailable")

    tool = RagService(FakeContext(BoomMgr())).build_kb_tool(["kb1"])
    result = await tool.call(None, query="q")
    assert result.startswith("error:")
