"""Humanize service: orchestrates gate + air layer + unreplied cache."""

from __future__ import annotations

import contextlib
import random
from dataclasses import dataclass
from typing import Any

from .humanize_air import AirReader
from .humanize_gate import GateDecision, ReplyGate
from .unreplied_cache import UnrepliedCache


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

    @staticmethod
    def _session_of(event: Any) -> str:
        return getattr(event, "unified_msg_origin", "") or "unknown"

    def _recent_lines(self, session: str) -> list[str]:
        return [f"{sender}: {text}" for sender, text, _ts in self.cache.peek(session)]

    async def process(self, event: Any) -> HumanizeOutcome:
        """Run gate (+air layer); returns outcome for pipeline handling."""
        decision: GateDecision = self.gate.decide(event)
        session = self._session_of(event)
        with contextlib.suppress(Exception):
            sender = str(event.get_sender_id() or "")
        text = getattr(event, "message_str", "") or ""

        needs_air = decision.reply and decision.reason == "probability" and getattr(
            self.config, "humanize_air_reading_llm", False
        )
        if decision.reply:
            if needs_air and not await self.air.should_reply(
                self._recent_lines(session), text
            ):
                self.cache.append(session, sender or "anon", text)
                return HumanizeOutcome(False, "air_no")
            entries = self.cache.drain(session)
            merged = self.cache.merge_block(entries) if entries else ""
            return HumanizeOutcome(True, decision.reason, merged)

        # denied paths that still record chatter
        if decision.reason in ("probability",):
            self.cache.append(session, sender or "anon", text)
        return HumanizeOutcome(False, decision.reason)

    def blocked_keyword_hit(self, event: Any) -> bool:
        words = list(getattr(self.config, "blocked_keywords", []) or [])
        lowered = (getattr(event, "message_str", "") or "").lower()
        return any(str(w).strip() and str(w).lower() in lowered for w in words)
