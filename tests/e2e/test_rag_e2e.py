"""E2E contract checks for the real AstrBot tool API (run inside Docker).

Skipped outside AstrBot runtime: `astrbot` is not installed in the dev venv.

NOTE: import astrbot.api FIRST (AstrBot import graph is order-sensitive).
"""

import pytest

pytest.importorskip("astrbot.api")
astrbot_tool = pytest.importorskip("astrbot.core.agent.tool")
FunctionTool = astrbot_tool.FunctionTool
ToolSet = astrbot_tool.ToolSet


def test_real_functiontool_matches_stub_contract():
    tool = FunctionTool(name="t", description="d", parameters={"type": "object", "properties": {}})
    assert tool.name == "t"
    assert tool.description == "d"
    ts = ToolSet()
    ts.add_tool(tool)
    assert ts.get_tool("t") is tool


def test_real_toolset_dedups_by_name():
    ts = ToolSet()
    ts.add_tool(FunctionTool(name="t", description="d1", parameters={}))
    ts.add_tool(FunctionTool(name="t", description="d2", parameters={}))
    assert len(ts.tools) == 1
    assert ts.get_tool("t").description == "d2"
