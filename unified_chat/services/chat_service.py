"""Chat service: command filter, dedup window, per-session social buffer."""

from __future__ import annotations

import contextlib
from collections import deque
from typing import Any

from ..native import chunk_text
from ..utils.hashing import dedup_hash


class ChatService:
    """Per-plugin-instance conversational state (no global singletons)."""

    MAX_SESSION_HISTORY = 50
    MAX_CONTEXT_CHARS = 4000
    DEDUP_WINDOW = 20
    SNIPPET_LEN = 120

    def __init__(self):
        self._buffers: dict[str, deque[tuple[str, str]]] = {}
        self._seen: dict[str, deque[str]] = {}
        self._last_activity: dict[str, float] = {}

    def _touch(self, session: str, now: float | None = None) -> None:
        import time as _time

        self._last_activity[session] = (
            _time.time() if now is None else float(now)
        )

    def sweep(self, now: float | None = None) -> int:
        """Evict sessions idle beyond 2h from buffers and dedup windows."""
        import time as _time

        now = _time.time() if now is None else float(now)
        horizon = 2 * 3600.0
        stale = [
            session
            for session, last in self._last_activity.items()
            if now - last > horizon
        ]
        for session in stale:
            self._buffers.pop(session, None)
            self._seen.pop(session, None)
            self._last_activity.pop(session, None)
        return len(stale)

    @staticmethod
    def is_command(text: str) -> bool:
        t = text.strip()
        return not t or t.startswith("/")

    def should_process(self, event: Any) -> bool:
        return not self.is_command(getattr(event, "message_str", ""))

    @staticmethod
    def hash_of(text: str) -> str:
        return dedup_hash(text)

    def seen_hash(self, session: str, h: str) -> bool:
        return h in self._seen.get(session, ())

    def remember_hash(self, session: str, h: str) -> None:
        q = self._seen.setdefault(session, deque(maxlen=self.DEDUP_WINDOW))
        q.append(h)
        self._touch(session)

    def record(self, event: Any) -> None:
        session = event.unified_msg_origin
        text = getattr(event, "message_str", "")
        sender = ""
        with contextlib.suppress(Exception):
            sender = event.get_sender_name() or ""
        snippet = text[: self.SNIPPET_LEN]
        with contextlib.suppress(Exception):
            chunks = chunk_text(text, self.SNIPPET_LEN, 0)
            if chunks:
                snippet = chunks[0]
        buf = self._buffers.setdefault(session, deque(maxlen=self.MAX_SESSION_HISTORY))
        buf.append((sender, snippet))
        self._touch(session)

    def social_context(self, event: Any) -> str:
        if event.is_private_chat():
            return ""
        buf = self._buffers.get(event.unified_msg_origin)
        if not buf:
            return ""
        senders: list[str] = []
        for sender, _ in buf:
            if sender and sender not in senders:
                senders.append(sender)
        senders = senders[-10:]
        lines = [f"Recently active: {', '.join(senders)}"]
        for sender, snippet in list(buf)[-5:]:
            who = sender or "?"
            lines.append(f"{who}: {snippet}")
        return "\n".join(lines)[: self.MAX_CONTEXT_CHARS]
