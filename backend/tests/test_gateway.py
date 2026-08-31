"""Approval-enforcement tests for the ToolGateway.

These treat the LLM layer as adversarial: the gateway must refuse to execute
mutating tools without a valid, matching, unconsumed, unexpired DB approval —
no matter what the framework claims.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic_ai import ApprovalRequired
from sqlalchemy import select

from app.agents.deps import RunDeps
from app.db.database import session_factory
from app.db.models import Action, Approval
from app.tools.classification import Tier, classify
from app.tools.gateway import args_hash, gateway, redact


class FakeCtx:
    """Duck-typed RunContext — only the fields the gateway reads."""

    def __init__(self, session_id: str, approved: bool = False, call_id: str = "call_1", role: str = "admin"):
        # Role defaults to admin here: these tests isolate the *approval* gate.
        # The role ceiling is a separate gate, covered in tests/test_roles.py.
        self.deps = RunDeps(session_id=session_id, role=role)
        self.tool_call_approved = approved
        self.tool_call_id = call_id


def make_executor(calls: list):
    async def executor(args):
        calls.append(args)
        return {"ok": True}

    return executor


async def add_approval(session_id: str, *, tool="run_container", server="docker", args=None, status="approved",
                       call_id="call_1", expired=False, consumed=False) -> Approval:
    args = args if args is not None else {"image": "nginx"}
    now = datetime.now(timezone.utc)
    async with session_factory() as db:
        row = Approval(
            session_id=session_id,
            tool_call_id=call_id,
            server=server,
            tool=tool,
            tier="mutating",
            args="{}",
            args_hash=args_hash(server, tool, args),
            status=status,
            expires_at=now + (timedelta(seconds=-5) if expired else timedelta(minutes=15)),
            consumed_at=now if consumed else None,
        )
        db.add(row)
        await db.commit()
        return row


async def last_action(tool: str) -> Action | None:
    async with session_factory() as db:
        rows = (await db.execute(select(Action).where(Action.tool == tool).order_by(Action.created_at.desc()))).scalars().all()
        return rows[0] if rows else None


# ---------------------------------------------------------------------------


async def test_read_tool_executes_and_audits(session_id):
    calls: list = []
    result = await gateway.execute(FakeCtx(session_id), "monitoring", "system_overview", {}, make_executor(calls))
    assert result == {"ok": True}
    assert len(calls) == 1
    action = await last_action("system_overview")
    assert action is not None and action.status == "executed" and action.tier == "read"


async def test_mutating_without_approval_pauses(session_id):
    calls: list = []
    with pytest.raises(ApprovalRequired):
        await gateway.execute(FakeCtx(session_id, approved=False), "docker", "run_container", {"image": "nginx"}, make_executor(calls))
    assert calls == []  # never executed


async def test_approved_flag_without_db_record_is_denied(session_id):
    """Even if the framework says approved, no DB record → denied."""
    calls: list = []
    result = await gateway.execute(FakeCtx(session_id, approved=True), "docker", "run_container", {"image": "nginx"}, make_executor(calls))
    assert result["denied"] is True
    assert calls == []
    action = await last_action("run_container")
    assert action.status == "denied"


async def test_wrong_args_hash_is_denied(session_id):
    await add_approval(session_id, args={"image": "nginx"})
    calls: list = []
    # model tries to run a DIFFERENT image than what was approved
    result = await gateway.execute(FakeCtx(session_id, approved=True), "docker", "run_container", {"image": "evil"}, make_executor(calls))
    assert result["denied"] is True
    assert calls == []


async def test_approved_executes_once_and_consumes(session_id):
    await add_approval(session_id, args={"image": "nginx"})
    calls: list = []
    result = await gateway.execute(FakeCtx(session_id, approved=True), "docker", "run_container", {"image": "nginx"}, make_executor(calls))
    assert result == {"ok": True}
    assert len(calls) == 1
    # replay of the same approval must be denied
    result2 = await gateway.execute(FakeCtx(session_id, approved=True), "docker", "run_container", {"image": "nginx"}, make_executor(calls))
    assert result2["denied"] is True
    assert len(calls) == 1


async def test_expired_approval_is_denied(session_id):
    await add_approval(session_id, expired=True)
    calls: list = []
    result = await gateway.execute(FakeCtx(session_id, approved=True), "docker", "run_container", {"image": "nginx"}, make_executor(calls))
    assert result["denied"] is True
    assert calls == []


async def test_rejected_approval_is_denied(session_id):
    await add_approval(session_id, status="rejected")
    calls: list = []
    result = await gateway.execute(FakeCtx(session_id, approved=True), "docker", "run_container", {"image": "nginx"}, make_executor(calls))
    assert result["denied"] is True
    assert calls == []


async def test_dry_run_mode_forces_dry_run(session_id, monkeypatch):
    from app.services.settings_store import store

    monkeypatch.setitem(store._cache, "dry_run_mode", "true")
    calls: list = []
    result = await gateway.execute(FakeCtx(session_id, approved=False), "docker", "run_container", {"image": "nginx"}, make_executor(calls))
    assert result == {"ok": True}
    assert calls[0]["dry_run"] is True  # no approval needed, but forced dry-run
    action = await last_action("run_container")
    assert action.status == "dry_run"


def test_unknown_tool_defaults_to_destructive():
    assert classify("docker", "brand_new_tool") is Tier.DESTRUCTIVE
    assert classify("nonsense", "whatever") is Tier.DESTRUCTIVE


def test_redaction_masks_secret_keys():
    obj = {"env": {"POSTGRES_PASSWORD": "hunter2", "PGDATA": "/data"}, "api_key": "sk-123", "items": [{"token": "t"}]}
    red = redact(obj)
    assert red["env"]["POSTGRES_PASSWORD"] == "***REDACTED***"
    assert red["env"]["PGDATA"] == "/data"
    assert red["api_key"] == "***REDACTED***"
    assert red["items"][0]["token"] == "***REDACTED***"
