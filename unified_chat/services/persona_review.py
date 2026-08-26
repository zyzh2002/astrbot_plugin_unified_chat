"""Persona tweak suggestions with a manual review chain (KV-backed)."""

from __future__ import annotations

import contextlib
import time
import uuid
from typing import Any

from ..storage import kv as kv_store

PENDING_KEY = "persona_pending"
MAX_PENDING = 20


def _parse(raw: str | None) -> list[dict]:
    if not raw:
        return []
    import json

    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


async def _load() -> list[dict]:
    try:
        return _parse(await kv_store.kv_get(PENDING_KEY))
    except Exception:
        return []


async def _save(items: list[dict]) -> None:
    import json

    await kv_store.kv_set(PENDING_KEY, json.dumps(items, ensure_ascii=False))


class PersonaReviewService:
    """Suggests persona tweaks; humans approve/reject via commands."""

    def __init__(self, context: Any, config: Any, memory_service: Any | None = None):
        self.context = context
        self.config = config
        self.memory_service = memory_service

    async def maybe_suggest(self, session_id: str = "") -> str | None:
        """Generate one suggestion from recent memories; returns its id."""
        llm_generate = getattr(self.context, "llm_generate", None)
        provider_id = getattr(self.config, "chat_provider_id", "")
        if llm_generate is None or not provider_id:
            return None
        hints = await self._memory_hints(session_id)
        if not hints:
            return None
        try:
            resp = await llm_generate(
                chat_provider_id=provider_id,
                prompt=(
                    "Recent durable memories about users:\n"
                    f"{hints}\n\nPropose ONE short persona adjustment "
                    "(max 3 sentences) reflecting these insights."
                ),
                system_prompt="You refine chat-bot personas based on observed facts.",
            )
            text = (getattr(resp, "completion_text", "") or "").strip()
        except Exception:
            return None
        if not text or len(text) > 1200:
            return None
        items = await _load()
        entry = {
            "id": uuid.uuid4().hex[:8],
            "text": text,
            "created_at": time.strftime("%Y-%m-%d %H:%M"),
            "session_id": session_id,
        }
        items.append(entry)
        await _save(items[-MAX_PENDING:])
        return entry["id"]

    async def _memory_hints(self, session_id: str) -> str:
        try:
            if self.memory_service is not None:
                hits = await self.memory_service.retrieve_hybrid(
                    "用户 偏好 事实",
                    session_id=session_id,
                    top_k=10,
                )
                return "\n".join(f"- {m.content[:80]}" for m in hits)
        except Exception:
            pass
        return ""

    @staticmethod
    async def list_pending() -> list[dict]:
        return await _load()

    @staticmethod
    async def resolve(entry_id: str, approve: bool) -> tuple[bool, str]:
        items = await _load()
        kept = []
        resolved = None
        for item in items:
            if item.get("id") == entry_id and resolved is None:
                resolved = item
            else:
                kept.append(item)
        if resolved is None:
            return False, ""
        await _save(kept)
        if not approve:
            return True, ""
        return True, str(resolved.get("text", ""))


def _log_error(msg: str) -> None:
    with contextlib.suppress(Exception):
        from astrbot.api import logger  # type: ignore

        logger.error(f"[unified_chat] persona-review {msg}", exc_info=True)
