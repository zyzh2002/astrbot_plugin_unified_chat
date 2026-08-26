"""Native binary bootstrap: cached loading and release prefetch.

Fail-silent by contract: no function here may raise out of the plugin
startup path; all failures degrade to the pure-Python fallback.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import platform
import sys
from pathlib import Path

PLUGIN_REPO_SLUG = "zyzh2002/astrbot_plugin_unified_chat"
RELEASE_BASE = f"https://github.com/{PLUGIN_REPO_SLUG}/releases/download"
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

FACADE_FUNCTIONS = ("chunk_text", "hash_dedup", "score_importance")


def default_cache_dir() -> Path:
    from ..utils.path import resolve_data_dir

    return resolve_data_dir(None, None)


def _module_names() -> tuple[str, str]:
    facade_name = __package__ or "unified_chat.native"
    package_name = facade_name.rsplit(".", 1)[0]
    return f"{package_name}._native", facade_name


def _import_extension(path: Path) -> None:
    import importlib.util

    extension_name, _facade_name = _module_names()
    spec = importlib.util.spec_from_file_location(extension_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot build import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[extension_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(extension_name, None)
        raise
    _bind_facade(module)


def _bind_facade(module) -> None:
    _extension_name, facade_name = _module_names()
    facade = sys.modules.get(facade_name)
    if facade is None:
        return
    for fn_name in FACADE_FUNCTIONS:
        fn = getattr(module, fn_name, None)
        if callable(fn):
            setattr(facade, fn_name, fn)


def try_load_cached(data_dir: Path) -> bool:
    """Load a previously prefetched binary; True on success."""
    native_dir = Path(data_dir) / "native" / plugin_version()
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


def cache_dir() -> Path:
    return default_cache_dir() / "native" / plugin_version()


def plugin_version() -> str:
    import re

    meta = Path(__file__).resolve().parents[2] / "metadata.yaml"
    text = meta.read_text(encoding="utf-8")
    match = re.search(r"(?m)^version:\s*([^\s#]+)", text)
    if not match:
        raise ValueError("metadata.yaml has no version line")
    return match.group(1).strip("\"'")


def _expected_sha256(sums_text: str, asset: str) -> str | None:
    import re as _re

    for line in sums_text.splitlines():
        line = line.strip()
        if not line:
            continue
        # BSD style: "SHA256 (name) = hash"
        match = _re.fullmatch(r"SHA256\s+\((.+?)\)\s*=\s*([0-9a-fA-F]{64})", line)
        if match:
            if match.group(1).rsplit("/", 1)[-1] == asset:
                return match.group(2).lower()
            continue
        # GNU/standard style: "<hash>  <name>" (optional leading "*")
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        name = parts[1].strip().lstrip("*")
        if name.rsplit("/", 1)[-1] == asset and _re.fullmatch(
            r"[0-9a-fA-F]{64}", parts[0]
        ):
            return parts[0].lower()
    return None


def _extract_native(wheel_bytes: bytes, dest: Path) -> None:
    import io
    import zipfile

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
    import urllib.request

    def _get() -> bytes:
        request = urllib.request.Request(
            url, headers={"User-Agent": "unified-chat-bootstrap"}
        )
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as resp:
            data = resp.read(MAX_WHEEL_BYTES + 1)
        if len(data) > MAX_WHEEL_BYTES:
            raise ValueError("asset exceeds size cap")
        return data

    return await asyncio.to_thread(_get)


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


async def prefetch() -> None:
    log = _logger()
    try:
        version = plugin_version()
        asset = wheel_asset_name(version)
        if asset is None:
            return
        dest = cache_dir()
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


def prefetch_async(enabled: bool) -> asyncio.Task | None:
    """Schedule background prefetch; None when disabled or already satisfied."""
    if not enabled:
        return None
    try:
        import importlib

        extension_name, _facade_name = _module_names()
        importlib.import_module(extension_name)

        return None
    except Exception:
        pass
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    return loop.create_task(prefetch())
