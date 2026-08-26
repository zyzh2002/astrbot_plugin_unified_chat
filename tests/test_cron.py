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
async def test_tick_runs_backup_and_learning_jobs():
    backup_calls = []
    job_calls = []

    class Backup:
        async def daily_tick(self):
            backup_calls.append(True)

    class Jobs:
        async def run(self):
            job_calls.append(True)

    cron = MemoryCleanupCron(
        FakeMemoryService(),
        backup_service=Backup(),
        learning_jobs=Jobs(),
    )
    assert await cron._tick() == 3
    assert backup_calls == [True]
    assert job_calls == [True]


@pytest.mark.asyncio
async def test_stop_idempotent_and_cancels():
    svc = FakeMemoryService()
    cron = MemoryCleanupCron(svc)
    cron.start()
    await asyncio.sleep(0.01)
    assert cron._task is not None
    await cron.stop()
    await cron.stop()
    assert cron._task is None
