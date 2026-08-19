"""Scheduled background tasks."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta
from typing import Any


class MemoryCleanupCron:
    """Daily 03:00 cleanup of low-importance expired memories."""

    def __init__(self, memory_service: Any):
        self.memory_service = memory_service
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="unified_chat_cron")

    def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
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
            return await self.memory_service.delete_expired_memories()
        except Exception:
            self._log_error("tick")
            return 0

    @staticmethod
    def _log_error(msg: str) -> None:
        with contextlib.suppress(Exception):
            from astrbot.api import logger  # type: ignore

            logger.error(f"[unified_chat] cron {msg}", exc_info=True)
