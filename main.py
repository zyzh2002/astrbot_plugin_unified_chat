"""AstrBot plugin entrypoint.

Single Star subclass delegating to internal lifecycle. No business logic here.
"""

import contextlib

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

try:
    # Inside AstrBot the plugin is imported as data.plugins.<name> and the
    # plugin directory is NOT on sys.path; use package-relative imports.
    from .unified_chat.core.lifecycle import PluginLifecycle
except ImportError:  # pragma: no cover - local dev (module imported as top-level)
    from unified_chat.core.lifecycle import PluginLifecycle  # type: ignore


class UnifiedChatPlugin(Star):
    """Unified Chat — conversation, memory, learning and RAG."""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context, config)
        # Do not raise in __init__; defer to initialize().
        self._lifecycle = PluginLifecycle(self, context, config)
        self._initialized = False

    async def initialize(self):
        """Called after handler binding."""
        try:
            await self._lifecycle.on_load()
            self._initialized = True
        except Exception as e:  # pragma: no cover
            from astrbot.api import logger

            logger.error(f"[unified_chat] initialize failed: {e}", exc_info=True)

    async def terminate(self):
        """Cleanup before unload."""
        with contextlib.suppress(Exception):
            await self._lifecycle.on_unload()

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        if not self._initialized:
            return
        await self._lifecycle.handle_message(event)

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req):
        if not self._initialized:
            return
        await self._lifecycle.handle_llm_request(event, req)

    @filter.command("unified_status")
    async def unified_status(self, event: AstrMessageEvent):
        """Show plugin status."""
        if not self._initialized:
            yield event.plain_result("[UnifiedChat] not initialized")
            return
        status = await self._lifecycle.get_status_async()
        yield event.plain_result(f"[UnifiedChat] {status}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("unified_migrate")
    async def unified_migrate(self, event: AstrMessageEvent, kb_name: str = ""):
        """Migrate knowledge base to new embedding dimension. Admin only."""
        if not self._initialized:
            yield event.plain_result("Plugin not initialized")
            return
        result = await self._lifecycle.migrate_kb(event, kb_name.strip())
        yield event.plain_result(result)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("umem")
    async def umem(self, event: AstrMessageEvent, action: str = "", arg: str = ""):
        """Memory management: status/search/forget/backup/reset."""
        if not self._initialized:
            yield event.plain_result("[umem] Plugin not initialized")
            return
        result = await self._lifecycle.umem(event, action.strip(), arg.strip())
        yield event.plain_result(result)


    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("uslang")
    async def uslang(self, event: AstrMessageEvent, action: str = "", arg: str = ""):
        """Slang management: list/confirm/deny."""
        if not self._initialized:
            yield event.plain_result("[uslang] Plugin not initialized")
            return
        result = await self._lifecycle.uslang(action.strip(), arg.strip())
        yield event.plain_result(result)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("upersona")
    async def upersona(self, event: AstrMessageEvent, action: str = "", arg: str = ""):
        """Persona suggestion review chain."""
        if not self._initialized:
            yield event.plain_result("[upersona] Plugin not initialized")
            return
        result = await self._lifecycle.upersona(action.strip(), arg.strip())
        yield event.plain_result(result)
