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


@pytest.mark.asyncio
async def test_tick_purges_retention(monkeypatch):
    calls = []

    async def fake_purge(config):
        calls.append(config)

    import unified_chat.core.cron as cron_mod

    monkeypatch.setattr(cron_mod, "purge_retention", fake_purge)
    cron = cron_mod.MemoryCleanupCron(FakeMemoryService(), config={"a": 1})
    await cron._tick()
    assert len(calls) == 1

    cron_no_cfg = cron_mod.MemoryCleanupCron(FakeMemoryService())
    await cron_no_cfg._tick()
    assert len(calls) == 1  # no config -> no purge


@pytest.mark.asyncio
async def test_daily_backup_runs_off_loop(tmp_path, monkeypatch):
    import threading
    from pathlib import Path as _Path

    from unified_chat.services.backup_service import BackupService

    seen = {}

    def fake_run_backup(self, reason):
        seen["thread"] = threading.get_ident()
        return _Path(tmp_path) / "b"

    monkeypatch.setattr(BackupService, "run_backup", fake_run_backup)
    svc = BackupService({}, _Path(tmp_path) / "db.sqlite")
    assert await svc.daily_tick() is True
    assert seen["thread"] != threading.get_ident()
