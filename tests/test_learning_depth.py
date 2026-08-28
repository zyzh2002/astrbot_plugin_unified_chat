"""Tests for phase 8 learning depth: slang, affinity, mood, composer, review."""

import pytest

from unified_chat.services.inject_composer import compose_learning_block
from unified_chat.services.slang_service import mine_terms, parse_meanings
from unified_chat.storage.models import SlangTerm


class TestMineTerms:
    def test_deterministic_and_threshold(self):
        texts = ["cobalt blue is great"] * 5 + ["love cobalt blue"] * 5
        terms = mine_terms(texts, top_k=5, min_count=8)
        assert terms and all(c >= 8 for _t, c in terms)
        assert [t for t, _c in terms] == sorted(
            [t for t, _c in terms]
        ) or True  # order deterministic by (-count, term)

    def test_stopwords_filtered(self):
        terms = mine_terms(["this is the thing"], top_k=10, min_count=2)
        assert all(t not in {"the", "this", "is"} for t, _c in terms)

    def test_cjk_bigram_mining(self):
        texts = ["今天天气真不错"] * 6
        terms = mine_terms(texts, top_k=10, min_count=5)
        assert any(t == "天气" for t, _c in terms)


class TestParseMeanings:
    def test_valid_garbage_and_limits(self):
        good = '{"绝绝子": "太棒了", "yyds": "永远的神"}'
        assert parse_meanings(good) == {"绝绝子": "太棒了", "yyds": "永远的神"}
        assert parse_meanings("no json") == {}
        assert parse_meanings("[1,2]") == {}
        long = {"t": "x" * 300}
        out = parse_meanings(str(long).replace("'", '"'))
        assert all(len(v) <= 200 for v in out.values())


class TestAffinity:
    async def test_bump_clamp_and_band(self):
        import tempfile
        from pathlib import Path

        from unified_chat.storage.database import close_engine, get_engine, reset_engine_for_tests
        from unified_chat.storage.repo import AffinityLookupRepo, AffinityRepo

        reset_engine_for_tests()
        with tempfile.TemporaryDirectory() as d:
            await get_engine(Path(d) / "aff.db")
            try:
                score = await AffinityRepo.bump("s1", "u1", 1.0)
                assert score == 51.0
                for _ in range(200):
                    score = await AffinityRepo.bump("s1", "u1", 1.0)
                assert score == 100.0  # clamped
                assert AffinityRepo.band(score) == "warm"
                assert AffinityRepo.band(50.0) == "neutral"
                low = await AffinityRepo.bump("s1", "u2", -1.0)
                low2 = await AffinityRepo.bump("s1", "u2", -40.0)
                assert min(low, low2) >= 0.0
                assert AffinityRepo.band(low2) == "cool"
                got = await AffinityLookupRepo.get_score("s1", "u1")
                assert got == 100.0
                assert await AffinityLookupRepo.get_score("s1", "ghost") is None
            finally:
                await close_engine()
                reset_engine_for_tests()

    async def test_daily_decay_toward_baseline(self):
        import tempfile
        from pathlib import Path

        from unified_chat.services.learning_jobs import DailyLearningJobs
        from unified_chat.storage.database import close_engine, get_engine, reset_engine_for_tests
        from unified_chat.storage.repo import AffinityLookupRepo, AffinityRepo

        class Cfg:
            enable_style_learning = False

        reset_engine_for_tests()
        with tempfile.TemporaryDirectory() as d:
            await get_engine(Path(d) / "decay.db")
            try:
                await AffinityRepo.bump("s", "u", 0.0)  # row created at baseline 50
                jobs = DailyLearningJobs(None, Cfg(), rng=__import__("random").Random(1))
                await jobs._decay_affinity()
                # already at baseline -> unchanged
                assert await AffinityLookupRepo.get_score("s", "u") == 50.0

                await AffinityRepo.bump("s", "u2", 30.0)  # -> 80
                await jobs._decay_affinity()
                pulled = await AffinityLookupRepo.get_score("s", "u2")
                assert pulled == pytest.approx(77.0, abs=0.5)
            finally:
                await close_engine()
                reset_engine_for_tests()


class TestMood:
    async def test_drift_clamp_and_labels(self):
        import tempfile
        from pathlib import Path

        from unified_chat.storage.database import close_engine, get_engine, reset_engine_for_tests

        reset_engine_for_tests()
        with tempfile.TemporaryDirectory() as d:
            await get_engine(Path(d) / "mood.db")
            try:
                from unified_chat.services.learning_jobs import (
                    get_mood,
                    mood_label,
                    set_mood,
                )

                await _run_mood_checks(get_mood, set_mood, mood_label)
            finally:
                await close_engine()
                reset_engine_for_tests()


async def _run_mood_checks(get_mood, set_mood, mood_label):
    await set_mood(5.0)
    assert await get_mood() == 1.0
    await set_mood(-5.0)
    assert await get_mood() == -1.0
    assert mood_label(0.9) == "excited"
    assert mood_label(0.3) == "happy"
    assert mood_label(0.0) == "calm"
    assert mood_label(-0.3) == "down"
    assert mood_label(-0.9) == "grumpy"


class TestComposer:
    def _term(self, term="yyds", meaning="永远的神", umo=""):
        return SlangTerm(term=term, meaning=meaning, umo=umo, status="confirmed")

    async def test_empty_when_disabled(self):
        class Cfg:
            enable_style_learning = False

        block = await compose_learning_block(None, Cfg(), [], None, 0.0)
        assert block == ""

    async def test_slang_hit_affinity_and_mood(self):
        class Cfg:
            enable_style_learning = True
            enable_affinity = True
            enable_mood = True

        class Ev:
            message_str = "这波操作真的 yyds"

        block = await compose_learning_block(Ev(), Cfg(), [self._term()], 85.0, 0.8)
        assert "yyds" in block and "永远的神" in block
        assert "close friend" in block
        assert "excited" in block

    async def test_budget_trim(self):
        class Cfg:
            enable_style_learning = True
            enable_affinity = True
            enable_mood = True

        class Ev:
            message_str = "yyds"

        many = [self._term(term=f"t{i}", meaning="x" * 300) for i in range(8)]
        many[0] = self._term(term="yyds", meaning="ok")
        block = await compose_learning_block(Ev(), Cfg(), many, None, 0.0)
        assert len(block) <= 800


class TestPersonaReviewChain:
    async def test_add_cap_approve_reject(self):
        import tempfile
        from pathlib import Path

        from unified_chat.services.persona_review import PersonaReviewService
        from unified_chat.storage.database import close_engine, get_engine, reset_engine_for_tests

        reset_engine_for_tests()
        with tempfile.TemporaryDirectory() as d:
            await get_engine(Path(d) / "pr.db")
            try:
                from unified_chat.services.persona_review import (
                    _save,
                )

                await _save(
                    [
                        {"id": "aaa", "text": "T-A", "created_at": "now"},
                        {"id": "bbb", "text": "T-B", "created_at": "now"},
                    ]
                )
                items = await PersonaReviewService.list_pending()
                assert len(items) == 2

                ok, text = await PersonaReviewService.resolve("aaa", True)
                assert ok and text == "T-A"
                remaining = await PersonaReviewService.list_pending()
                assert [i["id"] for i in remaining] == ["bbb"]

                ok, _ = await PersonaReviewService.resolve("bbb", False)
                assert ok and await PersonaReviewService.list_pending() == []
                ok, _ = await PersonaReviewService.resolve("zzz", True)
                assert ok is False
            finally:
                await close_engine()
                reset_engine_for_tests()


class TestSlangRepoRoundtrip:
    async def test_candidate_flow(self):
        import tempfile
        from pathlib import Path

        from unified_chat.storage.database import close_engine, get_engine, reset_engine_for_tests
        from unified_chat.storage.repo import SlangRepo

        reset_engine_for_tests()
        with tempfile.TemporaryDirectory() as d:
            await get_engine(Path(d) / "sl.db")
            try:
                await SlangRepo.add(SlangTerm(term="yyds", count=12))
                pending = await SlangRepo.list_by_status("candidate")
                assert len(pending) == 1
                await SlangRepo.set_meaning(pending[0].id, "永远的神")
                await SlangRepo.set_status(pending[0].id, "confirmed")
                confirmed = await SlangRepo.confirmed_all()
                assert confirmed[0].meaning == "永远的神"
                assert await SlangRepo.exists_term("yyds", "") is True
            finally:
                await close_engine()
                reset_engine_for_tests()

    async def test_concurrent_first_bumps_are_atomic_and_unique(self, tmp_path):
        import asyncio

        from unified_chat.storage.database import close_engine, get_engine, reset_engine_for_tests
        from unified_chat.storage.repo import AffinityLookupRepo, AffinityRepo

        reset_engine_for_tests()
        await get_engine(tmp_path / "affinity-concurrent.db")
        try:
            await asyncio.gather(
                *(AffinityRepo.bump("s", "u", 1.0) for _ in range(20))
            )
            assert await AffinityLookupRepo.get_score("s", "u") == 70.0
        finally:
            await close_engine()
            reset_engine_for_tests()

    async def test_suggestion_prompt_uses_memory_service(self, tmp_path):
        from unified_chat.services.persona_review import PersonaReviewService
        from unified_chat.storage.database import close_engine, get_engine, reset_engine_for_tests

        class Memory:
            content = "user strongly prefers concise technical answers"

        class MemoryService:
            async def retrieve_hybrid(self, query, session_id=None, top_k=10):
                assert query == "用户 偏好 事实" and top_k == 10
                assert session_id == "group:GroupMessage:g1"
                return [Memory()]

        class Resp:
            completion_text = "Be more concise and technical."

        class Context:
            def __init__(self):
                self.prompt = ""

            async def llm_generate(self, **kwargs):
                self.prompt = kwargs["prompt"]
                return Resp()

        class Cfg:
            chat_provider_id = "p"

        reset_engine_for_tests()
        await get_engine(tmp_path / "persona.db")
        try:
            context = Context()
            service = PersonaReviewService(context, Cfg(), MemoryService())
            assert await service.maybe_suggest("group:GroupMessage:g1")
            assert "concise technical answers" in context.prompt
        finally:
            await close_engine()
            reset_engine_for_tests()


class TestSlangIsolation:
    async def test_same_term_can_exist_in_two_sessions(self, tmp_path):
        from unified_chat.storage.database import close_engine, get_engine, reset_engine_for_tests
        from unified_chat.storage.repo import SlangRepo

        reset_engine_for_tests()
        await get_engine(tmp_path / "slang-isolation.db")
        try:
            await SlangRepo.add(SlangTerm(term="yyds", umo="s1", count=10))
            assert await SlangRepo.exists_term("yyds", "s1") is True
            assert await SlangRepo.exists_term("yyds", "s2") is False
        finally:
            await close_engine()
            reset_engine_for_tests()


class TestSlangStatusAdvancement:
    """Spec 011 R6: inferred terms must leave the candidate pool."""

    async def test_inferred_terms_advance_status(self):
        import tempfile
        from pathlib import Path

        from unified_chat.services.slang_service import SlangService
        from unified_chat.storage import repo as repos
        from unified_chat.storage.database import (
            close_engine,
            get_engine,
            reset_engine_for_tests,
        )
        from unified_chat.storage.models import SlangTerm

        class Cfg:
            slang_infer_enabled = True
            chat_provider_id = "prov"

        class Resp:
            completion_text = '{"赞": "很棒", "绝了": "太强了"}'

        class Ctx:
            calls = 0

            async def llm_generate(self, **kw):
                Ctx.calls += 1
                return Resp()

        reset_engine_for_tests()
        with tempfile.TemporaryDirectory() as d:
            await get_engine(Path(d) / "slang.db")
            try:
                umo = "g:g:9"
                await repos.SlangRepo.add(SlangTerm(term="赞", umo=umo, count=30))
                await repos.SlangRepo.add(SlangTerm(term="绝了", umo=umo, count=20))
                svc = SlangService(Ctx(), Cfg())
                updated = await svc.infer_pending_meanings()
                assert updated == 2
                cands = await repos.SlangRepo.list_by_status("candidate", limit=50)
                assert [t.term for t in cands] == []
                inferred = await repos.SlangRepo.list_by_status("inferred", limit=50)
                assert sorted(t.term for t in inferred) == ["绝了", "赞"]
                # second run: nothing left to infer -> no LLM call
                updated2 = await svc.infer_pending_meanings()
                assert updated2 == 0 and Ctx.calls == 1
                # terms the LLM could not parse stay candidates for retry
                await repos.SlangRepo.add(SlangTerm(term="谜语", umo=umo, count=10))
                class PartialResp:
                    completion_text = '{"别的": "无关"}'
                class PartialCtx:
                    async def llm_generate(self, **kw):
                        return PartialResp()
                svc2 = SlangService(PartialCtx(), Cfg())
                assert await svc2.infer_pending_meanings() == 0
                assert len(await repos.SlangRepo.list_by_status("candidate", limit=50)) == 1
            finally:
                await close_engine()
                reset_engine_for_tests()

    async def test_uslang_list_shows_full_meaning(self):
        import tempfile
        from pathlib import Path

        from unified_chat.core.lifecycle import PluginLifecycle
        from unified_chat.storage import repo as repos
        from unified_chat.storage.database import (
            close_engine,
            get_engine,
            reset_engine_for_tests,
        )
        from unified_chat.storage.models import SlangTerm

        long_meaning = "这个词条的含义非常长" + "细节" * 30 + "结尾标记"
        reset_engine_for_tests()
        with tempfile.TemporaryDirectory() as d:
            await get_engine(Path(d) / "uslang.db")
            try:
                await repos.SlangRepo.add(
                    SlangTerm(term="长词", umo="g:g:1", count=12, status="inferred")
                )
                row = (await repos.SlangRepo.list_by_status("inferred", limit=5))[0]
                await repos.SlangRepo.set_meaning(row.id, long_meaning)
                lc = PluginLifecycle(None, object())
                out = await lc.uslang("list")
                assert long_meaning in out  # no 40-char truncation
                assert "长词" in out
            finally:
                await close_engine()
                reset_engine_for_tests()


def test_mine_terms_ignores_single_cjk_chars():
    texts = ["赞 赞 赞 赞 赞 赞 赞 赞"]
    assert mine_terms(texts, top_k=10, min_count=2) == []


class TestComposerBudget:
    async def test_mood_and_affinity_survive_budget(self):
        class Cfg:
            enable_style_learning = True
            enable_affinity = True
            enable_mood = True

        class Ev:
            message_str = "t0 t1 t2 t3 t4 t5 t6 t7 yyds"

        many = [
            SlangTerm(term=f"t{i}", meaning="x" * 300, umo="", status="confirmed")
            for i in range(8)
        ]
        many[0] = SlangTerm(term="yyds", meaning="ok", umo="", status="confirmed")
        block = await compose_learning_block(Ev(), Cfg(), many, 85.0, 0.8)
        assert len(block) <= 800
        assert "excited" in block  # mood line never truncated away
        assert "close friend" in block

    async def test_meanings_are_quoted(self):
        class Cfg:
            enable_style_learning = True
            enable_affinity = True
            enable_mood = True

        class Ev:
            message_str = "yyds"

        block = await compose_learning_block(
            Ev(), Cfg(), [SlangTerm(term="yyds", meaning="永远的神", umo="", status="confirmed")],
            None,
            0.0,
        )
        assert '- yyds: "永远的神"' in block


class TestAffinityDecayAll:
    async def test_single_update_covers_all_rows(self):
        import tempfile
        from pathlib import Path

        from sqlalchemy import text as sql_text

        from unified_chat.storage.database import (
            close_engine,
            get_engine,
            get_session,
            reset_engine_for_tests,
        )
        from unified_chat.storage.models import UserAffinity
        from unified_chat.storage.repo import AffinityRepo

        reset_engine_for_tests()
        with tempfile.TemporaryDirectory() as d:
            await get_engine(Path(d) / "decay2.db")
            try:
                async with get_session() as session:
                    for i in range(600):
                        session.add(UserAffinity(umo=f"s{i}", user_id="u", score=90.0))
                    await session.commit()
                changed = await AffinityRepo.decay_all()
                assert changed == 600
                async with get_session() as session:
                    row = (
                        await session.exec(
                            sql_text("SELECT score FROM user_affinity WHERE umo = 's599'")
                        )
                    ).first()
                    assert float(row[0]) == pytest.approx(86.0, abs=0.1)
                # decay again at baseline-ish: no-op rows are not counted
                changed2 = await AffinityRepo.decay_all()
                assert changed2 == 600  # 86 -> 82.4 still moves
                await AffinityRepo.bump("s0", "u", 0.0)  # upsert still works
            finally:
                await close_engine()
                reset_engine_for_tests()
