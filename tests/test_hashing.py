"""Tests for utils.hashing."""

from unified_chat.native.fallback import hash_dedup as py_hash
from unified_chat.utils.hashing import dedup_hash


def test_dedup_hash_matches_fallback():
    for text in ["hello world", "", "中文测试", "x" * 500]:
        assert dedup_hash(text) == py_hash(text)


def test_dedup_hash_format():
    h = dedup_hash("abc")
    assert len(h) == 16
    assert all(c in "0123456789abcdef" for c in h)
