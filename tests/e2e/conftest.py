"""E2E sandbox: lets tests import the plugin exactly like AstrBot does.

Builds `<sandbox>/data/plugins/astrbot_plugin_unified_chat/` (source copy)
and puts the sandbox root on sys.path so
``import data.plugins.astrbot_plugin_unified_chat.main`` works on any
machine with ``astrbot`` importable — no Docker or /AstrBot checkout needed.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

_PLUGIN_PKG = "astrbot_plugin_unified_chat"
_COPIED_FILES = ("main.py", "metadata.yaml", "_conf_schema.json")
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")


def _build_sandbox() -> Path:
    src_root = Path(__file__).resolve().parents[2]
    sandbox = Path(tempfile.gettempdir()) / f"uc-e2e-{os.getpid()}"
    pkg_dst = sandbox / "data" / "plugins" / _PLUGIN_PKG
    if pkg_dst.exists():
        shutil.rmtree(sandbox, ignore_errors=True)
    pkg_dst.mkdir(parents=True)
    for name in _COPIED_FILES:
        shutil.copy2(src_root / name, pkg_dst / name)
    shutil.copytree(
        src_root / "unified_chat",
        pkg_dst / "unified_chat",
        dirs_exist_ok=True,
        ignore=_IGNORE,
    )
    return sandbox


_SANDBOX = _build_sandbox()
if str(_SANDBOX) not in sys.path:
    sys.path.insert(0, str(_SANDBOX))
