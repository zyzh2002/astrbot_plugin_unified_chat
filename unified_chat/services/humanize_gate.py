"""Air-reading reply gate: probability engine + bypass rules (pure logic)."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any

from .humanize_state import AttentionTracker, FatigueTracker, SessionGateState


@dataclass
class GateDecision:
    reply: bool
    reason: str


def _now() -> float:
    return time.monotonic()


class ReplyGate:
    """First-layer probabilistic gate for group messages."""

    def __init__(self, config: Any, rng: random.Random | None = None):
        self.config = config
        self.rng = rng or random.Random()
        self._states: dict[str, SessionGateState] = {}
        self.attention = AttentionTracker()
        self.fatigue = FatigueTracker()

    def _state(self, session: str) -> SessionGateState:
        if session not in self._states:
            self._states[session] = SessionGateState()
        return self._states[session]

    @staticmethod
    def _text_of(event: Any) -> str:
        return getattr(event, "message_str", "") or ""

    @staticmethod
    def _sender_of(event: Any) -> str:
        try:
            return str(event.get_sender_id() or "")
        except Exception:
            return ""

    @staticmethod
    def is_group(event: Any) -> bool:
        try:
            return bool(getattr(event, "get_group_id", lambda: None)())
        except Exception:
            return False

    def _is_command(self, text: str) -> bool:
        from .chat_service import ChatService

        return ChatService.is_command(text)

    def _matches_any(self, text: str, words: list[str]) -> bool:
        lowered = text.lower()
        return any(str(w).strip() and str(w).lower() in lowered for w in words)

    def decide(self, event: Any, now: float | None = None) -> GateDecision:
        cfg = self.config
        text = self._text_of(event)
        now = _now() if now is None else now

        if not getattr(cfg, "humanize_enable", False):
            return GateDecision(True, "disabled")
        sender = self._sender_of(event)
        blacklist = list(getattr(cfg, "blacklist_users", []) or [])
        if sender and sender in {str(u) for u in blacklist}:
            return GateDecision(False, "blacklisted")
        if self._is_command(text):
            return GateDecision(True, "command")
        blocked = list(getattr(cfg, "blocked_keywords", []) or [])
        if self._matches_any(text, blocked):
            return GateDecision(False, "blocked_keyword")
        if not self.is_group(event):
            return GateDecision(True, "private")
        triggers = list(getattr(cfg, "trigger_keywords", []) or [])
        if self._matches_any(text, triggers):
            return GateDecision(True, "trigger_keyword")
        if getattr(event, "is_wake", False):
            return GateDecision(True, "wake")

        session = getattr(event, "unified_msg_origin", "") or "unknown"
        state = self._state(session)
        self.attention.bump(state, sender or "anon", now)

        probability = self._probability(state, now)
        allowed = self.rng.random() < probability
        if not allowed:
            state.last_message_ts = now
            return GateDecision(False, "probability")
        self.fatigue.on_reply(state)
        state.last_reply_ts = now
        return GateDecision(True, "probability")

    def _probability(self, state: SessionGateState, now: float) -> float:
        cfg = self.config
        base = float(getattr(cfg, "humanize_base_probability", 0.15))
        window = float(getattr(cfg, "humanize_boost_window_seconds", 120))
        p = base
        if state.last_reply_ts and (now - state.last_reply_ts) <= window:
            p += float(getattr(cfg, "humanize_after_reply_probability", 0.8))
        if getattr(cfg, "humanize_attention_enabled", True):
            top_attention = max(
                (self.attention.decayed(state, user, now) for user in state.attention),
                default=0.0,
            )
            p += top_attention * float(
                getattr(cfg, "humanize_attention_boost_max", 0.3)
            )
        p -= min(
            self.fatigue.penalty(state, now, window),
            float(getattr(cfg, "humanize_fatigue_penalty_max", 0.35)),
        )
        return max(0.0, min(1.0, p))
