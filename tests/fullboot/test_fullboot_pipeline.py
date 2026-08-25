"""Full-boot acceptance tests against a real AstrBot subprocess."""

from __future__ import annotations

import pytest

from .harness import wait_until

pytestmark = pytest.mark.fullboot


def _system_prompt_text(harness) -> str:
    """Concatenated system-role contents seen by the mock LLM."""
    parts = []
    for req in harness.mock.requests:
        for msg in req.get("messages") or []:
            if msg.get("role") == "system":
                content = msg.get("content")
                if isinstance(content, str):
                    parts.append(content)
    return "\n".join(parts)


def test_boot_and_status(harness):
    reply = harness.chat("/unified_status")
    assert "status=loaded" in reply


def test_message_persists_to_plugin_db(harness):
    text = "hello astrbot, this message is definitely long enough for storage"
    harness.chat(text)

    def counts():
        rows = harness.sqlite_query(
            "SELECT COUNT(*) FROM messages WHERE content LIKE '%long enough%'"
        )
        return rows and rows[0][0] >= 1

    assert wait_until(counts, timeout_s=30), "message row never appeared"
    tables = {
        r[0]
        for r in harness.sqlite_query(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"messages", "memories", "learning_logs", "unified_kv", "memory_fts"} <= tables


def test_social_context_injected_into_llm_prompt(harness):
    first = "sentinel-cobalt-7f3a2b keep this exact token in mind please"
    second = "what token did I just mention?"
    harness.chat(first)
    harness.chat(second)

    assert wait_until(
        lambda: "cobalt-7f3a2b" in _system_prompt_text(harness), timeout_s=30
    ), f"token missing from system prompts; got:\n{_system_prompt_text(harness)[-2000:]}"


def test_memory_atom_recalled_via_hybrid_path(harness):
    marker = "sentinel-amber-9c41d7"
    fact = (
        f"please remember clearly that my favorite access code is {marker} "
        "and write it down for later use"
    )
    harness.chat(fact)

    def stored():
        rows = harness.sqlite_query(
            f"SELECT COUNT(*) FROM memories WHERE content LIKE '%{marker}%'"
        )
        return bool(rows) and rows[0][0] >= 1

    assert wait_until(stored, timeout_s=30), "memory atom was not stored"

    probe = "what is my favorite access code again? hint: it starts with sentinel"
    harness.chat(probe)

    assert wait_until(
        lambda: marker in _system_prompt_text(harness), timeout_s=30
    ), "stored memory was not injected via hybrid retrieval"


def test_humanize_enabled_private_still_replies():
    """Regression: with humanize on, private/webchat chats must always reply."""
    from .harness import AstrBotHarness

    harness = AstrBotHarness.start(plugin_config={"humanize_enable": True})
    try:
        reply = harness.chat("/unified_status")
        assert "status=loaded" in reply
        probe = "plain private message that must get an answer regardless of gate"
        reply2 = harness.chat(probe)
        assert reply2.strip(), "private chat got swallowed by the gate"
    finally:
        harness.stop()
