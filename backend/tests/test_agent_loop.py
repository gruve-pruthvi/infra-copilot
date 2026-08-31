"""Integration: real MCP stdio servers + gateway + pydantic-ai run loop.

Uses TestModel (no API key). Requires a running Docker daemon.
"""

from __future__ import annotations

import pytest
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.models.test import TestModel

from app.agents.deps import RunDeps
from app.tools.toolsets import get_mcp_toolsets, native_toolset


def make_agent(call_tools: list[str]) -> Agent:
    return Agent(
        TestModel(call_tools=call_tools),
        deps_type=RunDeps,
        output_type=[str, DeferredToolRequests],
        toolsets=[*get_mcp_toolsets(), native_toolset],
    )


@pytest.mark.anyio
async def test_read_tool_roundtrip_via_mcp(session_id):
    agent = make_agent(["list_containers"])
    result = await agent.run("list containers", deps=RunDeps(session_id=session_id, role="admin"))
    # TestModel echoes tool results into its final output
    assert not isinstance(result.output, DeferredToolRequests)
    msgs = result.all_messages()
    tool_returns = [p for m in msgs for p in m.parts if getattr(p, "part_kind", "") == "tool-return"]
    assert any(p.tool_name == "list_containers" for p in tool_returns)


@pytest.mark.anyio
async def test_mutating_tool_pauses_via_mcp(session_id):
    agent = make_agent(["stop_container"])
    result = await agent.run("stop something", deps=RunDeps(session_id=session_id, role="admin"))
    assert isinstance(result.output, DeferredToolRequests)
    assert result.output.approvals, "expected an approval-required tool call"
    assert result.output.approvals[0].tool_name == "stop_container"
