"""Role ceiling enforcement in the ToolGateway.

The role is a *second, independent* gate sitting in front of the approval
check: even a perfectly valid approval row must not let a viewer run a
mutating tool. Like tier classification, this is deterministic server-side
code — never a prompt rule — and it fails closed.
"""

from __future__ import annotations

import pytest

from app.agents.deps import RunDeps
from app.tools.classification import Tier
from app.tools.gateway import gateway
from app.tools.roles import ROLES, allows, normalize_role

from tests.test_gateway import add_approval, last_action, make_executor


class RoleCtx:
    def __init__(self, session_id: str, role, approved: bool = True, call_id: str = "call_role"):
        self.deps = RunDeps(session_id=session_id, role=role)
        self.tool_call_approved = approved
        self.tool_call_id = call_id


# --- the pure ceiling --------------------------------------------------------

def test_role_ladder():
    assert allows("viewer", Tier.READ)
    assert not allows("viewer", Tier.MUTATING)
    assert not allows("viewer", Tier.DESTRUCTIVE)

    assert allows("operator", Tier.READ)
    assert allows("operator", Tier.MUTATING)
    assert not allows("operator", Tier.DESTRUCTIVE)

    assert allows("admin", Tier.READ)
    assert allows("admin", Tier.MUTATING)
    assert allows("admin", Tier.DESTRUCTIVE)


def test_all_roles_are_known():
    assert set(ROLES) == {"viewer", "operator", "admin"}


@pytest.mark.parametrize("junk", [None, "", "root", "superuser", "admin;--", 42, object()])
def test_unknown_roles_fail_closed_to_viewer(junk):
    assert normalize_role(junk) == "viewer"


def test_role_names_are_case_insensitive():
    assert normalize_role("Admin") == "admin"
    assert normalize_role(" operator ") == "operator"


def test_unknown_role_denies_mutating():
    assert not allows("root", Tier.MUTATING)


# --- enforcement through the gateway -----------------------------------------

async def test_viewer_cannot_run_mutating_even_with_valid_approval(session_id):
    """The decisive test: approval present and valid, role still refuses."""
    args = {"image": "nginx"}
    await add_approval(session_id, args=args, call_id="call_role")
    calls: list = []

    res = await gateway.execute(
        RoleCtx(session_id, "viewer"), "docker", "run_container", args, make_executor(calls)
    )

    assert calls == [], "executor must never be reached"
    assert res["error"] is True
    assert res["denied"] is True
    assert "viewer" in res["message"]

    action = await last_action("run_container")
    assert action.status == "denied"


async def test_operator_cannot_run_destructive(session_id):
    args = {"container": "c1"}
    await add_approval(session_id, tool="remove_container", args=args, call_id="call_role")
    calls: list = []

    res = await gateway.execute(
        RoleCtx(session_id, "operator"), "docker", "remove_container", args, make_executor(calls)
    )

    assert calls == []
    assert res["denied"] is True


async def test_operator_can_run_mutating(session_id):
    args = {"image": "nginx"}
    await add_approval(session_id, args=args, call_id="call_role")
    calls: list = []

    res = await gateway.execute(
        RoleCtx(session_id, "operator"), "docker", "run_container", args, make_executor(calls)
    )

    assert calls == [args]
    assert res == {"ok": True}


async def test_admin_can_run_destructive(session_id):
    args = {"container": "c1"}
    await add_approval(session_id, tool="remove_container", args=args, call_id="call_role")
    calls: list = []

    res = await gateway.execute(
        RoleCtx(session_id, "admin"), "docker", "remove_container", args, make_executor(calls)
    )

    assert calls == [args]


async def test_viewer_can_still_read(session_id):
    calls: list = []
    res = await gateway.execute(
        RoleCtx(session_id, "viewer", approved=False), "docker", "list_containers", {}, make_executor(calls)
    )
    assert calls == [{}]
    assert res == {"ok": True}


async def test_missing_role_on_deps_fails_closed(session_id):
    """Deps without a role attribute at all must be treated as viewer."""

    class LegacyCtx:
        def __init__(self):
            self.deps = RunDeps(session_id=session_id)
            self.tool_call_approved = True
            self.tool_call_id = "call_role"

    await add_approval(session_id, args={"image": "nginx"}, call_id="call_role")
    calls: list = []
    res = await gateway.execute(LegacyCtx(), "docker", "run_container", {"image": "nginx"}, make_executor(calls))
    assert calls == []
    assert res["denied"] is True


async def test_role_check_precedes_approval_pause(session_id):
    """A viewer gets a hard denial, not an approval card it could never satisfy."""
    calls: list = []
    res = await gateway.execute(
        RoleCtx(session_id, "viewer", approved=False), "docker", "run_container", {"image": "nginx"},
        make_executor(calls),
    )
    assert calls == []
    assert res["denied"] is True


async def test_dry_run_mode_does_not_bypass_the_role_ceiling(session_id, monkeypatch):
    """Global dry-run skips approval, but a viewer still may not invoke mutating tools."""
    from app.services.settings_store import store

    monkeypatch.setattr(type(store), "dry_run_mode", property(lambda self: True))
    calls: list = []
    res = await gateway.execute(
        RoleCtx(session_id, "viewer", approved=False), "docker", "run_container", {"image": "nginx"},
        make_executor(calls),
    )
    assert calls == []
    assert res["denied"] is True
