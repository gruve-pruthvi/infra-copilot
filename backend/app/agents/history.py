"""Trim agent history to a character budget at whole-turn boundaries.

pydantic-ai 2.9 does not expose history_processors on Agent, so trimming
happens where RunManager materializes message_history. Cutting only at
ModelRequest-with-UserPromptPart boundaries guarantees tool-call parts are
never separated from their returns.
"""
from __future__ import annotations

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)


def reconcile_tool_calls(
    messages: list[ModelMessage], deferred: set[str] | None = None
) -> list[ModelMessage]:
    """Close any tool call left unanswered by an interrupted run.

    A run can die between issuing a tool call and recording its result — a
    provider 5xx, a timeout, a killed process. `_save_history` only runs on the
    success path, so the persisted history keeps the tool-call part with no
    matching tool-return, and pydantic-ai then rejects every later prompt with
    "Cannot provide a new user prompt when the message history contains
    unprocessed tool calls". That wedges the session permanently: the status is
    `error` rather than `awaiting_approval`, so the auto-reject path never runs.

    Appending a synthetic failed return restores the invariant. The outcome is
    reported as failed and explicitly *unknown* rather than as an error we
    observed, because the tool may well have run — the audit log in `actions`
    remains the record of what actually happened.

    `deferred` lists call ids that are legitimately unanswered right now — a
    run paused on an approval, about to be resumed with real
    `deferred_tool_results`. Those must be left alone; synthesizing a return
    for them would collide with the result the resume is about to supply.
    """
    deferred = deferred or set()
    if not messages:
        return messages
    answered = {
        p.tool_call_id
        for m in messages
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    }
    dangling = [
        p
        for m in messages
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, ToolCallPart)
        and p.tool_call_id not in answered
        and p.tool_call_id not in deferred
    ]
    if not dangling:
        return messages
    repair = ModelRequest(parts=[
        ToolReturnPart(
            tool_name=p.tool_name,
            tool_call_id=p.tool_call_id,
            outcome="failed",
            content={
                "error": True,
                "interrupted": True,
                "message": (
                    "The previous run was interrupted before this tool's result was "
                    "recorded. Whether it took effect is unknown — verify the current "
                    "state before retrying."
                ),
            },
        )
        for p in dangling
    ])
    return [*messages, repair]


def _turn_starts(messages: list[ModelMessage]) -> list[int]:
    return [
        i for i, m in enumerate(messages)
        if isinstance(m, ModelRequest) and any(isinstance(p, UserPromptPart) for p in m.parts)
    ]


def trim_history(messages: list[ModelMessage], char_budget: int) -> list[ModelMessage]:
    if not messages:
        return messages
    sizes = [len(ModelMessagesTypeAdapter.dump_json([m])) for m in messages]
    if sum(sizes) <= char_budget:
        return messages
    starts = _turn_starts(messages) or [0]
    for start in starts:
        if sum(sizes[start:]) <= char_budget:
            return messages[start:]
    return messages[starts[-1]:]  # last turn always survives
