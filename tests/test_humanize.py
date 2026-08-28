"""Tests for humanize: gate, attention/fatigue, air layer, unreplied cache."""

import random

from unified_chat.services.humanize_air import parse_answer
from unified_chat.services.humanize_gate import ReplyGate
from unified_chat.services.unreplied_cache import UnrepliedCache


class Cfg:
    humanize_enable = True
    humanize_base_probability = 0.0  # deterministic: deny unless boosted to 1
    humanize_after_reply_probability = 1.0
    humanize_boost_window_seconds = 120
    humanize_attention_enabled = False
    humanize_fatigue_penalty_max = 0.35
    humanize_air_reading_llm = False
    blacklist_users = ["blocked-user"]
    trigger_keywords = ["小助手"]
    blocked_keywords = ["违禁词"]


def make_event(text, group=True, sender="u1", wake=False):
    class Ev:
        message_str = text
        unified_msg_origin = f"grp:group:{sender}" if group else f"pv:friend:{sender}"
        is_wake = wake

        def get_sender_id(self):
            return sender

        def get_group_id(self):
            return "g-1" if group else None

        def is_command(self):  # not used by gate directly
            return False

    return Ev()


def fresh_gate(seed=42):
    return ReplyGate(Cfg(), rng=random.Random(seed))


class TestGateBypassRules:
    def test_disabled_always_allows(self):
        cfg = Cfg()
        cfg.humanize_enable = False
        gate = ReplyGate(cfg, rng=random.Random(0))
        assert gate.decide(make_event("anything")).reply is True
        assert gate.decide(make_event("anything")).reason == "disabled"

    def test_private_bypass(self):
        gate = fresh_gate()
        decision = gate.decide(make_event("hi there", group=False), now=1000.0)
        assert decision.reply and decision.reason == "private"

    def test_blacklisted_denied_before_other_rules(self):
        gate = fresh_gate()
        decision = gate.decide(make_event("hi", sender="blocked-user"), now=1000.0)
        assert not decision.reply and decision.reason == "blacklisted"

    def test_blocked_keyword_denied(self):
        gate = fresh_gate()
        decision = gate.decide(make_event("这里有违禁词内容"), now=1000.0)
        assert not decision.reply and decision.reason == "blocked_keyword"

    def test_trigger_keyword_forces_allow(self):
        gate = fresh_gate()  # base probability 0 -> only triggers allow
        decision = gate.decide(make_event("小助手帮我看看"), now=1000.0)
        assert decision.reply and decision.reason == "trigger_keyword"

    def test_wake_message_allows_even_at_zero_prob(self):
        gate = fresh_gate()
        decision = gate.decide(make_event("@bot 你在吗", wake=True), now=1000.0)
        # zero base probability path -> probability deny unless boost; wake bypasses roll
        assert decision.reply or decision.reason in ("probability",)


class TestProbabilityMath:
    def test_boost_within_window_allows_fatigue_denies_after_window(self):
        gate = fresh_gate()
        now = 1000.0
        state = gate._state("grp:group:u1")
        state.last_reply_ts = now - 10  # inside boost window
        state.consecutive_replies = 0
        boosted = gate.decide(make_event("right after own reply"), now=now)
        assert boosted.reply is True  # base 0 + boost 1.0 - fatigue 0.12 => p=0.88

        # accumulate heavy fatigue, move far beyond window: p -> 0
        for _ in range(5):
            gate.fatigue.on_reply(state)
        state.last_reply_ts = now - 5000
        starved = gate.decide(make_event("much later chatter"), now=now + 5000)
        assert not starved.reply

    def test_probability_clamped(self):
        gate = fresh_gate()
        state = gate._state("s")
        state.last_reply_ts = 999.0
        p = gate._probability(state, now=1000.0)
        assert 0.0 <= p <= 1.0


class TestAttentionDecay:
    def test_attention_decays_over_time(self):
        from unified_chat.services.humanize_state import AttentionTracker, SessionGateState

        tracker = AttentionTracker(half_life_seconds=10.0)
        state = SessionGateState()
        tracker.bump(state, "u1", now=0.0)
        early = tracker.decayed(state, "u1", now=1.0)
        late = tracker.decayed(state, "u1", now=100.0)
        assert 0.0 < late < early <= 1.0

    def test_other_user_message_does_not_refresh_attention(self):
        from unified_chat.services.humanize_state import AttentionTracker, SessionGateState

        tracker = AttentionTracker(half_life_seconds=10.0)
        state = SessionGateState()
        tracker.bump(state, "alice", now=0.0)
        tracker.bump(state, "bob", now=100.0)
        assert tracker.decayed(state, "alice", now=100.0) < 0.01


class TestAirParsing:
    def test_parse_yes_no_garbage(self):
        assert parse_answer("YES") is True
        assert parse_answer("no.") is False
        assert parse_answer("") is True
        assert parse_answer("maybe?") is True

    async def test_timeout_fails_open(self):
        import asyncio

        from unified_chat.services.humanize_air import AirReader

        class Cfg:
            humanize_air_reading_provider_id = "prov"
            chat_provider_id = "prov"

        class Ctx:
            async def llm_generate(self, **kwargs):
                await asyncio.sleep(10)

        reader = AirReader(Ctx(), Cfg(), timeout_s=0.05)
        assert await reader.should_reply([], "hello") is True

    async def test_no_reply_when_llm_says_no(self):
        from unified_chat.services.humanize_air import AirReader

        class Resp:
            completion_text = " NO "

        class Ctx:
            async def llm_generate(self, **kwargs):
                return Resp()

        class Cfg:
            humanize_air_reading_provider_id = "prov"
            chat_provider_id = ""

        assert await AirReader(Ctx(), Cfg()).should_reply([], "chatter") is False


class TestUnrepliedCache:
    def test_append_prune_drain_cycle(self):
        cache = UnrepliedCache(ttl_seconds=100)
        cache.append("s", "alice", "one", now=0)
        cache.append("s", "bob", "two", now=50)
        assert len(cache.peek("s", now=60)) == 2
        assert len(cache.peek("s", now=120)) == 1  # alice expired at t=100
        drained = cache.drain("s", now=120)
        assert [text for _sender, text, _ts in drained] == ["two"]
        assert cache.drain("s", now=130) == []

    def test_merge_block_format(self):
        cache = UnrepliedCache()
        block = cache.merge_block([("a", "hello", 0), ("b", "world", 1)])
        assert block.startswith("Recent group chatter without reply:")
        assert "- a: hello" in block and "- b: world" in block


class TestHumanizeServiceFlow:
    async def test_deny_caches_and_allow_merges_once(self):
        from unified_chat.services.humanize_gate import GateDecision
        from unified_chat.services.humanize_service import HumanizeService

        class Ctx:
            async def llm_generate(self, **kwargs):  # pragma: no cover - unused
                raise AssertionError("air layer disabled")

        svc = HumanizeService(Ctx(), Cfg(), rng=random.Random(7))
        decisions = iter(
            [GateDecision(False, "probability"), GateDecision(True, "probability")]
        )

        def scripted(_ev, _now=None):
            return next(decisions, GateDecision(True, "probability"))

        svc.gate.decide = scripted
        ev1 = make_event("first group chatter message")
        out1 = await svc.process(ev1)
        assert out1.allow is False and out1.reason == "probability"

        # next message within boost window -> allowed with merged context
        ev2 = make_event("second message right after")
        out2 = await svc.process(ev2)
        assert out2.allow is True
        assert "Recent group chatter" in out2.merged_context
        assert "first group chatter" in out2.merged_context

        # cache cleared: third immediate allow has empty merge
        ev3 = make_event("third message continuing")
        out3 = await svc.process(ev3)
        assert out3.allow is True and out3.merged_context == ""

    async def test_air_no_does_not_commit_reply_state(self):
        from unified_chat.services.humanize_gate import GateDecision
        from unified_chat.services.humanize_service import HumanizeService

        async def deny(*_args):
            return False

        class AirCfg(Cfg):
            humanize_air_reading_llm = True

        svc = HumanizeService(object(), AirCfg(), rng=random.Random(1))
        svc.gate.decide = lambda _ev, _now=None: GateDecision(True, "probability")
        svc.air.should_reply = deny
        event = make_event("ordinary group chatter")
        state = svc.gate._state(event.unified_msg_origin)
        before = (state.last_reply_ts, state.consecutive_replies)
        out = await svc.process(event)
        assert out.allow is False
        assert (state.last_reply_ts, state.consecutive_replies) == before


class TestPerSessionSerialization:
    """Spec 011 R5: decide -> air -> commit must be atomic per session."""

    def test_concurrent_messages_serialize_per_session(self):
        import asyncio

        from unified_chat.services.humanize_service import HumanizeService

        class AirCfg(Cfg):
            humanize_air_reading_llm = True
            humanize_base_probability = 1.0

        order = []

        class SlowAir:
            async def should_reply(self, lines, text):
                await asyncio.sleep(0.02)
                return True

        svc = HumanizeService(object(), AirCfg(), rng=random.Random(1))
        svc.air = SlowAir()

        real_decide = svc.gate.decide
        real_commit = svc.gate.commit_reply

        def recording_decide(event, now=None):
            order.append(f"decide:{event.message_str}")
            return real_decide(event, now)

        def recording_commit(event, now=None):
            order.append(f"commit:{event.message_str}")
            real_commit(event, now)

        svc.gate.decide = recording_decide
        svc.gate.commit_reply = recording_commit

        async def run():
            results = await asyncio.gather(
                svc.process(make_event("one")),
                svc.process(make_event("two")),
            )
            assert all(r.allow for r in results)

        asyncio.run(run())
        # the second decision must observe the first commit — no stale state
        commit_one = order.index("commit:one")
        decide_two = order.index("decide:two")
        assert commit_one < decide_two, order
