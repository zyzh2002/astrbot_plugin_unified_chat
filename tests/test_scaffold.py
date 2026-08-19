"""Scaffold smoke test: metadata and main importable with mocked AstrBot."""

import sys
import types


def _install_astrbot_stubs():
    # Stub astrbot.api.event
    if "astrbot.api.event" not in sys.modules:
        mod = types.ModuleType("astrbot.api.event")

        class DummyFilter:
            class EventMessageType:
                ALL = "all"

            def event_message_type(self, _):
                def deco(fn):
                    return fn

                return deco

            def on_llm_request(self):
                def deco(fn):
                    return fn

                return deco

            def command(self, *_a, **_kw):
                def deco(fn):
                    return fn

                return deco

        mod.filter = DummyFilter()
        mod.AstrMessageEvent = object
        sys.modules["astrbot.api.event"] = mod

    if "astrbot.api.star" not in sys.modules:
        mod = types.ModuleType("astrbot.api.star")

        class Star:
            def __init__(self, context):
                self.context = context

        class Context:
            pass

        mod.Star = Star
        mod.Context = Context
        sys.modules["astrbot.api.star"] = mod

    if "astrbot.api" not in sys.modules:
        mod = types.ModuleType("astrbot.api")
        log_mod = types.ModuleType("astrbot.api.logger")
        mod.logger = types.SimpleNamespace(info=lambda *a, **kw: None, error=lambda *a, **kw: None)
        sys.modules["astrbot.api"] = mod
        sys.modules["astrbot.api.logger"] = log_mod


_install_astrbot_stubs()


def test_metadata_loads():
    import pathlib

    import yaml

    data = yaml.safe_load(pathlib.Path("metadata.yaml").read_text(encoding="utf-8"))
    assert data["name"] == "astrbot_plugin_unified_chat"
    assert "astrbot_version" in data


def test_main_importable():
    import main  # noqa: F401

    assert True
