"""Agent toolsets.

Three MCP stdio servers (docker / k8s / monitoring) spawned from this venv
(mcp-servers is a path dependency), plus a small native toolset for things
that live in backend memory (metrics history) or need tight sandboxing
(write_manifest).

Every call — MCP or native — is routed through the ToolGateway. The model
never gets a direct MCP session.
"""

from __future__ import annotations

import sys
import time
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.mcp import MCPToolset, StdioTransport
from pydantic_ai.toolsets import FunctionToolset

from ..config import settings
from ..services.monitor import monitor
from ..services.progress import emit_progress
from .gateway import gateway

_MCP_SERVERS = {
    "docker": "docker_tools.server",
    "k8s": "k8s_tools.server",
    "monitoring": "monitoring_tools.server",
}

_mcp_toolsets: list[MCPToolset] | None = None


def _make_process_tool_call(server_id: str):
    async def process_tool_call(ctx: RunContext[Any], call_tool, name: str, tool_args: dict[str, Any]):
        async def executor(effective_args: dict[str, Any]):
            return await call_tool(name, effective_args)

        return await gateway.execute(ctx, server_id, name, dict(tool_args), executor)

    return process_tool_call


def _make_progress_handler(server_id: str):
    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        text = message or (f"{progress:g}/{total:g}" if total else f"{progress:g}")
        emit_progress(server_id, text)

    return on_progress


def get_mcp_toolsets() -> list[MCPToolset]:
    global _mcp_toolsets
    if _mcp_toolsets is None:
        _mcp_toolsets = [
            MCPToolset(
                StdioTransport(command=sys.executable, args=["-m", module], keep_alive=True),
                id=server_id,
                process_tool_call=_make_process_tool_call(server_id),
                tool_error_behavior="retry",
                progress_handler=_make_progress_handler(server_id),
            )
            for server_id, module in _MCP_SERVERS.items()
        ]
    return _mcp_toolsets


# ---------------------------------------------------------------------------
# Native tools
# ---------------------------------------------------------------------------

native_toolset: FunctionToolset = FunctionToolset(id="native")


@native_toolset.tool
async def system_history(ctx: RunContext[Any], minutes: int = 30) -> dict:
    """Host CPU/memory usage over the last N minutes (5s resolution), from the backend's monitor loop. Use to spot trends and spikes."""

    async def executor(args: dict[str, Any]) -> dict:
        cutoff = time.time() - max(1, min(args.get("minutes", 30), settings.history_window_minutes)) * 60
        series = [s for s in monitor.history_series() if s["ts"] >= cutoff]
        # downsample to <=200 points so it fits comfortably in context
        step = max(1, len(series) // 200)
        return {"points": series[::step], "interval_seconds": settings.monitor_interval_seconds * step}

    return await gateway.execute(ctx, "native", "system_history", {"minutes": minutes}, executor)


@native_toolset.tool
async def write_manifest(ctx: RunContext[Any], filename: str, content: str) -> dict:
    """Write a generated manifest / compose / config file into the sandboxed workspace directory (~/.infra-copilot/workspace). Returns the absolute path."""

    async def executor(args: dict[str, Any]) -> dict:
        root = settings.workspace_dir.resolve()
        target = (root / args["filename"]).resolve()
        if not target.is_relative_to(root):
            return {"error": True, "message": "path escapes the workspace sandbox"}
        if args.get("dry_run"):
            return {"dry_run": True, "would_write": str(target), "bytes": len(args["content"])}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(args["content"])
        return {"written": str(target), "bytes": len(args["content"])}

    return await gateway.execute(ctx, "native", "write_manifest", {"filename": filename, "content": content}, executor)
