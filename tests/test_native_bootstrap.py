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
