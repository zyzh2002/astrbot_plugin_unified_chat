"""Per-type memory TTL policy (days). Unknown types fall back to FACTUAL."""

from __future__ import annotations

TYPE_TTL_DAYS: dict[str, int] = {
    "EPISODIC": 14,
    "PLANNED": 30,
    "FACTUAL": 90,
    "RELATIONAL": 180,
    "PREFERENCE": 365,
}

DEFAULT_TYPE = "FACTUAL"


def ttl_for(memory_type: str) -> int:
    return TYPE_TTL_DAYS.get(memory_type, TYPE_TTL_DAYS[DEFAULT_TYPE])
