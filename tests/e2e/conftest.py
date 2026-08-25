"""E2E sandbox: lets tests import the plugin exactly like AstrBot does.

Builds `<sandbox>/data/plugins/astrbot_plugin_unified_chat/` with only the
plugin entrypoint files and puts both the sandbox root and this repo root on
sys.path. The entry's absolute-import fallback then binds to the single
`unified_chat` package from the repo, avoiding a duplicate-copy import that
would clash on SQLModel's global table metadata.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

_PLUGIN_PKG = "astrbot_plugin_unified_chat"
_COPIED_FILES = ("main.py", "metadata.yaml", "_conf_schema.json")


def _build_sandbox() -> Path:
    src_root = Path(__file__).resolve().parents[2]
    sandbox = Path(tempfile.gettempdir()) / f"uc-e2e-{os.getpid()}"
    pkg_dst = sandbox / "data" / "plugins" / _PLUGIN_PKG
    if pkg_dst.exists():
        shutil.rmtree(sandbox, ignore_errors=True)
    pkg_dst.mkdir(parents=True)
    for name in _COPIED_FILES:
        shutil.copy2(src_root / name, pkg_dst / name)
    return sandbox


_SANDBOX = _build_sandbox()
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
for _path in (_REPO_ROOT, str(_SANDBOX)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
