"""Reply gating state: attention tracking, fatigue, boost windows."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SessionGateState:
    """In-memory per-session gating state (no global singletons)."""

    last_reply_ts: float = 0.0
    last_message_ts: float = 0.0
    consecutive_replies: int = 0
    attention: dict[str, tuple[float, float]] = field(default_factory=dict)


class AttentionTracker:
    """Per-user attention scores in [0,1] with exponential time decay."""

    def __init__(self, half_life_seconds: float = 600.0):
        self.half_life = max(1.0, float(half_life_seconds))

    def _decay_factor(self, elapsed: float) -> float:
        return 0.5 ** (elapsed / self.half_life)

    def bump(self, state: SessionGateState, user: str, now: float) -> None:
        current = self.decayed(state, user, now)
        state.attention[user] = (min(1.0, current + 0.25), now)
        state.last_message_ts = now

    def decayed(self, state: SessionGateState, user: str, now: float) -> float:
        value, updated_at = state.attention.get(user, (0.0, now))
        if value <= 0.0:
            return 0.0
        elapsed = max(0.0, now - updated_at)
        return value * self._decay_factor(elapsed)


class FatigueTracker:
    """Consecutive-reply fatigue within the boost window."""

    def __init__(self, penalty_per_reply: float = 0.12):
        self.penalty_per_reply = penalty_per_reply

    def penalty(self, state: SessionGateState, now: float, window: float) -> float:
        if now - state.last_reply_ts > window:
            state.consecutive_replies = 0
        return state.consecutive_replies * self.penalty_per_reply

    def on_reply(self, state: SessionGateState) -> None:
        state.consecutive_replies += 1
