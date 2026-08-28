"""Path resolution for data_dir."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def resolve_data_dir(raw: dict[str, Any] | None, context: Any) -> Path:
    """Resolve data_dir with StarTools fallback chain.

    Order:
    1. raw["data_dir"] if non-empty
    2. StarTools.get_data_dir()
    3. get_astrbot_data_path() / plugin_data / name
    4. ./data/plugin_data/unified_chat
    """
    raw = raw or {}
    # 1. explicit override
    override = raw.get("data_dir")
    if isinstance(override, str) and override.strip():
        p = Path(override.strip()).expanduser()
        try:
            p.mkdir(parents=True, exist_ok=True)
            return p.resolve()
        except Exception:
            pass

    # 2. StarTools (AstrBot resolves get_data_dir against the caller frame,
    # which is this helper — so the plugin name must be passed explicitly)
    try:
        from astrbot.api.star import StarTools  # type: ignore

        p = Path(
            StarTools.get_data_dir(plugin_name="astrbot_plugin_unified_chat")
        )
        p.mkdir(parents=True, exist_ok=True)
        return p.resolve()
    except Exception:
        pass

    # 3. get_astrbot_data_path
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path  # type: ignore

        p = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_unified_chat"
        p.mkdir(parents=True, exist_ok=True)
        return p.resolve()
    except Exception:
        pass

    # 4. local fallback (never plugin source dir); same canonical name
    p = Path("data/plugin_data/astrbot_plugin_unified_chat")
    try:
        p.mkdir(parents=True, exist_ok=True)
        return p.resolve()
    except Exception:
        return p.resolve()
