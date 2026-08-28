"""Humanize service: orchestrates gate + air layer + unreplied cache."""

from __future__ import annotations

import asyncio
import contextlib
import random
from dataclasses import dataclass
from typing import Any

from .chat_service import ChatService
from .humanize_air import AirReader
from .humanize_gate import GateDecision, ReplyGate
from .unreplied_cache import UnrepliedCache

# Reasons whose allowed decision actually produces a group reply and may
# therefore consume the unreplied chatter context.
_REPLY_REASONS = ("trigger_keyword", "wake", "probability")


def matches_any(text: str, words: list[str]) -> bool:
    lowered = (text or "").lower()
    return any(str(w).strip() and str(w).lower() in lowered for w in words)


def _cfg_get(config: Any, key: str, default: Any) -> Any:
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _text_of(event: Any) -> str:
    return getattr(event, "message_str", "") or ""


def is_blacklisted(event: Any, config: Any) -> bool:
    """True when the sender is on the configured blacklist."""
    sender = ""
    with contextlib.suppress(Exception):
        sender = str(event.get_sender_id() or "")
    blacklist = {str(u) for u in (_cfg_get(config, "blacklist_users", None) or [])}
    return bool(sender) and sender in blacklist


def blocked_keyword_hit(event: Any, config: Any) -> bool:
    """True when the text hits a blocked keyword.

    Commands are exempt, matching the reply gate's ordering (a command like
    ``/umem search <word>`` must reach its handler).
    """
    text = _text_of(event)
    if ChatService.is_command(text):
        return False
    return matches_any(text, list(_cfg_get(config, "blocked_keywords", None) or []))


@dataclass
class HumanizeOutcome:
    allow: bool
    reason: str
    merged_context: str = ""


class HumanizeService:
    """Single entry point used by the message pipeline."""

    def __init__(self, context: Any, config: Any, rng: random.Random | None = None):
        self.config = config
        self.gate = ReplyGate(config, rng=rng)
        self.cache = UnrepliedCache()
        self.air = AirReader(context, config)
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, session: str) -> asyncio.Lock:
        lock = self._locks.get(session)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session] = lock
        return lock

    @staticmethod
    def _session_of(event: Any) -> str:
        return getattr(event, "unified_msg_origin", "") or "unknown"

    def _recent_lines(self, session: str) -> list[str]:
        return [f"{sender}: {text}" for sender, text, _ts in self.cache.peek(session)]

    async def process(self, event: Any) -> HumanizeOutcome:
        """Run gate (+air layer); returns outcome for pipeline handling.

        Serialized per session: decide/air/commit must be atomic against
        concurrent messages or the gate reads stale state and double-replies.
        """
        session = self._session_of(event)
        async with self._lock(session):
            return await self._process_locked(event, session)

    async def _process_locked(self, event: Any, session: str) -> HumanizeOutcome:
        decision: GateDecision = self.gate.decide(event)
        with contextlib.suppress(Exception):
            sender = str(event.get_sender_id() or "")
        text = _text_of(event)

        produces_reply = decision.reply and (
            decision.reason != "probability"
            or not getattr(self.config, "humanize_air_reading_llm", False)
        )
        needs_air = decision.reply and not produces_reply
        if decision.reply:
            if needs_air and not await self.air.should_reply(
                self._recent_lines(session), text
            ):
                self.cache.append(session, sender or "anon", text)
                return HumanizeOutcome(False, "air_no")
            if decision.reason in _REPLY_REASONS:
                entries = self.cache.drain(session)
                merged = self.cache.merge_block(entries) if entries else ""
            else:
                merged = ""
            if decision.reason == "probability":
                self.gate.commit_reply(event)
            return HumanizeOutcome(True, decision.reason, merged)

        # denied paths that still record chatter
        if decision.reason in ("probability",):
            self.cache.append(session, sender or "anon", text)
        return HumanizeOutcome(False, decision.reason)

    def blocked_keyword_hit(self, event: Any) -> bool:
        return blocked_keyword_hit(event, self.config)
