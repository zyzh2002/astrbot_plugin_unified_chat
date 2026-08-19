"""Native acceleration facade with Python fallback."""

try:
    from unified_chat._native import chunk_text, score_importance  # type: ignore
except Exception:  # pragma: no cover
    from unified_chat.native.fallback import chunk_text, score_importance  # noqa: F401

__all__ = ["chunk_text", "score_importance"]
