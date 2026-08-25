"""Composes the learning-context system block under a hard char budget."""

from __future__ import annotations

from typing import Any

MAX_BLOCK_CHARS = 800


async def compose_learning_block(
    event: Any,
    config: Any,
    slang_terms: list[Any],
    affinity_score: float | None,
    mood_scalar: float,
) -> str:
    """Build the combined slang/tone/mood block; "" when nothing applies."""
    if not getattr(config, "enable_style_learning", True):
        return ""
    parts: list[str] = []

    text = (getattr(event, "message_str", "") or "").lower()
    hits = [
        t
        for t in slang_terms
        if getattr(t, "meaning", "") and str(t.term).lower() in text
    ][:8]
    if hits:
        lines = "\n".join(f"- {t.term}: {t.meaning}" for t in hits)
        parts.append(f"Group slang you should understand:\n{lines}")

    if affinity_score is not None and getattr(config, "enable_affinity", True):
        band = "warm" if affinity_score > 70 else ("cool" if affinity_score < 30 else "neutral")
        tone = {
            "warm": "The sender is a close friend; be extra warm.",
            "cool": "Relations with the sender are strained; stay polite but reserved.",
            "neutral": "",
        }[band]
        if tone:
            parts.append(tone)

    if getattr(config, "enable_mood", True):
        label = _mood_label(mood_scalar)
        if label:
            parts.append(f"Your current mood feels {label}.")

    if not parts:
        return ""
    block = "\n".join(parts)
    if len(block) > MAX_BLOCK_CHARS:
        block = block[: MAX_BLOCK_CHARS - 1] + "…"
    return block


def _mood_label(scalar: float) -> str:
    if scalar > 0.5:
        return "excited"
    if scalar > 0.1:
        return "happy"
    if scalar >= -0.1:
        return "calm"
    if scalar > -0.5:
        return "down"
    return "grumpy"
