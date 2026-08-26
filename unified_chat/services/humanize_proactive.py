"""Silence-triggered proactive openers (opt-in, fail-silent)."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import random
import time
from typing import Any

PROMPT_TEMPLATE = (
    "The group has been silent for a while. Write ONE short, natural "
    "conversation opener in the chat's language (max 2 sentences). No quotes, "
    "no explanation. Recent topic hints:\n{hints}"
)


class ProactiveService:
    """Periodically checks silence and may send one opener per session."""

    def __init__(self, context: Any, config: Any, rng: random.Random | None = None):
        self.context = context
        self.config = config
        self.rng = rng or random.Random()
        self._task: asyncio.Task | None = None
        self._last_sent: dict[str, float] = {}

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="unified_chat_proactive")

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(60)
                with contextlib.suppress(Exception):
                    await self.tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # loop must survive anything

    async def tick(self) -> int:
        """One sweep over known sessions; returns number of openers sent."""
        if not getattr(self.config, "humanize_proactive", False):
            return 0
        from ..storage import repo as repos

        threshold_min = int(
            getattr(self.config, "humanize_proactive_min_silence_minutes", 45)
        )
        cutoff = time.time() - threshold_min * 60
        sent = 0
        sessions = await repos.MessageScanRepo.distinct_group_umos(limit=50)
        for umo, last_ts in sessions:
            now = time.time()
            if last_ts > cutoff:
                continue
            if now - self._last_sent.get(umo, 0) < threshold_min * 60:
                continue
            if await self._recently_sent(umo, threshold_min * 60):
                continue
            if self.rng.random() > 0.5:
                continue
            if await self._send_opener(umo):
                self._last_sent[umo] = now
                sent += 1
        return sent

    async def _send_opener(self, umo: str) -> bool:
        llm_generate = getattr(self.context, "llm_generate", None)
        provider_id = getattr(self.config, "chat_provider_id", "")
        if llm_generate is None or not provider_id:
            return False
        try:
            resp = await llm_generate(
                chat_provider_id=provider_id,
                prompt=PROMPT_TEMPLATE.format(hints="- (no recent context)"),
                system_prompt="You write one natural chat opener.",
            )
            text = (getattr(resp, "completion_text", "") or "").strip().strip('"')
            if not text or len(text) > 300:
                return False
            if await self._recent_duplicate(umo, text):
                return False
            from astrbot.api.event import MessageChain  # type: ignore
            from astrbot.api.message_components import Plain  # type: ignore

            await self.context.send_message(umo, MessageChain([Plain(text)]))
            await self._remember_sent(umo, text)
            return True
        except Exception:
            self._log_error("send_opener")
            return False

    @staticmethod
    def _dedup_key(umo: str) -> str:
        return "proactive_last:" + hashlib.sha256(umo.encode()).hexdigest()[:24]

    async def _recent_duplicate(self, umo: str, text: str) -> bool:
        from ..storage import kv as kv_store

        raw = await kv_store.kv_get(self._dedup_key(umo))
        if not raw:
            return False
        try:
            data = json.loads(raw)
            return (
                data.get("hash") == hashlib.sha256(text.encode()).hexdigest()
                and time.time() - float(data.get("ts", 0)) < 86400
            )
        except Exception:
            return False

    async def _recently_sent(self, umo: str, window_seconds: float) -> bool:
        from ..storage import kv as kv_store

        raw = await kv_store.kv_get(self._dedup_key(umo))
        if not raw:
            return False
        try:
            data = json.loads(raw)
            return time.time() - float(data.get("ts", 0)) < window_seconds
        except Exception:
            return False

    async def _remember_sent(self, umo: str, text: str) -> None:
        from ..storage import kv as kv_store

        await kv_store.kv_set(
            self._dedup_key(umo),
            json.dumps(
                {
                    "hash": hashlib.sha256(text.encode()).hexdigest(),
                    "ts": time.time(),
                }
            ),
        )

    @staticmethod
    def _log_error(msg: str) -> None:
        with contextlib.suppress(Exception):
            from astrbot.api import logger  # type: ignore

            logger.error(f"[unified_chat] proactive {msg}", exc_info=True)
