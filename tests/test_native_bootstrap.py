"""Tests for native bootstrap: platform mapping, caching, prefetch."""

import pytest

from unified_chat.native import bootstrap


@pytest.fixture
def fake_platform(monkeypatch):
    def set(sys_platform: str, machine: str):
        monkeypatch.setattr(bootstrap.sys, "platform", sys_platform)
        monkeypatch.setattr(bootstrap.platform, "machine", lambda: machine)

    return set


class TestPlatformKey:
    def test_windows_amd64(self, fake_platform):
        fake_platform("win32", "AMD64")
        assert bootstrap.platform_key() == "win_amd64"

    def test_linux_x86_64(self, fake_platform):
        fake_platform("linux", "x86_64")
        assert bootstrap.platform_key() == "manylinux_x86_64"

    def test_linux_aarch64(self, fake_platform):
        fake_platform("linux", "aarch64")
        assert bootstrap.platform_key() == "manylinux_aarch64"

    def test_macos_arm64(self, fake_platform):
        fake_platform("darwin", "arm64")
        assert bootstrap.platform_key() == "macosx_arm64"

    def test_unsupported_freebsd(self, fake_platform):
        fake_platform("freebsd", "amd64")
        assert bootstrap.platform_key() is None

    def test_linux_armv7(self, fake_platform):
        fake_platform("linux", "armv7l")
        assert bootstrap.platform_key() is None


class TestWheelAssetName:
    def test_linux_x86_64(self, fake_platform):
        fake_platform("linux", "x86_64")
        name = bootstrap.wheel_asset_name("0.1.0")
        assert name == (
            "astrbot_plugin_unified_chat-0.1.0-cp312-abi3-manylinux_2_28_x86_64.whl"
        )

    def test_windows(self, fake_platform):
        fake_platform("win32", "AMD64")
        assert bootstrap.wheel_asset_name("0.1.0") == (
            "astrbot_plugin_unified_chat-0.1.0-cp312-abi3-win_amd64.whl"
        )

    def test_macos(self, fake_platform):
        fake_platform("darwin", "arm64")
        assert bootstrap.wheel_asset_name("0.1.0") == (
            "astrbot_plugin_unified_chat-0.1.0-cp312-abi3-macosx_11_0_arm64.whl"
        )

    def test_unsupported_returns_none(self, fake_platform):
        fake_platform("freebsd", "amd64")
        assert bootstrap.wheel_asset_name("0.1.0") is None


class TestTryLoadCached:
    def _write_stub(self, monkeypatch, calls):
        import types

        def fake_import(path):
            calls.append(path)
            mod = types.ModuleType("stubmod")
            for fn in ("chunk_text", "hash_dedup", "score_importance"):
                setattr(mod, fn, lambda *a, _f=fn: _f)
            monkeypatch.setitem(
                __import__("sys").modules, "unified_chat._native", mod
            )
            monkeypatch.setattr(bootstrap, "_bind_facade", lambda mod: None)
            return mod

        return fake_import

    def test_loads_existing_binary(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            bootstrap, "_import_extension", self._write_stub(monkeypatch, calls)
        )
        native = tmp_path / "native"
        native.mkdir()
        (native / "_native.cp312-abi3-win_amd64.pyd").write_bytes(b"x")
        assert bootstrap.try_load_cached(tmp_path) is True
        assert len(calls) == 1

    def test_missing_dir_returns_false(self, tmp_path, monkeypatch):
        def no_call(_path):
            raise AssertionError("should not be called")

        monkeypatch.setattr(bootstrap, "_import_extension", no_call)
        assert bootstrap.try_load_cached(tmp_path / "nope") is False

    def test_corrupt_binary_falls_through(self, tmp_path, monkeypatch):
        def boom(_path):
            raise ImportError("bad magic")

        monkeypatch.setattr(bootstrap, "_import_extension", boom)
        native = tmp_path / "native"
        native.mkdir()
        (native / "_native_broken.pyd").write_bytes(b"garbage")
        assert bootstrap.try_load_cached(tmp_path) is False

    def test_prefers_so_then_pyd(self, tmp_path, monkeypatch):
        seen = []

        def rec(path):
            seen.append(path.name)
            raise ImportError("stop at first")

        monkeypatch.setattr(bootstrap, "_import_extension", rec)
        native = tmp_path / "native"
        native.mkdir()
        (native / "_native_zzz.pyd").write_bytes(b"x")
        (native / "_native_aaa.so").write_bytes(b"x")
        bootstrap.try_load_cached(tmp_path)
        assert seen[0] == "_native_aaa.so"


class TestFacadeFallbackIntact:
    def test_fallback_functions_still_importable(self):
        from unified_chat.native import chunk_text, hash_dedup, score_importance

        assert callable(chunk_text)
        assert callable(hash_dedup)
        assert callable(score_importance)
