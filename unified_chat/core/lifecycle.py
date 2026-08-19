"""Plugin lifecycle: init, message handling, llm hook, migration."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.star import Context, Star

from unified_chat.config import PluginConfig


class PluginLifecycle:
    """Orchestrates all internal services. Keeps main.py thin."""

    def __init__(self, plugin: Star, context: Context):
        self.plugin = plugin
        self.context = context
        self._status = "created"
        self._config: PluginConfig | None = None
        self._data_dir: Path | None = None

    async def on_load(self):
        try:
            raw: dict = {}
            # Try several defensively-known config sources.
            for getter in (
                lambda: getattr(self.context, "get_config", lambda: None)(),
                lambda: getattr(self.plugin, "config", None),
                lambda: getattr(self.context, "config", None),
            ):
                try:
                    val = getter()
                    if isinstance(val, dict) and val:
                        raw = val
                        break
                    if isinstance(val, dict):
                        raw = val
                except Exception:
                    continue
            if not isinstance(raw, dict):
                raw = {}

            from unified_chat.utils.path import resolve_data_dir

            data_dir = resolve_data_dir(raw, self.context)
            config = PluginConfig.from_dict(raw, data_dir=str(data_dir))
            self._config = config
            self._data_dir = data_dir

            from unified_chat.storage.database import get_engine

            db_path = data_dir / "unified_chat.db"
            await get_engine(db_path)
            self._status = "loaded"
        except Exception as e:  # pragma: no cover - defensive
            try:
                from astrbot.api import logger  # type: ignore

                logger.error(f"[unified_chat] on_load failed: {e}", exc_info=True)
            except Exception:
                pass
            self._status = f"load_failed: {e}"

    async def on_unload(self):
        with contextlib.suppress(Exception):
            from unified_chat.storage.database import close_engine

            await close_engine()
        self._status = "unloaded"

    async def handle_message(self, event: AstrMessageEvent):
        # TODO: pipeline: dedup -> filter -> affection -> memory -> learning (background tasks)
        _ = event

    async def handle_llm_request(self, event: AstrMessageEvent, req):
        # TODO: agentic RAG: inject KnowledgeBaseQueryTool if rag_agentic
        _ = event
        _ = req

    def get_status(self) -> str:
        if self._config is not None and self._data_dir is not None:
            return (
                f"{self._status} | data_dir={self._data_dir} | "
                f"rag_kbs={self._config.rag_kbs} agentic={self._config.rag_agentic} "
                f"mem_days={self._config.memory_cleanup_days}"
            )
        return self._status

    async def migrate_kb(self, event: AstrMessageEvent, kb_name: str) -> str:
        if not kb_name:
            return "Usage: /unified_migrate <kb_name>"
        # TODO: implement embedding dimension migration (spec 006)
        _ = event
        return f"Migration for '{kb_name}' not yet implemented (stub)."
