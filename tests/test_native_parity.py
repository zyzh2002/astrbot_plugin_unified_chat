"""Cross-implementation parity: Rust extension vs Python fallback (spec 011 R11).

Skips when the compiled module is unavailable (fallback active in CI without
maturin build); runs the comparison whenever the native binary is importable.
"""

import pytest

from unified_chat.native import chunk_text, fallback, hash_dedup, score_importance

pytestmark = pytest.mark.skipif(
    chunk_text is fallback.chunk_text,
    reason="native extension not built; fallback active",
)


def test_hash_parity():
    vectors = ["", "hello", "统一聊天 unified chat", "x" * 10_000, "\u0000null"]
    for text in vectors:
        assert hash_dedup(text) == fallback.hash_dedup(text)


def test_chunk_parity():
    cases = [
        ("abcdefgh", 3, 1),
        ("abcdefgh", 3, 0),
        ("a", 5, 2),
        ("统一码文本切块测试", 2, 1),
        ("   ", 4, 1),
        ("abc", 10, 3),
        ("abcdefghij", 4, 2),
    ]
    for text, size, overlap in cases:
        assert chunk_text(text, size, overlap) == fallback.chunk_text(
            text, size, overlap
        )


def test_score_parity():
    cases = [(0, 0.0, 0), (512, 72.0, 10), (100, 1.0, 3), (2048, 0.0, 25)]
    for char_len, recency, freq in cases:
        assert score_importance(char_len, recency, freq) == pytest.approx(
            fallback.score_importance(char_len, recency, freq)
        )
