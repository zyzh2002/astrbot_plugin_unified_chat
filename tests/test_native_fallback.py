"""Native fallback tests (no Rust required)."""

from unified_chat.native.fallback import chunk_text, hash_dedup, score_importance


def test_chunk_text_basic():
    text = "abcdefghij"
    chunks = chunk_text(text, 4, 1)
    assert chunks[0] == "abcd"
    assert chunks[1] == "defg"
    assert len(chunks) >= 2


def test_chunk_text_empty():
    assert chunk_text("", 5, 1) == []
    assert chunk_text("hi", 0, 0) == []


def test_score_importance_range():
    s = score_importance(100, 1.0, 1)
    assert 0.0 <= s <= 1.0


def test_hash_dedup_deterministic():
    assert hash_dedup("hello") == hash_dedup("hello")
    assert len(hash_dedup("hello")) == 16
    assert hash_dedup("") == hash_dedup("")


def test_hash_dedup_known_vector():
    assert hash_dedup("hello") == "a430d84680aabd0b"  # FNV-1a 64
    assert hash_dedup("") == "cbf29ce484222325"
