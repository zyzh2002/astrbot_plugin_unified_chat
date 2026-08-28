"""Unified message pipeline (non-blocking)."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any


class MessagePipeline:
    """Filter -> dedup -> record -> background stages.

    Stages after record run in a background task so message handling
    never blocks on memory/learning work (specs 004/005).
    """

    def __init__(
        self,
        config: Any,
        chat_service: Any,
        memory_service: Any = None,
        learning_service: Any = None,
    ):
        self.config = config
        self.chat_service = chat_service
        self.memory_service = memory_service
        self.learning_service = learning_service
        self._tasks: set[asyncio.Task] = set()

    async def process(self, event: Any) -> None:
        if not (
            self.config.enable_conversation_enhance
            or self.config.enable_persistent_memory
            or self.config.enable_adaptive_learning
        ):
            return
        try:
            if not self.chat_service.should_process(event):
                return
            session = event.unified_msg_origin
            h = self.chat_service.hash_of(event.message_str)
            if self.chat_service.seen_hash(session, h):
                return
            self.chat_service.remember_hash(session, h)
            if self.config.enable_conversation_enhance:
                self.chat_service.record(event)
            self._spawn_background(event)
        except Exception:
            self._log_error("process")

    def _spawn_background(self, event: Any) -> None:
        task = asyncio.create_task(self._after_stages(event), name="unified_chat_pipeline")
        self._tasks.add(task)
        task.add_done_callback(self._log_done)

    async def _after_stages(self, event: Any) -> None:
        sender_id = self._sender_of(event)
        await self._capture_message(event, sender_id)
        if self.memory_service is not None:
            await self.memory_service.maybe_store(event, sender_id)
        if self.learning_service is not None:
            await self.learning_service.maybe_learn(event, sender_id)
        if self.memory_service is not None and self.config.enable_persistent_memory:
            await self.memory_service.maybe_summarize(event)
        if getattr(self.config, "enable_affinity", True):
            try:
                from ..storage import repo as repos

                umo = getattr(event, "unified_msg_origin", "") or ""
                if umo:
                    await repos.AffinityRepo.bump(umo, sender_id or "anon")
            except Exception:
                pass

    async def _capture_message(self, event: Any, sender_id: str) -> None:
        from ..storage import repo as repos
        from ..storage.models import MessageRecord
        from ..utils.hashing import dedup_hash

        text = getattr(event, "message_str", "") or ""
        h = dedup_hash(text)
        if not text or await repos.MessageRepo.exists_hash(h):
            return
        group_id = ""
        with contextlib.suppress(Exception):
            group_id = str(event.get_group_id() or "")
        await repos.MessageRepo.add(
            MessageRecord(
                umo=getattr(event, "unified_msg_origin", "") or "",
                sender_id=sender_id,
                group_id=group_id,
                content=text,
                dedup_hash=h,
            )
        )

    @staticmethod
    def _sender_of(event: Any) -> str:
        with contextlib.suppress(Exception):
            return event.get_sender_id() or ""
        with contextlib.suppress(Exception):
            return event.get_sender_name() or ""
        return ""

    def _log_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        with contextlib.suppress(Exception):
            exc = task.exception()
            if exc is not None:
                self._log_error(f"background: {exc}")

    async def shutdown(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    @staticmethod
    def _log_error(msg: str) -> None:
        with contextlib.suppress(Exception):
            from astrbot.api import logger  # type: ignore

            logger.error(f"[unified_chat] pipeline {msg}", exc_info=True)
