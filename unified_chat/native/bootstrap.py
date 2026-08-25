"""Native binary bootstrap: cached loading and release prefetch.

Fail-silent by contract: no function here may raise out of the plugin
startup path; all failures degrade to the pure-Python fallback.
"""

from __future__ import annotations

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
