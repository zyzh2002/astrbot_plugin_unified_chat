"""Rule-based memory type classification (zero LLM calls)."""

from __future__ import annotations

import re

_PREFERENCE = re.compile(
    r"喜欢|讨厌|偏好|最爱|不喜欢|偏爱|prefer|favorite|\b(?:like|love|hate)s?\b",
    re.IGNORECASE,
)
_PLANNED = re.compile(
    r"明天|下周|下个月|打算|计划|约定|待办|tomorrow|next\s+(?:week|month)|plan\s+to|will\s",
    re.IGNORECASE,
)
_RELATIONAL = re.compile(
    r"是我的|我的朋友|我的同事|我的同学|我的室友|我的家人|"
    r"is\s+my\s+\w+|my\s+(?:friend|colleague|classmate|roommate|family)",
    re.IGNORECASE,
)
_EPISODIC = re.compile(
    r"今天|昨天|刚才|刚刚|早上|昨晚|today|yesterday|just\s+now|this\s+morning",
    re.IGNORECASE,
)


def classify_memory(text: str) -> str:
    """Classify a memory atom; first match wins in priority order."""
    if not text:
        return "FACTUAL"
    for pattern, kind in (
        (_PREFERENCE, "PREFERENCE"),
        (_PLANNED, "PLANNED"),
        (_RELATIONAL, "RELATIONAL"),
        (_EPISODIC, "EPISODIC"),
    ):
        if pattern.search(text):
            return kind
    return "FACTUAL"
