"""Tests for filter decoupling and destructive-command guards (spec 011 R3)."""

from unittest.mock import patch

import pytest

from unified_chat.core.lifecycle import PluginLifecycle
from unified_chat.storage.database import (
    close_engine,
    get_engine,
    reset_engine_for_tests,
)


class FakeContext:
    def __init__(self, cfg=None):
        self._cfg = cfg or {}

    def get_config(self):
        return self._cfg


class FakeEvent:
    def __init__(self, text, umo="g:g:1", sender="u1"):
        self.message_str = text
        self.unified_msg_origin = umo
        self._sender = sender
        self.stopped = False

    def get_sender_id(self):
        return self._sender

    def get_sender_name(self):
        return "alice"

    def is_private_chat(self):
        return False

    def stop_event(self):
        self.stopped = True


@pytest.fixture(autouse=True)
def _fresh_engine(tmp_path):
    reset_engine_for_tests()
    yield tmp_path
    reset_engine_for_tests()


async def _load(tmp_path, cfg):
    with patch(
        "unified_chat.utils.path.resolve_data_dir", lambda raw, ctx: tmp_path / "data"
    ):
        lc = PluginLifecycle(None, FakeContext(cfg))
        await lc.on_load()
        return lc


@pytest.mark.asyncio
async def test_blacklist_works_without_humanize(tmp_path):
    lc = await _load(tmp_path, {"blacklist_users": ["u1"]})
    ev = FakeEvent("hello friends")
    await lc.handle_message(ev)
    assert ev.stopped is True
    assert lc._chat_service._buffers.get("g:g:1") is None  # nothing recorded
    ok = FakeEvent("hello friends", sender="u2")
    await lc.handle_message(ok)
    assert ok.stopped is False
    assert len(lc._chat_service._buffers["g:g:1"]) == 1
    await lc.on_unload()


@pytest.mark.asyncio
async def test_blocked_keyword_stops_without_humanize(tmp_path):
    lc = await _load(tmp_path, {"blocked_keywords": ["badword"]})
    ev = FakeEvent("this has badword inside")
    await lc.handle_message(ev)
    assert ev.stopped is True
    assert lc._chat_service._buffers.get("g:g:1") is None
    await lc.on_unload()


@pytest.mark.asyncio
async def test_blocked_keyword_exempt_for_commands(tmp_path):
    lc = await _load(tmp_path, {"blocked_keywords": ["badword"]})
    ev = FakeEvent("/umem search badword")
    await lc.handle_message(ev)
    assert ev.stopped is False  # commands pass the pre-filter like the gate
    await lc.on_unload()


@pytest.mark.asyncio
async def test_umem_reset_blocked_without_isolation(tmp_path):
    lc = await _load(
        tmp_path, {"enable_persistent_memory": True, "memory_session_isolation": False}
    )
    await get_engine(lc._data_dir / "unified_chat.db")
    from unified_chat.storage.models import Memory
    from unified_chat.storage.repo import MemoryRepo

    await MemoryRepo.add(Memory(content="keep me", dedup_hash="k1", session_id=""))
    ev = FakeEvent("/umem reset")
    reply = await lc.umem(ev, "reset")
    assert "isolation" in reply
    assert await MemoryRepo.count() == 1  # nothing wiped
    await lc.on_unload()
    await close_engine()


@pytest.mark.asyncio
async def test_umem_reset_works_with_isolation(tmp_path):
    lc = await _load(
        tmp_path,
        {"enable_persistent_memory": True, "memory_session_isolation": True},
    )
    await get_engine(lc._data_dir / "unified_chat.db")
    from unified_chat.storage.models import Memory
    from unified_chat.storage.repo import MemoryRepo

    await MemoryRepo.add(
        Memory(content="mine", dedup_hash="m1", session_id="g:g:1")
    )
    await MemoryRepo.add(
        Memory(content="other", dedup_hash="m2", session_id="g:g:2")
    )
    reply = await lc.umem(FakeEvent("/umem reset"), "reset")
    assert "cleared 1" in reply
    remaining = await MemoryRepo.list_all()
    assert [m.content for m in remaining] == ["other"]
    await lc.on_unload()


def test_blocked_keyword_helper_command_exempt():
    from unified_chat.services.humanize_service import blocked_keyword_hit

    class Ev:
        message_str = "/umem search badword"

    cfg = {"blocked_keywords": ["badword"]}
    assert blocked_keyword_hit(Ev(), cfg) is False


def test_is_blacklisted_helper():
    from unified_chat.services.humanize_service import is_blacklisted

    class Ev:
        def get_sender_id(self):
            return "u9"

    assert is_blacklisted(Ev(), {"blacklist_users": ["u9"]}) is True
    assert is_blacklisted(Ev(), {"blacklist_users": []}) is False


@pytest.mark.asyncio
async def test_lifecycle_loaded_ok_reflects_startup_result(tmp_path):
    with patch(
        "unified_chat.utils.path.resolve_data_dir",
        lambda raw, ctx: (_ for _ in ()).throw(RuntimeError("no disk")),
    ):
        lc = PluginLifecycle(None, FakeContext())
        await lc.on_load()
        assert lc.loaded_ok is False
    lc_ok = await _load(tmp_path, {})
    try:
        assert lc_ok.loaded_ok is True
    finally:
        await lc_ok.on_unload()
