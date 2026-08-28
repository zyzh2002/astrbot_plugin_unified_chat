"""Tests for data_dir resolution tiers (spec 011 R11)."""

import sys
import types

from unified_chat.utils.path import resolve_data_dir


def _install_fake_startools(monkeypatch, expected_name, calls):
    mod = types.ModuleType("astrbot.api.star")

    class StarTools:
        @staticmethod
        def get_data_dir(plugin_name=None):
            calls.append(plugin_name)
            if plugin_name != expected_name:
                raise RuntimeError("caller frame not resolvable")
            return f"/data/plugin_data/{plugin_name}"

    mod.StarTools = StarTools
    monkeypatch.setitem(sys.modules, "astrbot.api.star", mod)


def test_tier2_passes_plugin_name(monkeypatch):
    calls = []
    _install_fake_startools(monkeypatch, "astrbot_plugin_unified_chat", calls)
    got = resolve_data_dir({}, object())
    assert calls == ["astrbot_plugin_unified_chat"]
    assert "astrbot_plugin_unified_chat" in str(got)


def test_tier4_fallback_uses_canonical_name(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "astrbot.api.star", None)
    monkeypatch.setattr(
        "unified_chat.utils.path.Path.cwd", lambda: tmp_path, raising=False
    )
    # break tier 3 too so tier 4 runs
    monkeypatch.setitem(
        sys.modules,
        "astrbot.core.utils.astrbot_path",
        None,
    )
    got = resolve_data_dir({}, object())
    assert str(got).endswith("astrbot_plugin_unified_chat")
