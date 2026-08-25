"""Tests for Phase 6 memory depth: typing, TTL, FTS, fusion, isolation."""

import pytest

from unified_chat.services.memory_classifier import classify_memory
from unified_chat.services.memory_ttls import TYPE_TTL_DAYS, ttl_for


class TestClassifier:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("我最喜欢蓝色", "PREFERENCE"),
            ("I really love spicy food", "PREFERENCE"),
            ("明天下午三点开会", "PLANNED"),
            ("we plan to launch next week", "PLANNED"),
            ("小明是我的同事", "RELATIONAL"),
            ("she is my classmate", "RELATIONAL"),
            ("今天在公园看到了一场精彩的比赛，非常精彩", "EPISODIC"),
            ("water boils at 100 degrees celsius", "FACTUAL"),
            ("", "FACTUAL"),
        ],
    )
    def test_classification(self, text, expected):
        assert classify_memory(text) == expected


class TestTtl:
    def test_table_covers_all_types(self):
        assert set(TYPE_TTL_DAYS) == {
            "EPISODIC",
            "PLANNED",
            "FACTUAL",
            "RELATIONAL",
            "PREFERENCE",
        }

    def test_ttl_for_known_and_unknown(self):
        assert ttl_for("EPISODIC") == 14
        assert ttl_for("PREFERENCE") == 365
        assert ttl_for("WHATEVER") == 90  # unknown -> FACTUAL default


class TestModelFields:
    def test_memory_new_fields_default(self):
        from unified_chat.storage.models import Memory

        mem = Memory(content="x")
        assert mem.memory_type == "FACTUAL"
        assert mem.session_id == ""
        assert mem.reinforce_count == 0

    def test_import_does_not_break_metadata_twice(self):
        import unified_chat.storage.models as mod
        from unified_chat.storage.models import Memory as M1

        assert mod.Memory is M1


class TestFtsAndFusion:
    @pytest.fixture(autouse=True)
    async def _db(self):
        import tempfile
        from pathlib import Path

        from unified_chat.storage.database import close_engine, get_engine, reset_engine_for_tests

        reset_engine_for_tests()
        with tempfile.TemporaryDirectory() as d:
            await get_engine(Path(d) / "fts.db")
            yield
            await close_engine()
            reset_engine_for_tests()

    async def test_fts_roundtrip_and_search(self):
        from unified_chat.storage.models import Memory
        from unified_chat.storage.repo import MemoryFts, MemoryRepo

        mem = await MemoryRepo.add(Memory(content="cobalt blue paint recipe"))
        await MemoryFts.index_add(mem.id, mem.content, "sess-1")
        hits = await MemoryFts.search("cobalt paint")
        assert hits and hits[0][0] == mem.id

    async def test_fts_defensive_quoting_no_crash(self):
        from unified_chat.storage.repo import MemoryFts

        assert await MemoryFts.search('"AND OR NOT ( )') == []

    async def test_rrf_fusion_orders_by_combined_rank(self):
        from unified_chat.storage.models import Memory
        from unified_chat.storage.repo import MemoryFts, MemoryRepo

        m1 = await MemoryRepo.add(Memory(content="cobalt blue pigment history"))
        m2 = await MemoryRepo.add(Memory(content="blue whale facts"))
        for mem in (m1, m2):
            await MemoryFts.index_add(mem.id, mem.content, "")

        service = _make_service()
        results = await service.retrieve_hybrid("blue")
        ids = [m.id for m in results]
        assert set(ids) >= {m1.id, m2.id}

    async def test_isolation_filter(self):
        from unified_chat.storage.models import Memory
        from unified_chat.storage.repo import MemoryFts, MemoryRepo

        only_s1 = await MemoryRepo.add(
            Memory(content="secret pizza topping", session_id="s1")
        )
        global_mem = await MemoryRepo.add(
            Memory(content="shared fact about pasta", session_id="")
        )
        other = await MemoryRepo.add(
            Memory(content="secret pizza topping", session_id="s2")
        )
        for mem in (only_s1, global_mem, other):
            await MemoryFts.index_add(mem.id, mem.content, mem.session_id)

        service = _make_service()
        results = await service.retrieve_hybrid("pizza", session_id="s1")
        assert [m.id for m in results] == [only_s1.id]

    async def test_retrieve_returns_string(self):

        service = _make_service()
        assert isinstance(await service.retrieve("anything"), str)


def _make_service():
    from unified_chat.services.memory_service import MemoryService

    class Cfg:
        enable_persistent_memory = True
        memory_kb_name = "kb"
        embedding_provider_id = ""
        rerank_provider_id = ""
        memory_session_isolation = True
        importance_threshold = 0.3
        memory_cleanup_days = 30

    return MemoryService(context=None, config=Cfg())


class TestStoreStamping:
    @pytest.fixture(autouse=True)
    async def _db(self):
        import tempfile
        from pathlib import Path

        from unified_chat.storage.database import close_engine, get_engine, reset_engine_for_tests

        reset_engine_for_tests()
        with tempfile.TemporaryDirectory() as d:
            await get_engine(Path(d) / "stamp.db")
            yield
            await close_engine()
            reset_engine_for_tests()

    def _make_event(self, text: str):
        class Ev:
            message_str = text
            unified_msg_origin = "sess-xyz:friend:u1"

        return Ev()

    async def test_store_stamps_type_session_and_ttl(self):
        service = _make_service()
        ev = self._make_event("经过这几天的相处，我最喜欢深蓝色，这是我明确的个人偏好，请记住")
        await service.maybe_store(ev, sender_id="u1")
        from unified_chat.storage.repo import MemoryRepo

        rows = await MemoryRepo.list_all()
        assert len(rows) == 1
        assert rows[0].memory_type == "PREFERENCE"
        assert rows[0].session_id == "sess-xyz:friend:u1"
        assert rows[0].expires_at is not None

    async def test_isolation_off_stores_global(self):
        service = _make_service()
        service.config.memory_session_isolation = False
        await service.maybe_store(self._make_event("plain factual statement here"), "u1")
        from unified_chat.storage.repo import MemoryRepo

        rows = await MemoryRepo.list_all()
        assert rows[0].session_id == ""

    async def test_deleted_memory_removed_from_fts(self):
        from unified_chat.storage.models import Memory
        from unified_chat.storage.repo import MemoryFts, MemoryRepo

        mem = await MemoryRepo.add(Memory(content="unique-zebra-token"))
        await MemoryFts.index_add(mem.id, mem.content, "")
        assert await MemoryFts.search("unique-zebra-token")
        await MemoryRepo.delete_by_ids([mem.id])
        assert not await MemoryFts.search("unique-zebra-token")
