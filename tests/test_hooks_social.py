"""Tests for inject_social_context hook."""

import pytest

from unified_chat.config import PluginConfig
from unified_chat.core.hooks import inject_social_context
from unified_chat.services.chat_service import ChatService


class FakeEvent:
    def __init__(self, text="x", umo="p:m:1", private=False):
        self.message_str = text
        self.unified_msg_origin = umo
        self._private = private

    def get_sender_name(self):
        return "alice"

    def is_private_chat(self):
        return self._private


class FakeReq:
    def __init__(self):
        self.contexts = []


@pytest.mark.asyncio
async def test_gate_off():
    req = FakeReq()
    await inject_social_context(
        FakeEvent(), req, PluginConfig(enable_conversation_enhance=False), ChatService()
    )
    assert req.contexts == []


@pytest.mark.asyncio
async def test_empty_when_no_history():
    req = FakeReq()
    await inject_social_context(FakeEvent(), req, PluginConfig(), ChatService())
    assert req.contexts == []


@pytest.mark.asyncio
async def test_appends_system_context():
    svc = ChatService()
    ev = FakeEvent()
    svc.record(ev)
    req = FakeReq()
    await inject_social_context(ev, req, PluginConfig(), svc)
    assert len(req.contexts) == 1
    assert req.contexts[0]["role"] == "system"
    assert "alice" in req.contexts[0]["content"]


@pytest.mark.asyncio
async def test_none_contexts_replaced():
    req = FakeReq()
    req.contexts = None
    svc = ChatService()
    ev = FakeEvent()
    svc.record(ev)
    await inject_social_context(ev, req, PluginConfig(), svc)
    assert isinstance(req.contexts, list) and len(req.contexts) == 1
