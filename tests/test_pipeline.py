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

    def get_sender_id(self):
        return f"id-{self._sender}"

    def get_group_id(self):
        return "g1"

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


@pytest.mark.asyncio
async def test_summary_sees_current_message_when_adaptive_learning_off(tmp_path):
    from unified_chat.storage.database import close_engine, get_engine, reset_engine_for_tests

    calls = []

    class Memory:
        async def maybe_store(self, event, sender_id):
            pass

        async def maybe_summarize(self, event):
            from unified_chat.storage.repo import MessageSessionRepo

            rows = await MessageSessionRepo.list_recent_by_session(
                event.unified_msg_origin, 10
            )
            calls.append([row.content for row in rows])

    reset_engine_for_tests()
    await get_engine(tmp_path / "pipeline.db")
    try:
        cfg = PluginConfig(
            enable_adaptive_learning=False,
            enable_persistent_memory=True,
        )
        pipe = MessagePipeline(cfg, ChatService(), Memory(), None)
        await pipe._after_stages(FakeEvent("current message in summary window"))
        assert calls == [["current message in summary window"]]
    finally:
        await close_engine()
        reset_engine_for_tests()


@pytest.mark.asyncio
async def test_log_done_survives_cancelled_task():
    svc = ChatService()
    pipe = MessagePipeline(PluginConfig(), svc)
    task = asyncio.create_task(asyncio.sleep(10), name="doomed")
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    # must not raise CancelledError out of the done-callback
    pipe._log_done(task)
    assert task not in pipe._tasks
