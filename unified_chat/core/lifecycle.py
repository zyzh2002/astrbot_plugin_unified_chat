"""Plugin lifecycle: init, message handling, llm hook, migration."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
        self._rag_service: Any | None = None
        self._chat_service: Any | None = None
        self._pipeline: Any | None = None
        self._memory_service: Any | None = None
        self._cron: Any | None = None

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

            from unified_chat.services.rag_service import RagService

            self._rag_service = RagService(self.context)

            from unified_chat.core.pipeline import MessagePipeline
            from unified_chat.services.chat_service import ChatService

            self._chat_service = ChatService()

            from unified_chat.services.memory_service import MemoryService

            self._memory_service = MemoryService(self.context, config)
            await self._memory_service.ensure_memory_kb()

            from unified_chat.services.learning_service import LearningService

            self._learning_service = LearningService(self.context, config)

            self._pipeline = MessagePipeline(
                config, self._chat_service, self._memory_service, self._learning_service
            )

            from unified_chat.core.cron import MemoryCleanupCron

            self._cron = MemoryCleanupCron(self._memory_service)
            self._cron.start()
        except Exception as e:  # pragma: no cover - defensive
            try:
                from astrbot.api import logger  # type: ignore

                logger.error(f"[unified_chat] on_load failed: {e}", exc_info=True)
            except Exception:
                pass
            self._status = f"load_failed: {e}"

    async def on_unload(self):
        with contextlib.suppress(Exception):
            if self._cron is not None:
                self._cron.stop()
        with contextlib.suppress(Exception):
            from unified_chat.storage.database import close_engine

            await close_engine()
        self._status = "unloaded"

    async def handle_message(self, event: AstrMessageEvent):
        if self._pipeline is None:
            return
        try:
            await self._pipeline.process(event)
        except Exception:
            with contextlib.suppress(Exception):
                from astrbot.api import logger  # type: ignore

                logger.error("[unified_chat] handle_message failed", exc_info=True)

    async def handle_llm_request(self, event: AstrMessageEvent, req):
        if self._config is None:
            return
        if self._rag_service is not None:
            try:
                from unified_chat.core.hooks import inject_kb_tool

                await inject_kb_tool(event, req, self._config, self._rag_service)
            except Exception:
                with contextlib.suppress(Exception):
                    from astrbot.api import logger  # type: ignore

                    logger.error("[unified_chat] handle_llm_request failed", exc_info=True)
        if self._chat_service is not None:
            try:
                from unified_chat.core.hooks import inject_social_context

                await inject_social_context(event, req, self._config, self._chat_service)
            except Exception:
                with contextlib.suppress(Exception):
                    from astrbot.api import logger  # type: ignore

                    logger.error("[unified_chat] inject_social_context failed", exc_info=True)
        if self._memory_service is not None:
            try:
                from unified_chat.core.hooks import inject_memories

                await inject_memories(event, req, self._config, self._memory_service)
            except Exception:
                with contextlib.suppress(Exception):
                    from astrbot.api import logger  # type: ignore

                    logger.error("[unified_chat] inject_memories failed", exc_info=True)

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
