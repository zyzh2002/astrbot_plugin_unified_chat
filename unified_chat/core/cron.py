"""Scheduled background tasks."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any


async def purge_retention(config: Any) -> tuple[int, int]:
    """Delete messages/learning logs past their retention window (0 = keep)."""
    from ..storage import repo as repos

    now = datetime.now(UTC)
    msg_days = int(getattr(config, "message_retention_days", 0) or 0)
    log_days = int(getattr(config, "learning_log_retention_days", 0) or 0)
    removed_msgs = 0
    removed_logs = 0
    if msg_days > 0:
        removed_msgs = await repos.MessageRepo.delete_older_than(
            now - timedelta(days=msg_days)
        )
    if log_days > 0:
        removed_logs = await repos.LearningLogRepo.delete_older_than(
            now - timedelta(days=log_days)
        )
    return removed_msgs, removed_logs


class MemoryCleanupCron:
    """Daily 03:00 cleanup of low-importance expired memories."""

    def __init__(
        self,
        memory_service: Any,
        backup_service: Any | None = None,
        learning_jobs: Any | None = None,
        config: Any | None = None,
        sweep_targets: list[Any] | None = None,
    ):
        self.memory_service = memory_service
        self.backup_service = backup_service
        self.learning_jobs = learning_jobs
        self.config = config
        self.sweep_targets = sweep_targets or []
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="unified_chat_cron")

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    @staticmethod
    def _seconds_until_next_03(now: datetime) -> float:
        if now.hour < 3:
            nxt = now.replace(hour=3, minute=0, second=0, microsecond=0)
        else:
            nxt = (now + timedelta(days=1)).replace(hour=3, minute=0, second=0, microsecond=0)
        return (nxt - now).total_seconds()

    async def _run(self) -> None:
        try:
            while True:
                delay = self._seconds_until_next_03(datetime.now())
                await asyncio.sleep(delay)
                await self._tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._log_error("cron loop")

    async def _tick(self) -> int:
        try:
            removed = await self.memory_service.delete_expired_memories()
        except Exception:
            self._log_error("tick")
            removed = 0
        if self.config is not None:
            try:
                await purge_retention(self.config)
            except Exception:
                self._log_error("retention purge")
        try:
            from ..storage.repo import MemoryFts

            await MemoryFts.reconcile()
        except Exception:
            self._log_error("fts reconcile")
        for target in self.sweep_targets:
            sweep = getattr(target, "sweep", None)
            if sweep is None:
                continue
            try:
                await sweep() if asyncio.iscoroutinefunction(sweep) else sweep()
            except Exception:
                self._log_error("state sweep")
        if self.backup_service is not None:
            try:
                await self.backup_service.daily_tick()
            except Exception:
                self._log_error("backup tick")
        if self.learning_jobs is not None:
            try:
                await self.learning_jobs.run()
            except Exception:
                self._log_error("learning tick")
        return removed

    @staticmethod
    def _log_error(msg: str) -> None:
        with contextlib.suppress(Exception):
            from astrbot.api import logger  # type: ignore

            logger.error(f"[unified_chat] cron {msg}", exc_info=True)
