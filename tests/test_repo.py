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
        await MessageRepo.add(MessageRecord(umo="u", sender_id="s", content="c", dedup_hash="h1"))
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


def test_utc_wall_format():
    from unified_chat.storage.repo import _utc_wall

    assert _utc_wall(datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=UTC)) == (
        "2026-01-02 03:04:05.000006"
    )
    # naive values are already UTC wall clock (SQLite round-trip)
    assert _utc_wall(datetime(2026, 1, 2, 3, 4, 5, 6)) == "2026-01-02 03:04:05.000006"


@pytest.mark.asyncio
async def test_distinct_umos_epoch_is_utc_and_ordered():
    reset_engine_for_tests()
    with tempfile.TemporaryDirectory() as d:
        await get_engine(Path(d) / "tz1.db")
        from unified_chat.storage import repo as repo_mod

        base = datetime.now(UTC) - timedelta(hours=1)
        for umo, minutes in (("a", 0), ("b", 30), ("c", 60)):
            await MessageRepo.add(
                MessageRecord(
                    umo=umo,
                    sender_id="s",
                    content="x",
                    dedup_hash=f"tz-{umo}",
                    group_id="g",
                    created_at=base + timedelta(minutes=minutes),
                )
            )
        rows = await repo_mod.MessageScanRepo.distinct_umos()
        assert [r[0] for r in rows] == ["c", "b", "a"]  # most recent first
        expected = (base + timedelta(minutes=60)).timestamp()
        assert abs(rows[0][1] - expected) < 5  # no local-offset skew
        g_rows = await repo_mod.MessageScanRepo.distinct_group_umos()
        assert [r[0] for r in g_rows] == ["a", "b", "c"]  # quietest first
        await close_engine()


@pytest.mark.asyncio
async def test_expiry_filters_use_utc_now(monkeypatch):
    reset_engine_for_tests()
    with tempfile.TemporaryDirectory() as d:
        await get_engine(Path(d) / "tz2.db")
        from unified_chat.storage import repo as repo_mod

        fake_now = datetime.now(UTC)
        monkeypatch.setattr(repo_mod, "_utcnow", lambda: fake_now)
        live = await MemoryRepo.add(
            Memory(
                content="future",
                dedup_hash="tz-f1",
                session_id="",
                expires_at=fake_now + timedelta(hours=1),
            )
        )
        dead = await MemoryRepo.add(
            Memory(
                content="past",
                dedup_hash="tz-p1",
                session_id="",
                expires_at=fake_now - timedelta(hours=1),
            )
        )
        got = await repo_mod.MemoryLookupRepo.get_visible_by_id(
            live.id, session_id="", isolation=True
        )
        assert got is not None and got.content == "future"
        got2 = await repo_mod.MemoryLookupRepo.get_visible_by_id(
            dead.id, session_id="", isolation=True
        )
        assert got2 is None
        by_hash = await repo_mod.MemoryLookupRepo.get_by_hash(
            "tz-f1", session_id="", isolation=True
        )
        assert by_hash is not None
        await close_engine()


@pytest.mark.asyncio
async def test_busy_timeout_pragma():
    reset_engine_for_tests()
    with tempfile.TemporaryDirectory() as d:
        await get_engine(Path(d) / "tz3.db")
        from sqlalchemy import text

        from unified_chat.storage.database import get_session

        async with get_session() as session:
            result = await session.exec(text("PRAGMA busy_timeout"))
            assert int(result.scalar_one()) == 5000
        await close_engine()
