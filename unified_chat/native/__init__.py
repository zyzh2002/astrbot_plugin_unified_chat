"""Native acceleration facade with Python fallback."""

try:
    from unified_chat._native import chunk_text, hash_dedup, score_importance  # type: ignore
except Exception:  # pragma: no cover
    from unified_chat.native.fallback import (  # noqa: F401
        chunk_text,
        hash_dedup,
        score_importance,
    )

__all__ = ["chunk_text", "score_importance", "hash_dedup"]
