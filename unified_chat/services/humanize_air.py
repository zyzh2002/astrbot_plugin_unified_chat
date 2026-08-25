"""LLM air-reading second layer: should the bot reply? (fail-open)"""

from __future__ import annotations

import asyncio
from typing import Any

SYSTEM_PROMPT = (
    "You decide whether a chat bot should reply to the newest group message. "
    "Consider atmosphere: messages not directed at the bot, pure chatter "
    "between others, or rhetorical noise mean NO. Questions, mentions, or "
    "continuations of the bot's own thread mean YES. "
    "Answer strictly with YES or NO."
)


def parse_answer(raw: str) -> bool:
    """True means reply; anything unparsable counts as YES."""
    text = (raw or "").strip().upper()
    return not text.startswith("NO")


class AirReader:
    """Second-layer LLM decision with timeout and fail-open semantics."""

    def __init__(self, context: Any, config: Any, timeout_s: float = 8.0):
        self.context = context
        self.config = config
        self.timeout = timeout_s

    def _provider_id(self) -> str:
        return (
            getattr(self.config, "humanize_air_reading_provider_id", "")
            or getattr(self.config, "chat_provider_id", "")
        )

    async def should_reply(self, recent_lines: list[str], latest: str) -> bool:
        provider_id = self._provider_id()
        llm_generate = getattr(self.context, "llm_generate", None)
        if not provider_id or llm_generate is None:
            return True
        window = "\n".join(recent_lines[-10:])
        prompt = f"Recent messages:\n{window}\n\nNewest message:\n{latest}"
        try:
            resp = await asyncio.wait_for(
                llm_generate(
                    chat_provider_id=provider_id,
                    prompt=prompt,
                    system_prompt=SYSTEM_PROMPT,
                ),
                timeout=self.timeout,
            )
        except Exception:
            return True
        raw = (getattr(resp, "completion_text", "") or "").strip()
        return parse_answer(raw)
