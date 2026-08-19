"""Hashing helpers backed by the native facade."""

from __future__ import annotations

from unified_chat.native import hash_dedup as _native_hash


def dedup_hash(text: str) -> str:
    """FNV-1a 64-bit hex hash for message dedup (native or fallback)."""
    return _native_hash(text)
