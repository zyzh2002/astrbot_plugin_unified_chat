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
        task.add_done_callback(self._log_done)

    async def _after_stages(self, event: Any) -> None:
        sender_id = self._sender_of(event)
        if self.memory_service is not None:
            await self.memory_service.maybe_store(event, sender_id)
            await self.memory_service.maybe_summarize(event)
        if getattr(self.config, "enable_affinity", True):
            try:
                from ..storage import repo as repos

                umo = getattr(event, "unified_msg_origin", "") or ""
                if umo:
                    await repos.AffinityRepo.bump(umo, sender_id or "anon")
            except Exception:
                pass
        if self.learning_service is not None:
            await self.learning_service.maybe_learn(event, sender_id)

    @staticmethod
    def _sender_of(event: Any) -> str:
        with contextlib.suppress(Exception):
            return event.get_sender_name() or ""
        return ""

    def _log_done(self, task: asyncio.Task) -> None:
        with contextlib.suppress(Exception):
            exc = task.exception()
            if exc is not None:
                self._log_error(f"background: {exc}")

    @staticmethod
    def _log_error(msg: str) -> None:
        with contextlib.suppress(Exception):
            from astrbot.api import logger  # type: ignore

            logger.error(f"[unified_chat] pipeline {msg}", exc_info=True)
