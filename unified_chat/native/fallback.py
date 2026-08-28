"""Pure Python fallback for native acceleration."""


def chunk_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Fallback chunking with same semantics as Rust impl."""
    if chunk_overlap < 0:
        # the Rust signature takes usize; pyo3 rejects negatives, so match it
        raise ValueError("chunk_overlap must be >= 0")
    if chunk_size <= 0:
        return []
    if not text:
        return []
    overlap = min(chunk_overlap, chunk_size - 1 if chunk_size > 1 else 0)
    step = chunk_size - overlap
    chars = list(text)
    out: list[str] = []
    start = 0
    while start < len(chars):
        end = min(start + chunk_size, len(chars))
        s = "".join(chars[start:end])
        if s.strip():
            out.append(s)
        if end == len(chars):
            break
        start += step
    return out


def score_importance(char_len: int, recency_hours: float, freq: int) -> float:
    """Fallback importance scoring."""
    import math

    len_score = min(char_len / 512.0, 1.0) * 0.3
    recency = math.exp(-recency_hours / 72.0) * 0.4
    freq_score = min(freq / 10.0, 1.0) * 0.3
    return max(0.0, min(1.0, len_score + recency + freq_score))


def hash_dedup(text: str) -> str:
    """FNV-1a 64-bit hash, lowercase hex (bit-identical to Rust impl)."""
    h = 0xCBF29CE484222325
    for b in text.encode("utf-8"):
        h ^= b
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"
