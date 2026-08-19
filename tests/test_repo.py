"""Tests for repo helpers (storage layer)."""

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from unified_chat.storage.database import close_engine, get_engine, reset_engine_for_tests
from unified_chat.storage.models import LearningLog, Memory, MessageRecord
from unified_chat.storage.repo import LearningLogRepo, MemoryRepo, MessageRepo


@pytest.mark.asyncio
async def test_message_repo_add_count():
    reset_engine_for_tests()
    with tempfile.TemporaryDirectory() as d:
        await get_engine(Path(d) / "r.db")
        await MessageRepo.add(MessageRecord(umo="u", sender_id="s", content="hi", dedup_hash="h"))
        assert await MessageRepo.count() == 1
        await close_engine()


@pytest.mark.asyncio
async def test_memory_repo_search_and_expired():
    reset_engine_for_tests()
    with tempfile.TemporaryDirectory() as d:
        await get_engine(Path(d) / "r2.db")
        old = datetime.now(UTC) - timedelta(days=40)
        await MemoryRepo.add(Memory(content="apple pie recipe", importance=0.9, created_at=old))
        await MemoryRepo.add(Memory(content="banana bread", importance=0.1, created_at=old))
        await MemoryRepo.add(Memory(content="carrot cake", importance=0.2, created_at=old))
        hits = await MemoryRepo.search_by_keyword("apple", limit=5)
        assert [m.content for m in hits] == ["apple pie recipe"]
        await MemoryRepo.add(Memory(content="100% sure", importance=0.5))
        assert len(await MemoryRepo.search_by_keyword("100% sure", limit=5)) == 1
        expired = await MemoryRepo.list_expired(0.5, datetime.now(UTC) - timedelta(days=30))
        assert [m.content for m in expired] == ["banana bread", "carrot cake"]
        ids = [m.id for m in expired if m.id is not None]
        assert await MemoryRepo.delete_by_ids(ids) == 2
        updated = await MemoryRepo.update_kb_doc_id(1, "doc1")
        assert updated is not None and updated.kb_doc_id == "doc1"
        assert (await MemoryRepo.update_kb_doc_id(999, "doc")) is None
        await close_engine()


@pytest.mark.asyncio
async def test_learning_log_repo_add():
    reset_engine_for_tests()
    with tempfile.TemporaryDirectory() as d:
        await get_engine(Path(d) / "r3.db")
        log = await LearningLogRepo.add(
            LearningLog(stage="filter", input_text="in", output_text="out")
        )
        assert log.id is not None
        await close_engine()
