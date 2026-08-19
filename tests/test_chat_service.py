"""Tests for ChatService."""

from unified_chat.services.chat_service import ChatService


class FakeEvent:
    def __init__(self, text, umo="p:m:1", sender="alice", private=False):
        self.message_str = text
        self.unified_msg_origin = umo
        self._sender = sender
        self._private = private

    def get_sender_name(self):
        return self._sender

    def is_private_chat(self):
        return self._private


def test_is_command():
    svc = ChatService()
    assert svc.is_command("/help")
    assert svc.is_command("   /help")
    assert svc.is_command("")
    assert svc.is_command("   ")
    assert not svc.is_command("hello world")


def test_should_process():
    svc = ChatService()
    assert not svc.should_process(FakeEvent("/cmd"))
    assert not svc.should_process(FakeEvent(""))
    assert svc.should_process(FakeEvent("hi there"))


def test_dedup_within_window():
    svc = ChatService()
    h = svc.hash_of("same text")
    assert not svc.seen_hash("s1", h)
    svc.remember_hash("s1", h)
    assert svc.seen_hash("s1", h)
    assert not svc.seen_hash("s2", h)


def test_buffer_caps():
    svc = ChatService()
    for i in range(60):
        svc.record(FakeEvent(f"msg{i}", sender=f"user{i}"))
    assert len(svc._buffers["p:m:1"]) <= ChatService.MAX_SESSION_HISTORY


def test_social_context_group():
    svc = ChatService()
    svc.record(FakeEvent("hello group", sender="alice"))
    svc.record(FakeEvent("hi alice", sender="bob"))
    ctx = svc.social_context(FakeEvent("hi alice"))
    assert "alice" in ctx and "bob" in ctx


def test_social_context_private_empty():
    svc = ChatService()
    svc.record(FakeEvent("hi", sender="alice", private=True))
    assert svc.social_context(FakeEvent("hi", private=True)) == ""


def test_social_context_empty_buffer():
    assert ChatService().social_context(FakeEvent("x")) == ""
