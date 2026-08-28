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
    """Build the combined slang/tone/mood block; "" when nothing applies.

    Tone and mood lines are emitted first and never dropped; slang lines are
    added one by one while the block still fits the budget, so a flood of
    long meanings cannot truncate the mood signal away.
    """
    if not getattr(config, "enable_style_learning", True):
        return ""
    parts: list[str] = []
    budget = MAX_BLOCK_CHARS

    if affinity_score is not None and getattr(config, "enable_affinity", True):
        band = "warm" if affinity_score > 70 else ("cool" if affinity_score < 30 else "neutral")
        tone = {
            "warm": "The sender is a close friend; be extra warm.",
            "cool": "Relations with the sender are strained; stay polite but reserved.",
            "neutral": "",
        }[band]
        if tone:
            parts.append(tone)
            budget -= len(tone) + 1

    if getattr(config, "enable_mood", True):
        label = _mood_label(mood_scalar)
        if label:
            mood_line = f"Your current mood feels {label}."
            parts.append(mood_line)
            budget -= len(mood_line) + 1

    text = (getattr(event, "message_str", "") or "").lower()
    hits = [
        t
        for t in slang_terms
        if getattr(t, "meaning", "") and str(t.term).lower() in text
    ][:8]
    if hits:
        header = "Group slang you should understand:"
        lines: list[str] = [header]
        used = len(header)
        for t in hits:
            line = f'- {t.term}: "{t.meaning}"'
            if used + 1 + len(line) > budget:
                break
            lines.append(line)
            used += 1 + len(line)
        if len(lines) > 1:
            parts.insert(0, "\n".join(lines))

    return "\n".join(parts)


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
