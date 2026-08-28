"""LLM-based memory summarization: conversation window -> memory atoms."""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from ..storage import repo as repos
from ..storage.models import Memory
from .chat_service import ChatService
from .memory_classifier import classify_memory
from .memory_ttls import ttl_for

SUMMARIZE_SYSTEM_PROMPT = (
    "Extract durable facts, preferences, plans or relationships from the "
    "following conversation. Reply with ONLY a JSON array of items like "
    '[{"content": "...", "type": "FACTUAL"}]. Allowed types: EPISODIC, '
    "FACTUAL, RELATIONAL, PREFERENCE, PLANNED. No prose outside the array."
)

_VALID_TYPES = {"EPISODIC", "FACTUAL", "RELATIONAL", "PREFERENCE", "PLANNED"}
_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def parse_summary_items(raw: str) -> list[tuple[str, str]]:
    """Defensively parse LLM output into (content, type) pairs."""
    if not raw:
        return []
    match = _ARRAY_RE.search(raw)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except Exception:
        return []
    items: list[tuple[str, str]] = []
    if not isinstance(data, list):
        return []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        content = str(entry.get("content", "")).strip()
        if len(content) < 4:
            continue
        mtype = str(entry.get("type", "")).strip().upper()
        if mtype not in _VALID_TYPES:
            mtype = classify_memory(content)
        items.append((content, mtype))
    return items[:20]


class MemorySummarizer:
    """Batch-summarizes captured messages into memory atoms (fail-silent)."""

    def __init__(
        self,
        context: Any,
        config: Any,
        store_atom: Callable[..., Awaitable[tuple[Memory, bool]]] | None = None,
    ):
        self.context = context
        self.config = config
        self._store_atom = store_atom
        self._counters: dict[str, int] = {}
        self._counter_ts: dict[str, float] = {}

    def observe(self, umo: str) -> bool:
        """Count a captured message; True when a summary run is due."""
        import time as _time

        batch = int(getattr(self.config, "summary_batch_size", 10) or 0)
        if batch <= 0:
            return False
        count = self._counters.get(umo, 0) + 1
        self._counters[umo] = count
        self._counter_ts[umo] = _time.monotonic()
        return count % batch == 0

    def sweep(self, now: float | None = None) -> int:
        """Drop counters for sessions idle beyond 24h."""
        import time as _time

        now = _time.monotonic() if now is None else float(now)
        horizon = 24 * 3600.0
        stale = [
            umo
            for umo, ts in self._counter_ts.items()
            if now - ts > horizon
        ]
        for umo in stale:
            self._counters.pop(umo, None)
            self._counter_ts.pop(umo, None)
        return len(stale)

    async def maybe_summarize(self, umo: str) -> int:
        """Run one summarize cycle for the session; returns atoms stored."""
        try:
            if not self.observe(umo):
                return 0
            return await self.summarize_session(umo)
        except Exception:
            self._log_error("maybe_summarize")
            return 0

    async def summarize_session(self, umo: str) -> int:
        if not getattr(self.config, "enable_persistent_memory", True):
            return 0
        batch = int(getattr(self.config, "summary_batch_size", 10) or 0)
        if batch <= 0:
            return 0
        llm_generate = getattr(self.context, "llm_generate", None)
        if llm_generate is None:
            return 0
        rows = await repos.MessageSessionRepo.list_recent_by_session(umo, batch)
        if len(rows) < max(2, min(batch, 3)):
            return 0
        window = "\n".join(f"- {r.content}" for r in rows if r.content.strip())
        raw = ""
        with contextlib.suppress(Exception):
            resp = await llm_generate(
                chat_provider_id=self.config.chat_provider_id,
                prompt=window,
                system_prompt=SUMMARIZE_SYSTEM_PROMPT,
            )
            raw = (getattr(resp, "completion_text", "") or "").strip()
        stored = 0
        for content, mtype in parse_summary_items(raw):
            with contextlib.suppress(Exception):
                if self._store_atom is not None:
                    _memory, created = await self._store_atom(
                        content,
                        source="summary",
                        importance=0.6,
                        session_id=umo,
                        mtype=mtype,
                    )
                    stored += int(created)
                else:
                    dedup = ChatService.hash_of(content)
                    if await repos.MemoryRepo.exists_hash(dedup):
                        continue
                    from datetime import UTC, datetime, timedelta

                    await repos.MemoryRepo.add(
                        Memory(
                            content=content,
                            importance=0.6,
                            source="summary",
                            dedup_hash=dedup,
                            memory_type=mtype,
                            session_id=umo,
                            expires_at=datetime.now(UTC) + timedelta(days=ttl_for(mtype)),
                        )
                    )
                    stored += 1
        return stored

    @staticmethod
    def _log_error(msg: str) -> None:
        with contextlib.suppress(Exception):
            from astrbot.api import logger  # type: ignore

            logger.error(f"[unified_chat] summarizer {msg}", exc_info=True)
