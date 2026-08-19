"""Tests for MessagePipeline."""

import asyncio

import pytest

from unified_chat.config import PluginConfig
from unified_chat.core.pipeline import MessagePipeline
from unified_chat.services.chat_service import ChatService


class FakeEvent:
    def __init__(self, text, umo="p:m:1", sender="alice"):
        self.message_str = text
        self.unified_msg_origin = umo
        self._sender = sender

    def get_sender_name(self):
        return self._sender

    def is_private_chat(self):
        return False


@pytest.mark.asyncio
async def test_pipeline_skips_command():
    svc = ChatService()
    pipe = MessagePipeline(PluginConfig(), svc)
    await pipe.process(FakeEvent("/cmd"))
    assert svc._buffers.get("p:m:1") is None


@pytest.mark.asyncio
async def test_pipeline_records_and_dedups():
    svc = ChatService()
    pipe = MessagePipeline(PluginConfig(), svc)
    await pipe.process(FakeEvent("hello"))
    await pipe.process(FakeEvent("hello"))
    assert len(svc._buffers["p:m:1"]) == 1


@pytest.mark.asyncio
async def test_pipeline_gate_off():
    svc = ChatService()
    pipe = MessagePipeline(PluginConfig(enable_conversation_enhance=False), svc)
    await pipe.process(FakeEvent("hello"))
    assert svc._buffers.get("p:m:1") is None


@pytest.mark.asyncio
async def test_after_stages_task_fires(monkeypatch):
    created = []

    def fake_task(coro, name):
        created.append(name)
        return asyncio.ensure_future(coro)

    monkeypatch.setattr(asyncio, "create_task", fake_task)
    svc = ChatService()
    pipe = MessagePipeline(PluginConfig(), svc)
    await pipe.process(FakeEvent("hello"))
    assert any("unified_chat" in n for n in created)
