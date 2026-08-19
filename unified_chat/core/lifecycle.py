"""Plugin lifecycle: init, message handling, llm hook, migration."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.star import Context, Star


class PluginLifecycle:
    """Orchestrates all internal services. Keeps main.py thin."""

    def __init__(self, plugin: Star, context: Context):
        self.plugin = plugin
        self.context = context
        self._status = "created"

    async def on_load(self):
        self._status = "loaded"
        # TODO: init config, storage, services, cron

    async def on_unload(self):
        self._status = "unloaded"
        # TODO: terminate services, close db

    async def handle_message(self, event: AstrMessageEvent):
        # TODO: pipeline: dedup -> filter -> affection -> memory -> learning (background tasks)
        _ = event

    async def handle_llm_request(self, event: AstrMessageEvent, req):
        # TODO: agentic RAG: inject KnowledgeBaseQueryTool if rag_agentic
        _ = event
        _ = req

    def get_status(self) -> str:
        return self._status

    async def migrate_kb(self, event: AstrMessageEvent, kb_name: str) -> str:
        if not kb_name:
            return "Usage: /unified_migrate <kb_name>"
        # TODO: implement embedding dimension migration
        _ = event
        return f"Migration for '{kb_name}' not yet implemented (stub)."
