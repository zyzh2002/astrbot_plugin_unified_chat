"""E2E lifecycle test: real plugin class + real AstrMessageEvent (Docker only).

Imports the plugin exactly like AstrBot does (data.plugins.<name>.main),
constructs a real AstrMessageEvent, and exercises initialize / command
handlers / message pipeline against a temp data_dir.

Skipped outside AstrBot runtime.

NOTE: import astrbot.api FIRST (AstrBot import graph is order-sensitive).
"""

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("astrbot.api")
pytest.importorskip("astrbot.core.message.components")
pytest.importorskip("astrbot.core.platform.astr_message_event")

import data.plugins.astrbot_plugin_unified_chat.main as plugin_main  # noqa: E402
from astrbot.core.message.components import Plain  # noqa: E402
from astrbot.core.platform.astr_message_event import AstrMessageEvent  # noqa: E402
from astrbot.core.platform.astrbot_message import AstrBotMessage  # noqa: E402
from astrbot.core.platform.message_type import MessageType  # noqa: E402
from astrbot.core.platform.platform_metadata import PlatformMetadata  # noqa: E402


class StubContext:
    def __init__(self):
        self.kb_manager = None
        self.llm_generate = None

    def get_config(self):
        return {}


class TmpContext(StubContext):
    def __init__(self, data_dir: Path):
        super().__init__()
        self._data_dir = str(data_dir)

    def get_config(self):
        return {"data_dir": self._data_dir}


def make_event(text: str) -> AstrMessageEvent:
    msg = AstrBotMessage()
    msg.type = MessageType.FRIEND_MESSAGE
    msg.self_id = "bot-1"
    msg.session_id = "e2e-session"
    msg.message_id = "msg-1"
    msg.group = None
    sender = type("Sender", (), {})()
    sender.user_id = "u-1"
    sender.nickname = "e2e-user"
    msg.sender = sender
    msg.message = [Plain(text)]
    msg.message_str = text
    msg.raw_message = None
    msg.timestamp = 0
    meta = PlatformMetadata(name="webchat", description="test", id="webchat-1")
    return AstrMessageEvent(text, msg, meta, "e2e-session")


def _plain_text(result) -> str:
    chain = getattr(result, "chain", None) or []
    return " ".join(comp.text for comp in chain if hasattr(comp, "text"))


@pytest.mark.asyncio
async def test_plugin_lifecycle_in_real_astrbot(tmp_path):
    plugin = plugin_main.UnifiedChatPlugin(TmpContext(tmp_path / "data1"))
    await plugin.initialize()
    try:
        event = make_event("/unified_status")
        results = [r async for r in plugin.unified_status(event)]
        assert "status=loaded" in _plain_text(results[0])

        msg_event = make_event(
            "hello astrbot, this is a long enough message for memory and learning"
        )
        await plugin.on_message(msg_event)
        await asyncio.sleep(1.0)

        status = await plugin._lifecycle.get_status_async()
        assert "memories=" in status

        migrate_usage = [r async for r in plugin.unified_migrate(event, "")]
        assert "Usage" in _plain_text(migrate_usage[0])
    finally:
        await plugin.terminate()


@pytest.mark.asyncio
async def test_pipeline_writes_to_temp_db(tmp_path):

    plugin = plugin_main.UnifiedChatPlugin(TmpContext(tmp_path / "data2"))
    await plugin.initialize()
    try:
        msg_event = make_event(
            "second message long enough for memory and learning pipeline"
        )
        await plugin.on_message(msg_event)
        await asyncio.sleep(1.0)

        import sqlite3

        db = tmp_path / "data2" / "unified_chat.db"
        assert db.exists()
        con = sqlite3.connect(db)
        tables = {
            r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert tables >= {
            "messages",
            "memories",
            "learning_logs",
            "unified_kv",
            "memory_fts",
        }
        n_messages = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        n_memories = con.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        assert n_messages == 1
        assert n_memories == 1
    finally:
        await plugin.terminate()
