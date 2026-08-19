"""Shared test fixtures and AstrBot API stubs."""

import sys
import types

from pydantic import Field
from pydantic.dataclasses import dataclass


def _install_astrbot_tool_stub():
    if "astrbot.core.agent.tool" in sys.modules:
        return
    try:
        import astrbot.core.agent.tool  # noqa: F401

        return  # real AstrBot available (e.g. Docker); keep it
    except ImportError:
        pass
    pkg = types.ModuleType("astrbot.core.agent.tool")

    @dataclass
    class FunctionTool:
        name: str
        description: str
        parameters: dict = Field(default_factory=dict)
        handler: object = None
        active: bool = True

        async def call(self, context, **kwargs):
            raise NotImplementedError(
                "FunctionTool.call() must be implemented by subclasses or set a handler."
            )

    @dataclass
    class ToolSet:
        tools: list = Field(default_factory=list)

        def add_tool(self, tool):
            for i, existing in enumerate(self.tools):
                if existing.name == tool.name:
                    existing_active = bool(getattr(existing, "active", True))
                    new_active = bool(getattr(tool, "active", True))
                    if new_active or not existing_active:
                        self.tools[i] = tool
                    return
            self.tools.append(tool)

        def get_tool(self, name):
            for tool in self.tools:
                if tool.name == name:
                    return tool
            return None

    pkg.FunctionTool = FunctionTool
    pkg.ToolSet = ToolSet
    sys.modules["astrbot.core.agent.tool"] = pkg


_install_astrbot_tool_stub()
