"""Routes MCP progress notifications to the SSE stream of the session whose
tool call is currently in flight.

Known limitation (Task 12): the MCP `progress_handler` pydantic-ai gives us
is registered per-server (one callback per MCPToolset/stdio server), not
per-call. We track a single "currently in flight" (session_id, tool_call_id)
per server and attribute every progress notification from that server to it.
If two sessions happen to have tool calls running concurrently against the
same MCP server, a progress line can land on the wrong session's tool card.
This is cosmetic only — it never affects the gateway's approval/audit
guarantees, which are keyed off the real tool_call_id at execution time.
"""
from __future__ import annotations

from typing import Callable

_emitters: dict[str, Callable[[dict], None]] = {}      # session_id -> emit(payload)
_inflight: dict[str, tuple[str, str]] = {}             # server_id -> (session_id, tool_call_id)


def register_session(session_id: str, emit: Callable[[dict], None]) -> None:
    _emitters[session_id] = emit


def unregister_session(session_id: str) -> None:
    _emitters.pop(session_id, None)


def set_inflight(server: str, session_id: str, tool_call_id: str) -> None:
    _inflight[server] = (session_id, tool_call_id)


def clear_inflight(server: str) -> None:
    _inflight.pop(server, None)


def emit_progress(server: str, message: str) -> None:
    target = _inflight.get(server)
    if not target:
        return
    session_id, tool_call_id = target
    emit = _emitters.get(session_id)
    if emit:
        emit({"type": "tool_progress", "tool_call_id": tool_call_id, "text": message})
