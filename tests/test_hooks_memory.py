"""Tests for inject_memories hook."""

import pytest

from unified_chat.config import PluginConfig
from unified_chat.core.hooks import inject_memories


class FakeEvent:
    def __init__(self, text="query text"):
        self.message_str = text


class FakeReq:
    def __init__(self):
        self.contexts = []


class FakeMemoryService:
    def __init__(self, result=""):
        self.result = result
        self.calls = []

    async def retrieve(self, query):
        self.calls.append(query)
        return self.result


@pytest.mark.asyncio
async def test_inject_memories_gate_off():
    req = FakeReq()
    svc = FakeMemoryService("mem")
    await inject_memories(FakeEvent(), req, PluginConfig(enable_persistent_memory=False), svc)
    assert req.contexts == []
    assert svc.calls == []


@pytest.mark.asyncio
async def test_inject_memories_appends():
    req = FakeReq()
    svc = FakeMemoryService("- a memory")
    await inject_memories(FakeEvent(), req, PluginConfig(), svc)
    assert svc.calls == ["query text"]
    assert len(req.contexts) == 1
    assert req.contexts[0]["role"] == "system"
    assert "- a memory" in req.contexts[0]["content"]


@pytest.mark.asyncio
async def test_inject_memories_empty_result():
    req = FakeReq()
    svc = FakeMemoryService("")
    await inject_memories(FakeEvent(), req, PluginConfig(), svc)
    assert req.contexts == []


@pytest.mark.asyncio
async def test_inject_memories_none_contexts():
    req = FakeReq()
    req.contexts = None
    svc = FakeMemoryService("m")
    await inject_memories(FakeEvent(), req, PluginConfig(), svc)
    assert isinstance(req.contexts, list) and len(req.contexts) == 1


@pytest.mark.asyncio
async def test_inject_memories_swallows_errors():
    class BoomService:
        async def retrieve(self, query):
            raise RuntimeError("boom")

    req = FakeReq()
    await inject_memories(FakeEvent(), req, PluginConfig(), BoomService())
    assert req.contexts == []
