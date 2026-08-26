"""Agent-facing memory tools: recall and memorize (agentic mode)."""

from __future__ import annotations

import contextlib
from typing import Any

from .memory_classifier import classify_memory

RECALL_TOOL_NAME = "unified_chat_memory_recall"
MEMORIZE_TOOL_NAME = "unified_chat_memory_memorize"


def build_memory_tools(memory_service: Any, session_id: str) -> list[Any]:
    """Build recall/memorize FunctionTools bound to the memory service.

    Returns [] when the astrbot tool API is unavailable.
    """
    try:
        from astrbot.core.agent.tool import FunctionTool  # type: ignore
        from pydantic import Field
        from pydantic.dataclasses import dataclass
    except Exception:
        return []

    service = memory_service

    @dataclass
    class MemoryRecallTool(FunctionTool):  # type: ignore[misc, valid-type]
        name: str = RECALL_TOOL_NAME
        description: str = (
            "Recall long-term memories relevant to a keyword query. Use when "
            "the user asks what the bot remembers or context is ambiguous. "
            "Prefer short high-signal keywords."
        )
        parameters: dict = Field(
            default_factory=lambda: {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Concise keywords."},
                    "k": {
                        "type": "integer",
                        "description": "Max results, default 5.",
                    },
                },
                "required": ["query"],
            }
        )

        async def call(self, context, **kwargs):
            query = str(kwargs.get("query", "")).strip()
            if not query:
                return "error: empty query"
            try:
                k = int(kwargs.get("k", 5) or 5)
                hits = await service.retrieve_hybrid(
                    query,
                    session_id=session_id,
                    top_k=max(1, min(k, 20)),
                )
            except Exception as exc:
                return f"error: {exc}"
            if not hits:
                return "No matching memories."
            return "\n".join(
                f"[{m.id}] ({m.memory_type}) {m.content}" for m in hits
            )

    @dataclass
    class MemoryMemorizeTool(FunctionTool):  # type: ignore[misc, valid-type]
        name: str = MEMORIZE_TOOL_NAME
        description: str = (
            "Store one durable fact/preference/plan into long-term memory. "
            "Use when the user explicitly asks to remember something or a "
            "stable fact appears."
        )
        parameters: dict = Field(
            default_factory=lambda: {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "One concise durable statement.",
                    },
                },
                "required": ["text"],
            }
        )

        async def call(self, context, **kwargs):
            text = str(kwargs.get("text", "")).strip()
            if len(text) < 4:
                return "error: text too short to memorize"
            try:
                mem_id = await service.memorize_text(
                    text,
                    source="agent",
                    mtype=classify_memory(text),
                    session_id=session_id,
                )
            except Exception as exc:
                return f"error: {exc}"
            return f"memorized as id={mem_id}"

    return [MemoryRecallTool(), MemoryMemorizeTool()]


async def inject_memory_tools(event: Any, req: Any, config: Any, memory_service: Any) -> None:
    """Add memory tools into req.func_tool; never raises."""
    if not config.enable_persistent_memory or not config.rag_agentic:
        return
    try:
        session_id = getattr(event, "unified_msg_origin", "") or ""
        tools = build_memory_tools(memory_service, session_id)
        if not tools:
            return
        func_tool = getattr(req, "func_tool", None)
        if func_tool is None:
            from astrbot.core.agent.tool import ToolSet  # type: ignore

            func_tool = ToolSet()
            req.func_tool = func_tool
        for tool in tools:
            if func_tool.get_tool(tool.name) is None:
                func_tool.add_tool(tool)
    except Exception:
        with contextlib.suppress(Exception):
            from astrbot.api import logger  # type: ignore

            logger.error("[unified_chat] inject_memory_tools failed", exc_info=True)
