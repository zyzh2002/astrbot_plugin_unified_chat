"""Unreplied-message cache: keeps group chatter context until next reply."""

from __future__ import annotations

import time
from collections import deque


class UnrepliedCache:
    """Per-session ring buffer of messages that did not trigger a reply."""

    def __init__(self, maxlen: int = 20, ttl_seconds: float = 1800.0):
        self.ttl = ttl_seconds
        self._data: dict[str, deque] = {}
        self._maxlen = maxlen

    def append(self, session: str, sender: str, text: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        bucket = self._data.setdefault(session, deque(maxlen=self._maxlen))
        bucket.append((sender, text, now))

    def _pruned(self, session: str, now: float) -> deque:
        bucket = self._data.get(session)
        if bucket is None:
            return deque()
        while bucket and (now - bucket[0][2]) > self.ttl:
            bucket.popleft()
        return bucket

    def drain(self, session: str, now: float | None = None) -> list[tuple[str, str, float]]:
        """Return cached entries and clear them."""
        now = time.time() if now is None else now
        bucket = self._pruned(session, now)
        items = list(bucket)
        self._data[session] = deque(maxlen=self._maxlen)
        return items

    def peek(self, session: str, now: float | None = None) -> list[tuple[str, str, float]]:
        now = time.time() if now is None else now
        return list(self._pruned(session, now))

    def merge_block(self, entries: list[tuple[str, str, float]]) -> str:
        lines = [f"- {sender}: {text}" for sender, text, _ts in entries]
        return "Recent group chatter without reply:\n" + "\n".join(lines)

    def sweep(self, now: float | None = None) -> int:
        """Evict sessions whose newest entry is older than the TTL."""
        now = time.time() if now is None else now
        stale = [
            session
            for session, bucket in self._data.items()
            if not bucket or (now - bucket[-1][2]) > self.ttl
        ]
        for session in stale:
            del self._data[session]
        return len(stale)
