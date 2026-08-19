"""Learning service: filter -> refine -> reinforce pipeline."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from ..storage import repo as repos
from ..storage.models import LearningLog, Memory, MessageRecord
from ..utils.hashing import dedup_hash
from .chat_service import ChatService

REFINE_SYSTEM_PROMPT = (
    "Distill the following chat message into ONE concise, durable fact or "
    "preference statement about the user, in the message's language. "
    "Reply with exactly one line. If nothing durable can be extracted, "
    "reply with an empty line."
)


class LearningService:
    """Adaptive learning domain (per-plugin-instance, no globals)."""

    MIN_LEARN_CHARS = 8

    def __init__(self, context: Any, config: Any):
        self.context = context
        self.config = config
        self._semaphore = asyncio.Semaphore(2)

    def should_learn(self, event: Any) -> bool:
        if not self.config.enable_adaptive_learning:
            return False
        text = getattr(event, "message_str", "")
        if ChatService.is_command(text):
            return False
        return len(text.strip()) >= self.MIN_LEARN_CHARS

    async def refine(self, text: str) -> str:
        if not self.config.chat_provider_id:
            return ""
        llm_generate = getattr(self.context, "llm_generate", None)
        if llm_generate is None:
            return ""
        async with self._semaphore:
            try:
                resp = await llm_generate(
                    chat_provider_id=self.config.chat_provider_id,
                    prompt=text,
                    system_prompt=REFINE_SYSTEM_PROMPT,
                )
                return (getattr(resp, "completion_text", "") or "").strip()
            except Exception:
                self._log_error("refine")
                return ""

    async def maybe_learn(self, event: Any, sender_id: str) -> None:
        try:
            if not self.should_learn(event):
                return
            text = event.message_str
            h = dedup_hash(text)
            if await repos.MessageRepo.exists_hash(h):
                return
            group_id = ""
            with contextlib.suppress(Exception):
                group_id = str(event.get_group_id() or "")
            await repos.MessageRepo.add(
                MessageRecord(
                    umo=event.unified_msg_origin,
                    sender_id=sender_id,
                    group_id=group_id,
                    content=text,
                    dedup_hash=h,
                )
            )
            await repos.LearningLogRepo.add(
                LearningLog(stage="filter", input_text=text, output_text="", provider_id="")
            )
            if not self.config.chat_provider_id:
                return
            refined = await self.refine(text)
            if not refined:
                return
            await repos.LearningLogRepo.add(
                LearningLog(
                    stage="refine",
                    input_text=text,
                    output_text=refined,
                    provider_id=self.config.chat_provider_id,
                )
            )
            if len(refined) < self.MIN_LEARN_CHARS:
                return
            rh = dedup_hash(refined)
            if await repos.MemoryRepo.exists_hash(rh):
                return
            await repos.MemoryRepo.add(
                Memory(content=refined, importance=0.5, source="learning", dedup_hash=rh)
            )
            await repos.LearningLogRepo.add(
                LearningLog(stage="reinforce", input_text=refined, output_text="", provider_id="")
            )
        except Exception:
            self._log_error("maybe_learn")

    @staticmethod
    def _log_error(msg: str) -> None:
        with contextlib.suppress(Exception):
            from astrbot.api import logger  # type: ignore

            logger.error(f"[unified_chat] learning {msg}", exc_info=True)
