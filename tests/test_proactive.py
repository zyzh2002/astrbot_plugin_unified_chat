"""Tests for proactive group-chat scheduling."""

from unittest.mock import AsyncMock, patch

import pytest

from unified_chat.config import PluginConfig
from unified_chat.services.humanize_proactive import ProactiveService


@pytest.mark.asyncio
async def test_proactive_tick_uses_group_scan_repo():
    config = PluginConfig(
        humanize_proactive=True,
        humanize_proactive_min_silence_minutes=1,
        chat_provider_id="p",
    )
    service = ProactiveService(object(), config)
    service.rng.random = lambda: 0.0
    service._send_opener = AsyncMock(return_value=True)
    service._recently_sent = AsyncMock(return_value=False)
    with patch(
        "unified_chat.storage.repo.MessageScanRepo.distinct_group_umos",
        AsyncMock(return_value=[("group:GroupMessage:g1", 0.0)]),
    ):
        assert await service.tick() == 1
        service._send_opener.assert_awaited_once_with("group:GroupMessage:g1")


@pytest.mark.asyncio
async def test_proactive_duplicate_is_persisted_for_24_hours(tmp_path):
    from unified_chat.storage.database import close_engine, get_engine, reset_engine_for_tests

    reset_engine_for_tests()
    await get_engine(tmp_path / "proactive.db")
    try:
        service = ProactiveService(object(), PluginConfig())
        await service._remember_sent("group:GroupMessage:g1", "same opener")
        restarted = ProactiveService(object(), PluginConfig())
        assert await restarted._recently_sent("group:GroupMessage:g1", 3600)
        assert await restarted._recent_duplicate(
            "group:GroupMessage:g1", "same opener"
        )
        assert not await restarted._recent_duplicate(
            "group:GroupMessage:g1", "different opener"
        )
    finally:
        await close_engine()
        reset_engine_for_tests()
