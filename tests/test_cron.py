"""Tests for MemoryCleanupCron."""

import asyncio
from datetime import datetime

import pytest

from unified_chat.core.cron import MemoryCleanupCron


class FakeMemoryService:
    def __init__(self):
        self.ticks = 0

    async def delete_expired_memories(self):
        self.ticks += 1
        return 3


def test_seconds_until_next_03():
    cron = MemoryCleanupCron(FakeMemoryService())
    assert abs(cron._seconds_until_next_03(datetime(2026, 8, 20, 4, 0, 0)) - 23 * 3600) < 1
    assert abs(cron._seconds_until_next_03(datetime(2026, 8, 20, 1, 0, 0)) - 2 * 3600) < 1
    assert abs(cron._seconds_until_next_03(datetime(2026, 8, 20, 3, 0, 0)) - 24 * 3600) < 1


@pytest.mark.asyncio
async def test_tick_delegates():
    svc = FakeMemoryService()
    cron = MemoryCleanupCron(svc)
    assert await cron._tick() == 3
    assert svc.ticks == 1


@pytest.mark.asyncio
async def test_tick_survives_failure():
    class BoomService:
        async def delete_expired_memories(self):
            raise RuntimeError("boom")

    cron = MemoryCleanupCron(BoomService())
    assert await cron._tick() == 0


@pytest.mark.asyncio
async def test_stop_idempotent_and_cancels():
    svc = FakeMemoryService()
    cron = MemoryCleanupCron(svc)
    cron.start()
    await asyncio.sleep(0.01)
    assert cron._task is not None
    cron.stop()
    cron.stop()
    assert cron._task is None
