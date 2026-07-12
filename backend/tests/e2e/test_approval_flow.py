"""End-to-end approval flow through the RunManager (no API key needed).

Drives: user message → run pauses on a mutating tool → approval row created →
approve/reject via resolve_approval → run resumes → terminal event + audit.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.models.test import TestModel
from sqlalchemy import select

from app.agents.deps import RunDeps
from app.db.database import session_factory
from app.db.models import Action, Approval, ChatSession
from app.tools.toolsets import get_mcp_toolsets, native_toolset


def _fake_build_agent():
    agent = Agent(
        TestModel(call_tools=["stop_container"]),
        deps_type=RunDeps,
        output_type=[str, DeferredToolRequests],
        toolsets=[*get_mcp_toolsets(), native_toolset],
    )
    return agent, None


async def _drain_until(queue: asyncio.Queue, terminal: set[str], timeout: float = 60) -> list[dict]:
    events = []
    while True:
        ev = await asyncio.wait_for(queue.get(), timeout=timeout)
        events.append(ev)
        if ev["type"] in terminal:
            return events


@pytest.fixture()
def patched_run_manager(monkeypatch):
    from app.agents import run_manager as rm

    monkeypatch.setattr(rm, "build_agent", _fake_build_agent)
    return rm.run_manager


async def test_approve_resumes_and_executes(session_id, patched_run_manager):
    rm = patched_run_manager
    queue = rm.broadcaster(session_id).subscribe()

    await rm.start_turn(session_id, "stop the idle container")
    events = await _drain_until(queue, {"turn_paused", "done", "error"})
    assert events[-1]["type"] == "turn_paused", events

    approval_events = [e for e in events if e["type"] == "approval_required"]
    assert approval_events, events
    approval = approval_events[0]["approval"]
    assert approval["tool"] == "stop_container"
    assert approval["tier"] == "mutating"

    async with session_factory() as db:
        sess = await db.get(ChatSession, session_id)
        assert sess.status == "awaiting_approval"

    result = await rm.resolve_approval(approval["id"], approved=True)
    assert result["resumed"] is True

    events2 = await _drain_until(queue, {"done", "error"})
    assert events2[-1]["type"] == "done", events2

    # the gateway consumed the approval and audited an execution attempt
    async with session_factory() as db:
        row = await db.get(Approval, approval["id"])
        assert row.status == "approved" and row.consumed_at is not None
        actions = (
            (await db.execute(select(Action).where(Action.session_id == session_id, Action.tool == "stop_container")))
            .scalars()
            .all()
        )
        # TestModel invents a container name, so execution runs but the tool
        # reports a docker NotFound error — status is still "executed" (the
        # call happened) with an error payload inside the result.
        assert actions, "expected an audit row for the approved execution"
        sess = await db.get(ChatSession, session_id)
        assert sess.status == "idle"


async def test_reject_resumes_with_denial(session_id, patched_run_manager):
    rm = patched_run_manager
    queue = rm.broadcaster(session_id).subscribe()

    await rm.start_turn(session_id, "stop the idle container")
    events = await _drain_until(queue, {"turn_paused", "done", "error"})
    approval = next(e for e in events if e["type"] == "approval_required")["approval"]

    result = await rm.resolve_approval(approval["id"], approved=False, reason="not now")
    assert result["resumed"] is True

    events2 = await _drain_until(queue, {"done", "error"})
    assert events2[-1]["type"] == "done", events2

    async with session_factory() as db:
        row = await db.get(Approval, approval["id"])
        assert row.status == "rejected"
        # denial means the tool was never executed for this call
        actions = (
            (await db.execute(select(Action).where(Action.session_id == session_id, Action.tool == "stop_container")))
            .scalars()
            .all()
        )
        assert actions == []
