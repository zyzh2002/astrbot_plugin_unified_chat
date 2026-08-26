"""Storage models for unified_chat (SQLModel)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MessageRecord(SQLModel, table=True):
    """Raw captured messages for learning."""

    __tablename__ = "messages"

    id: int | None = Field(default=None, primary_key=True)
    umo: str = Field(index=True, max_length=255)
    sender_id: str = Field(index=True, max_length=255)
    group_id: str = Field(default="", max_length=255)
    content: str = Field(default="")
    dedup_hash: str = Field(default="", index=True, max_length=64)
    created_at: datetime = Field(default_factory=_utcnow, index=True)


class Memory(SQLModel, table=True):
    """Persistent memory entries."""

    __tablename__ = "memories"

    id: int | None = Field(default=None, primary_key=True)
    content: str = Field(default="")
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    source: str = Field(default="auto", max_length=64)
    dedup_hash: str = Field(default="", index=True, max_length=64)
    kb_doc_id: str | None = Field(default=None, index=True, max_length=64)
    access_count: int = Field(default=0)
    memory_type: str = Field(default="FACTUAL", max_length=16, index=True)
    session_id: str = Field(default="", max_length=255, index=True)
    reinforce_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=_utcnow, index=True)
    last_accessed_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime | None = Field(default=None, index=True)


class LearningLog(SQLModel, table=True):
    """Learning pipeline logs for audit."""

    __tablename__ = "learning_logs"

    id: int | None = Field(default=None, primary_key=True)
    stage: str = Field(max_length=32)  # filter/refine/reinforce
    input_text: str = Field(default="")
    output_text: str = Field(default="")
    provider_id: str = Field(default="", max_length=255)
    created_at: datetime = Field(default_factory=_utcnow, index=True)


class UnifiedKV(SQLModel, table=True):
    """Generic key-value for migration flags and small state."""

    __tablename__ = "unified_kv"

    key: str = Field(primary_key=True, max_length=255)
    value: str = Field(default="")
    updated_at: datetime = Field(default_factory=_utcnow)


class SlangTerm(SQLModel, table=True):
    """Group slang candidates and confirmed meanings."""

    __tablename__ = "slang_terms"

    id: int | None = Field(default=None, primary_key=True)
    term: str = Field(index=True, max_length=64)
    meaning: str = Field(default="", max_length=512)
    umo: str = Field(index=True, default="", max_length=255)
    status: str = Field(index=True, default="candidate", max_length=16)
    count: int = Field(default=0)
    created_at: datetime = Field(default_factory=_utcnow)


class UserAffinity(SQLModel, table=True):
    """Per-session-user affection score in [0, 100]."""

    __tablename__ = "user_affinity"
    __table_args__ = (UniqueConstraint("umo", "user_id", name="uq_affinity_scope_user"),)

    id: int | None = Field(default=None, primary_key=True)
    umo: str = Field(index=True, max_length=255)
    user_id: str = Field(index=True, max_length=64)
    score: float = Field(default=50.0)
    updated_at: datetime = Field(default_factory=_utcnow, index=True)
