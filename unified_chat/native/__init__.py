"""Native acceleration facade with Python fallback."""

try:
    from .._native import chunk_text, hash_dedup, score_importance  # type: ignore
except Exception:  # pragma: no cover
    try:
        from .bootstrap import default_cache_dir, try_load_cached

        try_load_cached(default_cache_dir())
        from .._native import chunk_text, hash_dedup, score_importance  # type: ignore
    except Exception:  # pragma: no cover
        from .fallback import (  # noqa: F401
            chunk_text,
            hash_dedup,
            score_importance,
        )

__all__ = ["chunk_text", "score_importance", "hash_dedup"]
