# Native Cross-Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship prebuilt native wheels for 4 platforms and let the plugin auto-fetch/load them at startup with fail-silent fallback.

**Architecture:** abi3-py312 wheels built by maturin-action matrix on tag push, published to GitHub Releases with SHA256SUMS; runtime bootstrap loads cached binaries from `data/plugin_data/`, prefetches missing ones in background over stdlib urllib, never raises.

**Tech Stack:** Rust/pyo3 (abi3), GitHub Actions (PyO3/maturin-action), stdlib-only Python (urllib, zipfile, hashlib, importlib).

**Spec:** `.agents/docs/specs/007-native-cross-platform.md`

## Global Constraints

- Runtime deps: ZERO new dependencies (stdlib only for bootstrap).
- Downloaded binaries go under `data/plugin_data/<plugin>/native/`, never plugin source dir.
- Any bootstrap failure logs and continues; never raises, never blocks startup.
- All code comments in English; no Chinese identifiers.
- Verify with `uv run pytest -q`, `uv run ruff check .`, `cargo test -p unified_chat_native`.
- Branch: `feat/native-cross-platform` (already created).

---

### Task 1: Enable abi3-py312

**Files:**
- Modify: `rust/Cargo.toml:11`

**Interfaces:**
- Produces: wheel tags `cp312-abi3-*` (consumed by Task 5 `wheel_asset_name`).

- [ ] **Step 1: Edit pyo3 features**

In `rust/Cargo.toml` replace:

```toml
pyo3 = { version = "0.26", features = ["extension-module"] }
```

with:

```toml
pyo3 = { version = "0.26", features = ["extension-module", "abi3-py312"] }
```

- [ ] **Step 2: Local build verifies tag**

Run: `uv run maturin build --release --out %TEMP%\uc-wheel`
Expected: output filename contains `cp312-abi3-win_amd64` (on Windows host).

- [ ] **Step 3: Rust tests stay green**

Run: `cargo test -p unified_chat_native`
Expected: PASS (unchanged behavior).

- [ ] **Step 4: Commit**

```bash
git add rust/Cargo.toml
git commit -m "feat(native): enable abi3-py312 so each platform ships one wheel"
```

### Task 2: Release workflow

**Files:**
- Create: `.github/workflows/release.yml`

**Interfaces:**
- Produces: release assets `unified_chat_native-<ver>-cp312-abi3-{manylinux_2_28_x86_64|manylinux_2_28_aarch64|win_amd64|macosx_11_0_arm64}.whl` + `SHA256SUMS` (consumed by Task 5 URLs).

- [ ] **Step 1: Create workflow**

```yaml
name: Release

on:
  push:
    tags: ["v*"]

permissions:
  contents: write

jobs:
  build:
    name: Build ${{ matrix.target }}
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        include:
          - target: x86_64-unknown-linux-gnu
            os: ubuntu-latest
            manylinux: "2_28"
          - target: aarch64-unknown-linux-gnu
            os: ubuntu-latest
            manylinux: "2_28"
          - target: x86_64-pc-windows-msvc
            os: windows-latest
            manylinux: "auto"
          - target: aarch64-apple-darwin
            os: macos-latest
            manylinux: "auto"
    steps:
      - uses: actions/checkout@v4
      - uses: PyO3/maturin-action@v1
        with:
          target: ${{ matrix.target }}
          manylinux: ${{ matrix.manylinux }}
          command: build
          args: --release --strip --out dist
      - uses: actions/upload-artifact@v4
        with:
          name: wheel-${{ matrix.target }}
          path: dist/*.whl

  release:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with:
          merge-multiple: true
          path: dist
      - name: Checksums and version guard
        shell: bash
        run: |
          TAG_NUM="${GITHUB_REF_NAME#v}"
          for w in dist/*.whl; do
            case "$(basename "$w")" in
              *-"${TAG_NUM}"-*) ;;
              *) echo "::error::wheel version mismatch: $w"; exit 1 ;;
            esac
          done
          cd dist && sha256sum --tag *.whl > SHA256SUMS
      - uses: softprops/action-gh-release@v2
        with:
          files: |
            dist/*.whl
            dist/SHA256SUMS
```

Note: manylinux MUST be passed as action input (not CLI arg) — CI run 32835734228 proved CLI args skip containerization.

- [ ] **Step 2: Sanity-check YAML parses**

Run: `uv run python -c "import yaml,pathlib;yaml.safe_load(pathlib.Path('.github/workflows/release.yml').read_text(encoding='utf-8'));print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "feat(ci): build 4-platform abi3 wheels and publish releases with SHA256SUMS"
```

### Task 3: bootstrap platform mapping (TDD)

**Files:**
- Create: `unified_chat/native/bootstrap.py`
- Test: `tests/test_native_bootstrap.py`

**Interfaces:**
- Produces: `platform_key() -> str | None`, `wheel_asset_name(version: str) -> str | None`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_native_bootstrap.py`:

```python
"""Tests for native bootstrap: platform mapping, caching, prefetch."""

import pytest

from unified_chat.native import bootstrap


@pytest.fixture
def fake_platform(monkeypatch):
    def set(sys_platform: str, machine: str):
        monkeypatch.setattr(bootstrap.sys, "platform", sys_platform)
        monkeypatch.setattr(
            bootstrap.platform, "machine", lambda: machine
        )

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
    @pytest.mark.parametrize(
        ("version", "suffix"),
        [
            ("0.1.0", "-cp312-abi3-manylinux_2_28_x86_64.whl"),
        ],
    )
    def test_supported(self, fake_platform, version, suffix):
        fake_platform("linux", "x86_64")
        name = bootstrap.wheel_asset_name(version)
        assert name == f"astrbot_plugin_unified_chat-{version}{suffix}"

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
```

- [ ] **Step 2: Run tests, expect ImportError**

Run: `uv run pytest tests/test_native_bootstrap.py -q`
Expected: FAIL (`cannot import name 'bootstrap'` / ModuleNotFoundError)

- [ ] **Step 3: Implement mapping**

Create `unified_chat/native/bootstrap.py`:

```python
"""Native binary bootstrap: cached loading and release prefetch.

Fail-silent by contract: no function here may raise out of the plugin
startup path; all failures degrade to the pure-Python fallback.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

PLUGIN_REPO_SLUG = "zyzh2002/astrbot_plugin_unified_chat"
RELEASE_BASE = (
    f"https://github.com/{PLUGIN_REPO_SLUG}/releases/download"
)
DOWNLOAD_TIMEOUT_S = 30.0
MAX_WHEEL_BYTES = 32 * 1024 * 1024


def platform_key() -> str | None:
    """Map current interpreter to a release-asset selector, or None."""
    system = sys.platform
    machine = (platform.machine() or "").lower()
    if system.startswith("win") and machine in ("amd64", "x86_64"):
        return "win_amd64"
    if system.startswith("linux"):
        if machine in ("amd64", "x86_64"):
            return "manylinux_x86_64"
        if machine in ("aarch64", "arm64"):
            return "manylinux_aarch64"
    if system == "darwin" and machine in ("arm64", "aarch64"):
        return "macosx_arm64"
    return None


def wheel_asset_name(version: str) -> str | None:
    """Canonical release asset filename for this platform."""
    key = platform_key()
    if key is None:
        return None
    tag = {
        "win_amd64": "win_amd64",
        "manylinux_x86_64": "manylinux_2_28_x86_64",
        "manylinux_aarch64": "manylinux_2_28_aarch64",
        "macosx_arm64": "macosx_11_0_arm64",
    }[key]
    return f"astrbot_plugin_unified_chat-{version}-cp312-abi3-{tag}.whl"
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_native_bootstrap.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add unified_chat/native/bootstrap.py tests/test_native_bootstrap.py
git commit -m "feat(native): platform-to-release-asset mapping in bootstrap"
```

### Task 4: Cached-binary loading + facade wiring (TDD)

**Files:**
- Modify: `unified_chat/native/bootstrap.py`
- Modify: `unified_chat/native/__init__.py`
- Test: `tests/test_native_bootstrap.py`

**Interfaces:**
- Consumes: `unified_chat.utils.path.resolve_data_dir(raw, context) -> Path`.
- Produces: `cache_dir() -> Path`, `try_load_cached(data_dir: Path) -> bool`, `default_cache_dir() -> Path`.

- [ ] **Step 1: Write failing tests** (append to `tests/test_native_bootstrap.py`):

```python
class TestTryLoadCached:
    def _write_stub(self, monkeypatch, calls):
        import types

        def fake_import(path):
            calls.append(path)
            mod = types.ModuleType("stubmod")
            for fn in ("chunk_text", "hash_dedup", "score_importance"):
                setattr(mod, fn, lambda *a, _f=fn: _f)
            monkeypatch.setitem(__import__("sys").modules, "unified_chat._native", mod)
            monkeypatch.setattr(bootstrap, "_bind_facade", lambda mod: None)
            return mod

        return fake_import

    def test_loads_existing_binary(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(bootstrap, "_import_extension", self._write_stub(monkeypatch, calls))
        native = tmp_path / "native"
        native.mkdir()
        (native / "_native.cp312-abi3-win_amd64.pyd").write_bytes(b"x")
        assert bootstrap.try_load_cached(tmp_path) is True
        assert len(calls) == 1

    def test_missing_dir_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            bootstrap, "_import_extension", lambda p: (_ for _ in ()).throw(AssertionError("called"))
        )
        assert bootstrap.try_load_cached(tmp_path / "nope") is False

    def test_corrupt_binary_falls_through(self, tmp_path, monkeypatch):
        def boom(path):
            raise ImportError("bad magic")

        monkeypatch.setattr(bootstrap, "_import_extension", boom)
        native = tmp_path / "native"
        native.mkdir()
        (native / "_native_broken.pyd").write_bytes(b"garbage")
        assert bootstrap.try_load_cached(tmp_path) is False

    def test_prefers_so_then_pyd(self, tmp_path, monkeypatch):
        seen = []
        captured = []

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
```

Also add a facade-level regression test in the same file:

```python
class TestFacadeFallbackIntact:
    def test_fallback_functions_still_importable(self):
        from unified_chat.native import chunk_text, hash_dedup, score_importance

        assert callable(chunk_text)
        assert callable(hash_dedup)
        assert callable(score_importance)
```

- [ ] **Step 2: Run tests, expect FAIL** (`try_load_cached` / `_import_extension` missing)

Run: `uv run pytest tests/test_native_bootstrap.py -q`

- [ ] **Step 3: Implement in bootstrap.py** (append):

```python
FACADE_FUNCTIONS = ("chunk_text", "hash_dedup", "score_importance")


def default_cache_dir() -> Path:
    from ..utils.path import resolve_data_dir

    return resolve_data_dir(None, None) / "native"


def _import_extension(path: Path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("unified_chat._native", str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot build import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["unified_chat._native"] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop("unified_chat._native", None)
        raise
    _bind_facade(module)


def _bind_facade(module) -> None:
    facade = sys.modules.get("unified_chat.native")
    if facade is None:
        return
    for fn_name in FACADE_FUNCTIONS:
        fn = getattr(module, fn_name, None)
        if callable(fn):
            setattr(facade, fn_name, fn)


def try_load_cached(data_dir: Path) -> bool:
    """Load a previously prefetched binary; True on success."""
    native_dir = Path(data_dir) / "native"
    if not native_dir.is_dir():
        return False
    candidates = sorted(native_dir.glob("_native*.so")) + sorted(
        native_dir.glob("_native*.pyd")
    )
    for path in candidates:
        try:
            _import_extension(path)
            return True
        except Exception:
            continue
    return False
```

- [ ] **Step 4: Rewire facade** — replace body of `unified_chat/native/__init__.py` with:

```python
"""Native acceleration facade with Python fallback."""

try:
    from .._native import chunk_text, hash_dedup, score_importance  # type: ignore
except Exception:  # pragma: no cover
    try:
        from .bootstrap import default_cache_dir, try_load_cached

        try_load_cached(default_cache_dir())
        from .._native import chunk_text, hash_dedup, score_importance  # type: ignore
    except Exception:  # pragma: no cover
        from .fallback import (  # noqa: F401
            chunk_text,
            hash_dedup,
            score_importance,
        )

__all__ = ["chunk_text", "score_importance", "hash_dedup"]
```

- [ ] **Step 5: Full suite green**

Run: `uv run pytest tests/test_native_bootstrap.py tests/test_native_fallback.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add unified_chat/native/__init__.py unified_chat/native/bootstrap.py tests/test_native_bootstrap.py
git commit -m "feat(native): load prefetched binaries from data dir before falling back"
```

### Task 5: Prefetch pipeline (TDD)

**Files:**
- Modify: `unified_chat/native/bootstrap.py`
- Test: `tests/test_native_bootstrap.py`

**Interfaces:**
- Consumes: `wheel_asset_name` (Task 3).
- Produces: `plugin_version() -> str`, `prefetch_async(enabled: bool) -> asyncio.Task | None`, internals `_fetch(url)->bytes(awaitable)`, `_extract_native(wheel_bytes, dest)`, `_expected_sha256(sums_text, asset)`.

- [ ] **Step 1: Write failing tests** (append):

```python
import hashlib
import io
import zipfile


def _make_wheel(member: str = "unified_chat/_native.cp312-abi3-x.so") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(member, b"fake-binary-payload")
    return buf.getvalue()


class TestExtractAndChecksum:
    def test_extract_native_member(self, tmp_path):
        wheel = _make_wheel()
        bootstrap._extract_native(wheel, tmp_path)
        out = list(tmp_path.glob("_native*.so"))
        assert len(out) == 1
        assert out[0].read_bytes() == b"fake-binary-payload"

    def test_extract_rejects_wheel_without_native(self, tmp_path):
        with pytest.raises(Exception):
            bootstrap._extract_native(_make_wheel("unified_chat/other.txt"), tmp_path)

    def test_expected_sha256_found(self):
        wheel = _make_wheel()
        digest = hashlib.sha256(wheel).hexdigest()
        sums = f"{digest}  astrbot_plugin_unified_chat-0.1.0-cp312-abi3-win_amd64.whl\n"
        assert bootstrap._expected_sha256(
            sums, "astrbot_plugin_unified_chat-0.1.0-cp312-abi3-win_amd64.whl"
        ) == digest

    def test_expected_sha256_handles_star_and_dirs(self):
        wheel = _make_wheel()
        digest = hashlib.sha256(wheel).hexdigest()
        sums = f"{digest} *dist/astrbot_plugin_unified_chat-0.1.0-cp312-abi3-win_amd64.whl\n"
        assert (
            bootstrap._expected_sha256(
                sums, "astrbot_plugin_unified_chat-0.1.0-cp312-abi3-win_amd64.whl"
            )
            == digest
        )
        assert bootstrap._expected_sha256(sums, "missing.whl") is None


class TestPrefetch:
    async def test_happy_path_caches_binary(self, tmp_path, monkeypatch):
        wheel = _make_wheel()
        digest = hashlib.sha256(wheel).hexdigest()
        asset = "astrbot_plugin_unified_chat-0.1.0-cp312-abi3-win_amd64.whl"

        async def fake_fetch(url):
            return wheel if url.endswith(asset) else (f"{digest}  {asset}\n").encode()

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
            return wheel if url.endswith(asset) else b"deadbeef  wrong\n"

        monkeypatch.setattr(bootstrap, "_fetch", fake_fetch)
        monkeypatch.setattr(bootstrap, "cache_dir", lambda: tmp_path)
        monkeypatch.setattr(bootstrap, "plugin_version", lambda: "0.1.0")
        monkeypatch.setattr(bootstrap.platform, "machine", lambda: "AMD64")
        monkeypatch.setattr(bootstrap.sys, "platform", "win32")

        await bootstrap.prefetch()
        assert not list(tmp_path.glob("_native*"))

    async def test_network_error_silent(self, tmp_path, monkeypatch):
        async def boom(url):
            raise OSError("network down")

        monkeypatch.setattr(bootstrap, "_fetch", boom)
        monkeypatch.setattr(bootstrap, "cache_dir", lambda: tmp_path)
        monkeypatch.setattr(bootstrap, "plugin_version", lambda: "0.1.0")
        monkeypatch.setattr(bootstrap.platform, "machine", lambda: "AMD64")
        monkeypatch.setattr(bootstrap.sys, "platform", "win32")

        await bootstrap.prefetch()  # must not raise
        assert not list(tmp_path.glob("_native*"))

    async def test_prefetch_async_disabled(self, monkeypatch):
        assert bootstrap.prefetch_async(False) is None

    async def test_prefetch_async_when_native_present(self, monkeypatch):
        import types

        monkeypatch.setitem(
            __import__("sys").modules, "unified_chat._native", types.ModuleType("x")
        )
        assert bootstrap.prefetch_async(True) is None
```

- [ ] **Step 2: Run, expect FAIL** (attributes missing)

Run: `uv run pytest tests/test_native_bootstrap.py -q`

- [ ] **Step 3: Implement** (append to bootstrap.py):

```python
import asyncio
import contextlib
import hashlib
import hmac
import io
import re
import urllib.request
import zipfile


def cache_dir() -> Path:
    return default_cache_dir()


def plugin_version() -> str:
    meta = Path(__file__).resolve().parents[2] / "metadata.yaml"
    text = meta.read_text(encoding="utf-8")
    match = re.search(r"(?m)^version:\s*([^\s#]+)", text)
    if not match:
        raise ValueError("metadata.yaml has no version line")
    return match.group(1).strip("\"'")


def _expected_sha256(sums_text: str, asset: str) -> str | None:
    for line in sums_text.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        name = parts[1].strip().lstrip("*")
        if name.rsplit("/", 1)[-1] == asset:
            return parts[0].lower()
    return None


def _extract_native(wheel_bytes: bytes, dest: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as zf:
        members = [
            name
            for name in zf.namelist()
            if Path(name).name.startswith("_native")
            and name.endswith((".so", ".pyd"))
        ]
        if not members:
            raise ValueError("no native extension member inside wheel")
        payload = zf.read(members[0])
    target = dest / Path(members[0]).name
    dest.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(payload)
    tmp.replace(target)


async def _fetch(url: str) -> bytes:
    def _get() -> bytes:
        request = urllib.request.Request(
            url, headers={"User-Agent": "unified-chat-bootstrap"}
        )
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as resp:  # noqa: S310
            data = resp.read(MAX_WHEEL_BYTES + 1)
        if len(data) > MAX_WHEEL_BYTES:
            raise ValueError("asset exceeds size cap")
        return data

    return await asyncio.to_thread(_get)


async def prefetch() -> None:
    log = _logger()
    try:
        version = plugin_version()
        asset = wheel_asset_name(version)
        if asset is None:
            return
        dest = cache_dir()
        if dest.is_dir() and any(dest.glob("_native*")):
            return
        base = f"{RELEASE_BASE}/v{version}"
        wheel_bytes = await _fetch(f"{base}/{asset}")
        sums_bytes = await _fetch(f"{base}/SHA256SUMS")
        expected = _expected_sha256(sums_bytes.decode("utf-8", "replace"), asset)
        actual = hashlib.sha256(wheel_bytes).hexdigest()
        if expected is None or not hmac.compare_digest(actual, expected):
            _log(log, f"checksum mismatch for {asset}; skipping install")
            return
        _extract_native(wheel_bytes, dest)
        _log(log, f"native binary cached ({asset}); restart to activate")
    except Exception as exc:
        _log(log, f"prefetch skipped: {exc}")


def _logger():
    try:
        from astrbot.api import logger  # type: ignore

        return logger
    except Exception:
        import logging

        return logging.getLogger("unified_chat")


def _log(logger_obj, message: str) -> None:
    with contextlib.suppress(Exception):
        logger_obj.info(f"[unified_chat] {message}")


def prefetch_async(enabled: bool) -> asyncio.Task | None:
    """Schedule background prefetch; None when disabled/satisfied."""
    if not enabled:
        return None
    try:
        import unified_chat._native  # noqa: F401

        return None
    except Exception:
        pass
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    return loop.create_task(prefetch())
```

- [ ] **Step 4: Run bootstrap tests**

Run: `uv run pytest tests/test_native_bootstrap.py -q`
Expected: PASS

- [ ] **Step 5: Lifecycle trigger** — modify `unified_chat/core/lifecycle.py`:

In `PluginLifecycle.__init__` add field:

```python
        self._prefetch_task: Any | None = None
```

At end of `on_load` (before final `except`, after MigrationService block) append:

```python
            try:
                from ..native import bootstrap

                self._prefetch_task = bootstrap.prefetch_async(
                    config.native_autodownload
                )
            except Exception:
                pass
```

In `on_unload`, before `self._status = "unloaded"` add:

```python
        if self._prefetch_task is not None:
            with contextlib.suppress(Exception):
                self._prefetch_task.cancel()
            self._prefetch_task = None
```

- [ ] **Step 6: Full suite**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all PASS, no lint errors

- [ ] **Step 7: Commit**

```bash
git add unified_chat/native/bootstrap.py unified_chat/core/lifecycle.py tests/test_native_bootstrap.py
git commit -m "feat(native): background wheel prefetch with sha256 verification"
```

### Task 6: Config key + metadata fix

**Files:**
- Modify: `_conf_schema.json`, `unified_chat/config.py`, `metadata.yaml`
- Test: `tests/test_config_validation.py` (extend)

**Interfaces:**
- Produces: `PluginConfig.native_autodownload: bool = True`.

- [ ] **Step 1: Failing test** — append to `tests/test_config_validation.py`:

```python
def test_native_autodownload_roundtrip():
    from unified_chat.config import PluginConfig

    cfg = PluginConfig.from_dict({"native_autodownload": False})
    assert cfg.native_autodownload is False
    assert PluginConfig.from_dict({}).native_autodownload is True
    assert PluginConfig.from_dict({"native_autodownload": "yes"}).native_autodownload is True
    assert "native_autodownload" in PluginConfig.from_dict({}).to_dict()
```

- [ ] **Step 2: Run, expect FAIL**: `uv run pytest tests/test_config_validation.py -q`

- [ ] **Step 3: Implement**

`_conf_schema.json` — add top-level key:

```json
  "native_autodownload": {
    "description": "启动时自动下载匹配平台的原生加速库（失败自动回退纯Python）",
    "type": "bool",
    "default": true
  },
```

`unified_chat/config.py`: add `"native_autodownload": True` to `DEFAULTS`; field `native_autodownload: bool = True` on dataclass; in `from_dict` return kwargs add:

```python
            native_autodownload=bool(pick("native_autodownload", d["native_autodownload"])),
```

and in `to_dict`:

```python
            "native_autodownload": self.native_autodownload,
```

`metadata.yaml`: replace repo line:

```yaml
repo: https://github.com/zyzh2002/astrbot_plugin_unified_chat
```

- [ ] **Step 4: Tests pass + scaffold check**

Run: `uv run pytest tests/test_config_validation.py tests/test_scaffold.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add _conf_schema.json unified_chat/config.py metadata.yaml tests/test_config_validation.py
git commit -m "feat(config): native_autodownload toggle and correct repo metadata"
```

### Task 7: Final verification and merge

- [ ] **Step 1:** `uv sync && uv run pytest -q && uv run ruff check . && cargo test -p unified_chat_native` — all green
- [ ] **Step 2:** Rebase onto main, push branch, open fast-forward merge to main, push main
- [ ] **Step 3:** Watch CI on main (`gh run watch`) until green
