from __future__ import annotations

from typing import Any

from pydantic_ai import Agent, DeferredToolRequests

from ..llm.models import build_model
from ..tools.toolsets import get_mcp_toolsets, native_toolset
from .deps import RunDeps
from .prompts import SYSTEM_PROMPT


def build_agent() -> tuple[Agent[RunDeps, Any], dict[str, Any] | None]:
    """Construct the Infra Copilot agent for the currently configured provider.

    Built per-run so provider/model changes in Settings apply immediately.
    Toolsets (with keep-alive MCP subprocesses) are shared singletons.
    """
    model, model_settings = build_model()
    agent: Agent[RunDeps, Any] = Agent(
        model,
        deps_type=RunDeps,
        instructions=SYSTEM_PROMPT,
        output_type=[str, DeferredToolRequests],
        toolsets=[*get_mcp_toolsets(), native_toolset],
        # Providers (notably Gemini) occasionally return an empty response; the
        # default budget of 1 turns that transient into a failed run.
        retries={"output": 3},
    )
    return agent, model_settings
