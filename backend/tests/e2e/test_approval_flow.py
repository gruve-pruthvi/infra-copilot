"""End-to-end approval flow through the RunManager (no API key needed).

Drives: user message → run pauses on a mutating tool → approval row created →
approve/reject via resolve_approval → run resumes → terminal event + audit.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.models.test import TestModel
from sqlalchemy import select

from app.agents.deps import RunDeps
from app.db.database import session_factory
from app.db.models import Action, Approval, ChatSession
from app.tools.toolsets import get_mcp_toolsets, native_toolset


def _build_fake_agent(call_tools: list[str]):
    agent = Agent(
        TestModel(call_tools=call_tools),
        deps_type=RunDeps,
        output_type=[str, DeferredToolRequests],
        toolsets=[*get_mcp_toolsets(), native_toolset],
    )
    return agent, None


def _fake_build_agent():
    return _build_fake_agent(["stop_container"])


def _install_fake_agent(monkeypatch, call_tools: list[str] | None = None):
    """Patch run_manager.build_agent to a TestModel agent that calls the given tools.

    Returns the (module-level) run_manager singleton so callers can use it the
    same way the `patched_run_manager` fixture does.
    """
    from app.agents import run_manager as rm

    tools = call_tools if call_tools is not None else ["stop_container"]
    monkeypatch.setattr(rm, "build_agent", lambda: _build_fake_agent(tools))
    return rm.run_manager


async def _drain_until(queue: asyncio.Queue, terminal: set[str], timeout: float = 60) -> list[dict]:
    events = []
    while True:
        ev = await asyncio.wait_for(queue.get(), timeout=timeout)
        events.append(ev)
        if ev["type"] in terminal:
            return events


@pytest.fixture()
def patched_run_manager(monkeypatch):
    return _install_fake_agent(monkeypatch)


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


async def test_resolve_approval_resumes_once_for_last_sibling(session_id, monkeypatch):
    """Two pending approvals from one turn: resuming only happens once the last
    sibling is resolved. This is the invariant the batch endpoint relies on to
    do "loop over single resolves" and still resume exactly once.
    """
    rm = _install_fake_agent(monkeypatch, call_tools=["stop_container", "restart_container"])
    queue = rm.broadcaster(session_id).subscribe()

    await rm.start_turn(session_id, "stop and restart seed-nginx")
    events = await _drain_until(queue, {"turn_paused", "done", "error"})
    assert events[-1]["type"] == "turn_paused", events

    approval_ids = [e["approval"]["id"] for e in events if e["type"] == "approval_required"]
    assert len(approval_ids) == 2, events

    r1 = await rm.resolve_approval(approval_ids[0], approved=True)
    assert r1["resumed"] is False

    r2 = await rm.resolve_approval(approval_ids[1], approved=False, reason="batch reject")
    assert r2["resumed"] is True

    events2 = await _drain_until(queue, {"done", "error"})
    assert events2[-1]["type"] == "done", events2


async def test_batch_endpoint_resolves_and_resumes_once(session_id, monkeypatch):
    """Drives the actual POST /api/approvals/batch endpoint: one approve + one
    reject in a single call must resolve both approvals and resume the run
    exactly once.
    """
    from app.agents import run_manager as rm
    from app.main import app

    _install_fake_agent(monkeypatch, call_tools=["stop_container", "restart_container"])

    queue = rm.run_manager.broadcaster(session_id).subscribe()
    await rm.run_manager.start_turn(session_id, "stop and restart seed-nginx")
    events = await _drain_until(queue, {"turn_paused", "done", "error"})
    assert events[-1]["type"] == "turn_paused", events

    approvals = [e["approval"] for e in events if e["type"] == "approval_required"]
    assert len(approvals) == 2, events
    stop_id = next(a["id"] for a in approvals if a["tool"] == "stop_container")
    restart_id = next(a["id"] for a in approvals if a["tool"] == "restart_container")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/approvals/batch",
            json={"approve": [stop_id], "reject": [restart_id], "reason": "batch reject"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resumed"] is True, body
    assert body["results"][stop_id]["ok"] is True, body
    assert body["results"][restart_id]["ok"] is True, body

    events2 = await _drain_until(queue, {"done", "error"})
    assert events2[-1]["type"] == "done", events2

    async with session_factory() as db:
        stop_row = await db.get(Approval, stop_id)
        restart_row = await db.get(Approval, restart_id)
        assert stop_row.status == "approved" and stop_row.consumed_at is not None
        assert restart_row.status == "rejected" and restart_row.reject_reason == "batch reject"

        stop_actions = (
            (await db.execute(select(Action).where(Action.session_id == session_id, Action.tool == "stop_container")))
            .scalars()
            .all()
        )
        assert stop_actions, "expected an audit row for the approved execution"

        restart_actions = (
            (
                await db.execute(
                    select(Action).where(Action.session_id == session_id, Action.tool == "restart_container")
                )
            )
            .scalars()
            .all()
        )
        assert restart_actions == [], "rejected tool must never be executed"


async def test_batch_endpoint_reject_wins_if_id_in_both(session_id, monkeypatch):
    """When the same approval id appears in both approve and reject lists, reject
    wins: the reject loop processes it first, then the approve loop skips it.
    Ensures the result is ok:True and the DB shows rejected status.
    """
    from app.agents import run_manager as rm
    from app.main import app

    _install_fake_agent(monkeypatch, call_tools=["stop_container"])

    queue = rm.run_manager.broadcaster(session_id).subscribe()
    await rm.run_manager.start_turn(session_id, "stop the idle container")
    events = await _drain_until(queue, {"turn_paused", "done", "error"})
    assert events[-1]["type"] == "turn_paused", events

    approval = next(e for e in events if e["type"] == "approval_required")["approval"]
    approval_id = approval["id"]

    # Call batch endpoint with the same id in both approve and reject
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/approvals/batch",
            json={"approve": [approval_id], "reject": [approval_id], "reason": "reject wins"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resumed"] is True, body
    # Reject wins: result should be ok:True (reject loop succeeded)
    assert body["results"][approval_id]["ok"] is True, body

    events2 = await _drain_until(queue, {"done", "error"})
    assert events2[-1]["type"] == "done", events2

    # DB should show rejected status (reject loop processed it)
    async with session_factory() as db:
        row = await db.get(Approval, approval_id)
        assert row.status == "rejected" and row.reject_reason == "reject wins"
        # Tool must not be executed (rejection prevents it)
        actions = (
            (await db.execute(select(Action).where(Action.session_id == session_id, Action.tool == "stop_container")))
            .scalars()
            .all()
        )
        assert actions == [], "rejected tool must never be executed even if id is in both lists"
