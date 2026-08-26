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
    @staticmethod
    def _native_dir(root):
        return root / "native" / "0.1.0"

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
        native = self._native_dir(tmp_path)
        native.mkdir(parents=True)
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
        native = self._native_dir(tmp_path)
        native.mkdir(parents=True)
        (native / "_native_broken.pyd").write_bytes(b"garbage")
        assert bootstrap.try_load_cached(tmp_path) is False

    def test_prefers_so_then_pyd(self, tmp_path, monkeypatch):
        seen = []

        def rec(path):
            seen.append(path.name)
            raise ImportError("stop at first")

        monkeypatch.setattr(bootstrap, "_import_extension", rec)
        native = self._native_dir(tmp_path)
        native.mkdir(parents=True)
        (native / "_native_zzz.pyd").write_bytes(b"x")
        (native / "_native_aaa.so").write_bytes(b"x")
        bootstrap.try_load_cached(tmp_path)
        assert seen[0] == "_native_aaa.so"

    def test_module_names_follow_local_package(self, monkeypatch):
        monkeypatch.setattr(bootstrap, "__package__", "unified_chat.native")
        assert bootstrap._module_names() == (
            "unified_chat._native",
            "unified_chat.native",
        )

    def test_module_names_follow_astrbot_package(self, monkeypatch):
        monkeypatch.setattr(
            bootstrap,
            "__package__",
            "data.plugins.astrbot_plugin_unified_chat.unified_chat.native",
        )
        assert bootstrap._module_names() == (
            "data.plugins.astrbot_plugin_unified_chat.unified_chat._native",
            "data.plugins.astrbot_plugin_unified_chat.unified_chat.native",
        )

    def test_cache_writer_and_loader_share_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "unified_chat.utils.path.resolve_data_dir", lambda raw, context: tmp_path
        )
        monkeypatch.setattr(bootstrap, "plugin_version", lambda: "0.1.0")
        assert bootstrap.default_cache_dir() == tmp_path
        assert bootstrap.cache_dir() == tmp_path / "native" / "0.1.0"


class TestFacadeFallbackIntact:
    def test_fallback_functions_still_importable(self):
        from unified_chat.native import chunk_text, hash_dedup, score_importance

        assert callable(chunk_text)
        assert callable(hash_dedup)
        assert callable(score_importance)


def _make_wheel(member: str = "unified_chat/_native.cp312-abi3-x.so") -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(member, b"fake-binary-payload")
    return buf.getvalue()


class TestExtractAndChecksum:
    def test_extract_native_member(self, tmp_path):
        bootstrap._extract_native(_make_wheel(), tmp_path)
        out = list(tmp_path.glob("_native*.so"))
        assert len(out) == 1
        assert out[0].read_bytes() == b"fake-binary-payload"

    def test_extract_rejects_wheel_without_native(self, tmp_path):
        with pytest.raises(ValueError, match="no native extension member"):
            bootstrap._extract_native(_make_wheel("unified_chat/other.txt"), tmp_path)

    def test_expected_sha256_found(self):
        import hashlib

        wheel = _make_wheel()
        digest = hashlib.sha256(wheel).hexdigest()
        asset = "astrbot_plugin_unified_chat-0.1.0-cp312-abi3-win_amd64.whl"
        sums = f"{digest}  {asset}\n"
        assert bootstrap._expected_sha256(sums, asset) == digest

    def test_expected_sha256_handles_star_and_dirs(self):
        import hashlib

        wheel = _make_wheel()
        digest = hashlib.sha256(wheel).hexdigest()
        asset = "astrbot_plugin_unified_chat-0.1.0-cp312-abi3-win_amd64.whl"
        sums = f"{digest} *dist/{asset}\n"
        assert bootstrap._expected_sha256(sums, asset) == digest
        assert bootstrap._expected_sha256(sums, "missing.whl") is None


class TestPrefetch:
    async def test_happy_path_caches_binary(self, tmp_path, monkeypatch):
        import hashlib

        wheel = _make_wheel()
        digest = hashlib.sha256(wheel).hexdigest()
        asset = "astrbot_plugin_unified_chat-0.1.0-cp312-abi3-win_amd64.whl"

        async def fake_fetch(url):
            if url.endswith(asset):
                return wheel
            return f"{digest}  {asset}\n".encode()

        monkeypatch.setattr(bootstrap, "_fetch", fake_fetch)
        monkeypatch.setattr(bootstrap, "cache_dir", lambda: tmp_path)
        monkeypatch.setattr(bootstrap, "plugin_version", lambda: "0.1.0")
        monkeypatch.setattr(bootstrap.platform, "machine", lambda: "AMD64")
        monkeypatch.setattr(bootstrap.sys, "platform", "win32")

        await bootstrap.prefetch()
        assert list(tmp_path.glob("_native*"))

    async def test_checksum_mismatch_skips(self, tmp_path, monkeypatch):
        wheel = _make_wheel()
        asset = "astrbot_plugin_unified_chat-0.1.0-cp312-abi3-win_amd64.whl"

        async def fake_fetch(url):
            if url.endswith(asset):
                return wheel
            return b"deadbeef  wrong\n"

        monkeypatch.setattr(bootstrap, "_fetch", fake_fetch)
        monkeypatch.setattr(bootstrap, "cache_dir", lambda: tmp_path)
        monkeypatch.setattr(bootstrap, "plugin_version", lambda: "0.1.0")
        monkeypatch.setattr(bootstrap.platform, "machine", lambda: "AMD64")
        monkeypatch.setattr(bootstrap.sys, "platform", "win32")

        await bootstrap.prefetch()
        assert not list(tmp_path.glob("_native*"))

    async def test_network_error_silent(self, tmp_path, monkeypatch):
        async def boom(_url):
            raise OSError("network down")

        monkeypatch.setattr(bootstrap, "_fetch", boom)
        monkeypatch.setattr(bootstrap, "cache_dir", lambda: tmp_path)
        monkeypatch.setattr(bootstrap, "plugin_version", lambda: "0.1.0")
        monkeypatch.setattr(bootstrap.platform, "machine", lambda: "AMD64")
        monkeypatch.setattr(bootstrap.sys, "platform", "win32")

        await bootstrap.prefetch()
        assert not list(tmp_path.glob("_native*"))

    async def test_corrupt_cache_does_not_prevent_redownload(
        self, tmp_path, monkeypatch
    ):
        import hashlib

        version_dir = tmp_path / "native" / "0.1.0"
        version_dir.mkdir(parents=True)
        (version_dir / "_native_broken.pyd").write_bytes(b"garbage")
        wheel = _make_wheel("unified_chat/_native.cp312-abi3-win_amd64.pyd")
        digest = hashlib.sha256(wheel).hexdigest()
        asset = "astrbot_plugin_unified_chat-0.1.0-cp312-abi3-win_amd64.whl"
        fetched = []

        async def fake_fetch(url):
            fetched.append(url)
            if url.endswith(asset):
                return wheel
            return f"{digest}  {asset}\n".encode()

        monkeypatch.setattr(bootstrap, "_fetch", fake_fetch)
        monkeypatch.setattr(bootstrap, "default_cache_dir", lambda: tmp_path)
        monkeypatch.setattr(bootstrap, "plugin_version", lambda: "0.1.0")
        monkeypatch.setattr(bootstrap.sys, "platform", "win32")
        monkeypatch.setattr(bootstrap.platform, "machine", lambda: "AMD64")
        await bootstrap.prefetch()
        assert len(fetched) == 2
        assert list(version_dir.glob("_native*.pyd"))

    async def test_prefetch_async_disabled(self):
        assert bootstrap.prefetch_async(False) is None

    async def test_prefetch_async_when_native_present(self, monkeypatch):
        import types

        monkeypatch.setitem(
            __import__("sys").modules,
            "unified_chat._native",
            types.ModuleType("x"),
        )
        assert bootstrap.prefetch_async(True) is None


class TestChecksumFormats:
    def test_parses_bsd_tag_format(self):
        import hashlib

        wheel = _make_wheel()
        digest = hashlib.sha256(wheel).hexdigest()
        asset = "astrbot_plugin_unified_chat-0.1.0-cp312-abi3-win_amd64.whl"
        sums = f"SHA256 ({asset}) = {digest}\n"
        assert bootstrap._expected_sha256(sums, asset) == digest
