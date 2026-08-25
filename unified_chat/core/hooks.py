"""LLM request hooks: unified tool injection."""

from __future__ import annotations

import contextlib
from typing import Any


async def inject_kb_tool(event: Any, req: Any, config: Any, rag_service: Any) -> None:
    """Inject the plugin-scoped knowledge base tool into req.func_tool.

    Agentic-only. Never raises; failures are logged and skipped.
    """
    if not config.rag_agentic or not config.rag_kbs:
        return
    try:
        tool = rag_service.build_kb_tool(config.rag_kbs)
        if tool is None:
            return
        func_tool = getattr(req, "func_tool", None)
        if func_tool is None:
            from astrbot.core.agent.tool import ToolSet  # type: ignore

            func_tool = ToolSet()
            req.func_tool = func_tool
        if func_tool.get_tool(tool.name) is not None:
            return
        func_tool.add_tool(tool)
    except Exception:
        with contextlib.suppress(Exception):
            from astrbot.api import logger  # type: ignore

            logger.error("[unified_chat] inject_kb_tool failed", exc_info=True)


async def inject_social_context(event: Any, req: Any, config: Any, chat_service: Any) -> None:
    """Append a per-group social context system message to req.contexts.

    Gated by enable_conversation_enhance. Never raises.
    """
    if not config.enable_conversation_enhance:
        return
    try:
        social = chat_service.social_context(event)
        if not social:
            return
        contexts = getattr(req, "contexts", None)
        if contexts is None:
            contexts = []
            req.contexts = contexts
        contexts.append({"role": "system", "content": social})
    except Exception:
        with contextlib.suppress(Exception):
            from astrbot.api import logger  # type: ignore

            logger.error("[unified_chat] inject_social_context failed", exc_info=True)


async def inject_memories(event: Any, req: Any, config: Any, memory_service: Any) -> None:
    """Append retrieved memories as a system message to req.contexts.

    Gated by enable_persistent_memory. Never raises.
    """
    if not config.enable_persistent_memory:
        return
    try:
        text = getattr(event, "message_str", "")
        if not text:
            return
        memories = await memory_service.retrieve(text)
        if not memories:
            return
        contexts = getattr(req, "contexts", None)
        if contexts is None:
            contexts = []
            req.contexts = contexts
        contexts.append({"role": "system", "content": f"Relevant memories:\n{memories}"})
    except Exception:
        with contextlib.suppress(Exception):
            from astrbot.api import logger  # type: ignore

            logger.error("[unified_chat] inject_memories failed", exc_info=True)


async def inject_memory_tools(event: Any, req: Any, config: Any, memory_service: Any) -> None:
    """Add agent memory recall/memorize tools; never raises."""
    try:
        from ..services.memory_tools import inject_memory_tools as _inject

        await _inject(event, req, config, memory_service)
    except Exception:
        with contextlib.suppress(Exception):
            from astrbot.api import logger  # type: ignore

            logger.error("[unified_chat] inject_memory_tools failed", exc_info=True)


async def inject_learning_block(event: Any, req: Any, config: Any, services: dict) -> None:
    """Append slang/affinity/mood context as one system message. Never raises."""
    try:
        from ..services.inject_composer import compose_learning_block

        block = await compose_learning_block(
            event,
            config,
            services.get("slang_terms", []),
            services.get("affinity_score"),
            services.get("mood_scalar", 0.0),
        )
        if not block:
            return
        contexts = getattr(req, "contexts", None)
        if contexts is None:
            contexts = []
            req.contexts = contexts
        contexts.append({"role": "system", "content": block})
    except Exception:
        with contextlib.suppress(Exception):
            from astrbot.api import logger  # type: ignore

            logger.error("[unified_chat] inject_learning_block failed", exc_info=True)
