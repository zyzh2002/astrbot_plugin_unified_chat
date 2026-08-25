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


class TestSummarizer:
    async def _service(self, raw: str):
        import tempfile
        from pathlib import Path

        from unified_chat.services.memory_summarizer import MemorySummarizer
        from unified_chat.storage.database import close_engine, reset_engine_for_tests

        class Resp:
            completion_text = raw

        class Ctx:
            async def llm_generate(self, **kwargs):
                self.kwargs = kwargs
                return Resp()

        class Cfg:
            summary_batch_size = 3
            chat_provider_id = "prov"

        reset_engine_for_tests()

        from unified_chat.storage.database import get_engine as _ge

        d = tempfile.mkdtemp()
        self._tmpdir = d
        await _ge(Path(d) / "sum.db")

        async def cleanup():
            await close_engine()
            reset_engine_for_tests()

        self._cleanup = cleanup
        return MemorySummarizer(Ctx(), Cfg())

    async def test_observe_triggers_every_nth(self):
        service = await self._service("[]")
        assert service.observe("s") is False
        assert service.observe("s") is False
        assert service.observe("s") is True
        await self._cleanup()

    async def test_parse_garbage_returns_empty(self):
        from unified_chat.services.memory_summarizer import parse_summary_items

        assert parse_summary_items("") == []
        assert parse_summary_items("no json here at all") == []
        assert parse_summary_items('{"not": "a list"}') == []
        assert parse_summary_items('[{"content": ""}]') == []

    async def test_parse_valid_and_type_fallback(self):
        from unified_chat.services.memory_summarizer import parse_summary_items

        items = parse_summary_items(
            'sure! here you go: [{"content": "likes blue color", "type": "PREFERENCE"},'
            ' {"content": "has a sister named Ann"}] thanks'
        )
        assert items == [("likes blue color", "PREFERENCE"), ("has a sister named Ann", "FACTUAL")]

    async def test_summarize_session_stores_atoms(self):
        from unified_chat.storage.models import MessageRecord
        from unified_chat.storage.repo import MemoryRepo, MessageRepo

        service = await self._service(
            '[{"content": "user prefers dark mode", "type": "PREFERENCE"}]'
        )
        umo = "sess-sum:1"
        for i in range(3):
            await MessageRepo.add(
                MessageRecord(
                    umo=umo, sender_id="u", content=f"msg {i} about settings",
                    dedup_hash=f"h{i}",
                )
            )
        stored = await service.summarize_session(umo)
        assert stored == 1
        rows = await MemoryRepo.list_all()
        assert rows[0].memory_type == "PREFERENCE"
        assert rows[0].source == "summary"
        await self._cleanup()

    async def test_too_few_messages_skips_llm(self):
        from unified_chat.storage.models import MessageRecord
        from unified_chat.storage.repo import MessageRepo

        service = await self._service('[{"content": "x fact here", "type": "FACTUAL"}]')
        await MessageRepo.add(
            MessageRecord(umo="s2", sender_id="u", content="only one msg here", dedup_hash="h")
        )
        assert await service.summarize_session("s2") == 0
        await self._cleanup()


class TestBackupService:
    def _make_db(self, root):
        import sqlite3
        from pathlib import Path

        db = Path(root) / "unified_chat.db"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE t (x TEXT)")
        con.execute("INSERT INTO t VALUES ('v')")
        con.commit()
        con.close()
        return db

    def _service(self, db, keep_last=2, monkeypatch=None):
        from unified_chat.services.backup_service import BackupService

        class Cfg:
            backup_keep_last = keep_last

        return BackupService(Cfg(), db)

    def test_run_backup_creates_snapshot(self, tmp_path):
        db = self._make_db(tmp_path)
        service = self._service(db)
        dest = service.run_backup("manual")
        assert dest is not None and (dest / "unified_chat.db").exists()

    def test_retention_prunes_oldest(self, tmp_path):
        import time

        db = self._make_db(tmp_path)
        service = self._service(db)
        for _ in range(4):
            service.run_backup("daily")
            time.sleep(1.1)  # ensure distinct second-resolution stamps
        assert len(service.list_backups()) == 2

    async def test_version_backup_once(self, tmp_path, monkeypatch):
        import tempfile
        from pathlib import Path

        from unified_chat.services.backup_service import BackupService
        from unified_chat.storage.database import close_engine, get_engine, reset_engine_for_tests

        reset_engine_for_tests()
        d = Path(tempfile.mkdtemp())
        await get_engine(d / "unified_chat.db")
        try:
            class Cfg:
                backup_keep_last = 5

            service = BackupService(Cfg(), d / "unified_chat.db")
            first = await service.maybe_backup_version("0.1.0")
            second = await service.maybe_backup_version("0.1.0")
            third = await service.maybe_backup_version("0.2.0")
            assert first is True and second is False and third is True
        finally:
            await close_engine()
            reset_engine_for_tests()


class TestSequentialStoreRegression:
    async def test_two_stores_no_naive_datetime_crash(self):
        import tempfile
        from pathlib import Path

        from unified_chat.storage.database import close_engine, get_engine, reset_engine_for_tests

        reset_engine_for_tests()
        with tempfile.TemporaryDirectory() as d:
            await get_engine(Path(d) / "seq.db")
            try:
                service = _make_service()

                class Ev:
                    def __init__(self, t):
                        self.message_str = t
                        self.unified_msg_origin = "sess-seq"

                await service.maybe_store(
                    Ev("first message that is definitely long enough here"), "u1"
                )
                await service.maybe_store(
                    Ev("second different message also long enough ok"), "u1"
                )
                from unified_chat.storage.repo import MemoryRepo

                assert await MemoryRepo.count() == 2
            finally:
                await close_engine()
                reset_engine_for_tests()
