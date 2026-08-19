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


@pytest.mark.asyncio
async def test_exists_hash():
    reset_engine_for_tests()
    with tempfile.TemporaryDirectory() as d:
        await get_engine(Path(d) / "r4.db")
        assert not await MessageRepo.exists_hash("h1")
        await MessageRepo.add(
            MessageRecord(umo="u", sender_id="s", content="c", dedup_hash="h1")
        )
        assert await MessageRepo.exists_hash("h1")
        assert not await MemoryRepo.exists_hash("h1")
        await MemoryRepo.add(Memory(content="c", dedup_hash="h2"))
        assert await MemoryRepo.exists_hash("h2")
        await close_engine()


@pytest.mark.asyncio
async def test_count_helpers():
    reset_engine_for_tests()
    with tempfile.TemporaryDirectory() as d:
        await get_engine(Path(d) / "r5.db")
        assert await MemoryRepo.count() == 0
        await MemoryRepo.add(Memory(content="a", dedup_hash="h1"))
        await MemoryRepo.add(Memory(content="b", dedup_hash="h2"))
        assert await MemoryRepo.count() == 2
        assert await LearningLogRepo.count_by_stage("filter") == 0
        await LearningLogRepo.add(LearningLog(stage="filter", input_text="x"))
        await LearningLogRepo.add(LearningLog(stage="filter", input_text="y"))
        await LearningLogRepo.add(LearningLog(stage="refine", input_text="z"))
        assert await LearningLogRepo.count_by_stage("filter") == 2
        assert await LearningLogRepo.count_by_stage("refine") == 1
        assert await LearningLogRepo.count_by_stage("reinforce") == 0
        await close_engine()


@pytest.mark.asyncio
async def test_clear_kb_doc_ids():
    reset_engine_for_tests()
    with tempfile.TemporaryDirectory() as d:
        await get_engine(Path(d) / "r6.db")
        m1 = await MemoryRepo.add(Memory(content="a", dedup_hash="h1", kb_doc_id="d1"))
        await MemoryRepo.add(Memory(content="b", dedup_hash="h2", kb_doc_id="d2"))
        await MemoryRepo.add(Memory(content="c", dedup_hash="h3"))
        assert await MemoryRepo.clear_kb_doc_ids() == 2
        assert m1.kb_doc_id is not None  # instance copy unaffected
        all_mems = await MemoryRepo.list_all()
        assert all(m.kb_doc_id is None for m in all_mems)
        await close_engine()
