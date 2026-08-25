"""Full-boot acceptance tests against a real AstrBot subprocess."""

from __future__ import annotations

import pytest

from .harness import wait_until

pytestmark = pytest.mark.fullboot


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
    assert {"messages", "memories", "learning_logs", "unified_kv"} <= tables


def test_social_context_injected_into_llm_prompt(harness):
    first = "sentinel-cobalt-7f3a2b keep this exact token in mind please"
    second = "what token did I just mention?"
    harness.chat(first)
    harness.chat(second)

    def seen():
        return "cobalt-7f3a2b" in harness.mock.all_prompt_text()

    assert wait_until(seen, timeout_s=30), (
        f"first message never reached LLM prompt; got:\n{harness.mock.all_prompt_text()[-2000:]}"
    )
