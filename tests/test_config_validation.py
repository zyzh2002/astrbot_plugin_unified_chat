"""Tests for PluginConfig validation and coercion."""

from unified_chat.config import PluginConfig


def test_config_clamps_invalid():
    c = PluginConfig.from_dict({"memory_cleanup_days": -5, "importance_threshold": 2.0})
    assert c.memory_cleanup_days == 30
    assert 0 <= c.importance_threshold <= 1
    c2 = PluginConfig.from_dict({"rag_kbs": "not-a-list"})  # type: ignore[arg-type]
    assert isinstance(c2.rag_kbs, list)


def test_memory_kb_name_default_and_override():
    assert PluginConfig().memory_kb_name == "unified_chat_memories"
    c = PluginConfig.from_dict({"memory_kb_name": "my_mem"})
    assert c.memory_kb_name == "my_mem"


def test_to_dict_roundtrip():
    c = PluginConfig.from_dict({"rag_kbs": ["kb1"], "memory_kb_name": "m"})
    d = c.to_dict()
    assert d["rag_kbs"] == ["kb1"]
    assert d["memory_kb_name"] == "m"
    assert "data_dir" not in d


def test_native_autodownload_roundtrip():
    from unified_chat.config import PluginConfig

    cfg = PluginConfig.from_dict({"native_autodownload": False})
    assert cfg.native_autodownload is False
    assert PluginConfig.from_dict({}).native_autodownload is True
    assert PluginConfig.from_dict({"native_autodownload": "yes"}).native_autodownload is True
    assert "native_autodownload" in PluginConfig.from_dict({}).to_dict()


def test_humanize_keys_roundtrip():
    from unified_chat.config import PluginConfig

    cfg = PluginConfig.from_dict(
        {
            "humanize_enable": True,
            "humanize_base_probability": 0.4,
            "blacklist_users": ["10001", 10002],
            "trigger_keywords": ["小助手"],
            "blocked_keywords": [],
        }
    )
    assert cfg.humanize_enable is True
    assert abs(cfg.humanize_base_probability - 0.4) < 1e-9
    assert cfg.blacklist_users == ["10001", "10002"]
    assert PluginConfig.from_dict({}).humanize_enable is False
    dump = cfg.to_dict()
    assert "blacklist_users" in dump and "humanize_enable" in dump


def test_phase8_learning_keys_roundtrip():
    from unified_chat.config import PluginConfig

    cfg = PluginConfig.from_dict(
        {
            "enable_style_learning": False,
            "slang_top_k": 20,
            "enable_affinity": False,
            "persona_auto_suggest": True,
        }
    )
    assert cfg.enable_style_learning is False
    assert cfg.slang_top_k == 20
    assert cfg.enable_affinity is False
    assert cfg.persona_auto_suggest is True
    assert PluginConfig.from_dict({}).enable_style_learning is True
    assert PluginConfig.from_dict({}).persona_auto_suggest is False
