"""RAG service: plugin-scoped knowledge base query tool (agentic only)."""

from __future__ import annotations

from typing import Any

TOOL_NAME = "unified_chat_kb_query"


class RagService:
    """Builds a FunctionTool bound to the plugin's rag_kbs.

    All astrbot/pydantic imports are lazy and guarded so the plugin
    stays loadable even when the AstrBot tool API is unavailable.
    """

    def __init__(self, context: Any):
        self.context = context

    async def exclude_kb(
        self,
        kb_names: list[str],
        excluded_kb_name: str,
    ) -> list[str]:
        manager = getattr(self.context, "kb_manager", None)
        if manager is None:
            return [name for name in kb_names if name != excluded_kb_name]
        excluded = {excluded_kb_name}
        try:
            helper = await manager.get_kb_by_name(excluded_kb_name)
            if helper is not None:
                for attr in ("kb_id", "id", "kb_name", "name"):
                    value = getattr(helper, attr, None)
                    if value:
                        excluded.add(str(value))
        except Exception:
            pass
        return [name for name in kb_names if str(name) not in excluded]

    def build_kb_tool(self, kb_names: list[str], top_m_final: int = 5) -> Any | None:
        if not kb_names:
            return None
        kb_manager = getattr(self.context, "kb_manager", None)
        if kb_manager is None:
            return None
        try:
            from astrbot.core.agent.tool import FunctionTool  # type: ignore
            from pydantic import Field
            from pydantic.dataclasses import dataclass
        except Exception:
            return None

        bound_names = list(kb_names)

        @dataclass
        class UnifiedChatKbQueryTool(FunctionTool):  # type: ignore[misc, valid-type]
            name: str = TOOL_NAME
            description: str = (
                "Query the plugin-configured knowledge base(s) for facts, "
                "background knowledge or previously indexed content. "
                "Only send a concise keyword query."
            )
            parameters: dict = Field(
                default_factory=lambda: {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "A concise keyword query.",
                        },
                    },
                    "required": ["query"],
                }
            )

            async def call(self, context, **kwargs):
                query = str(kwargs.get("query", "")).strip()
                if not query:
                    return "error: Query parameter is empty."
                try:
                    result = await kb_manager.retrieve(
                        query=query,
                        kb_names=bound_names,
                        top_k_fusion=20,
                        top_m_final=top_m_final,
                    )
                except Exception as e:
                    return f"error: {e}"
                if not result or not isinstance(result, dict):
                    return "No relevant knowledge found."
                text = str(result.get("context_text", "")).strip()
                return text or "No relevant knowledge found."

        return UnifiedChatKbQueryTool()
