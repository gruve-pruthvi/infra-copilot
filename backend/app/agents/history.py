"""Trim agent history to a character budget at whole-turn boundaries.

pydantic-ai 2.9 does not expose history_processors on Agent, so trimming
happens where RunManager materializes message_history. Cutting only at
ModelRequest-with-UserPromptPart boundaries guarantees tool-call parts are
never separated from their returns.
"""
from __future__ import annotations

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelRequest, UserPromptPart


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
