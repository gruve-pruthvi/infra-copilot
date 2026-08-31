"""Role ceiling — the second half of the gateway's authorization decision.

Tier classification answers "how dangerous is this tool?"; the role answers
"is this operator allowed to do things that dangerous?". Both are static,
deterministic, server-side, and both fail closed: an unrecognised role is a
viewer, exactly as an unrecognised tool is destructive.

SCOPE — read this before relying on roles as a security boundary. This is a
single-operator design: there is exactly one PIN, and /api/auth/elevate grants
admin on re-entry of that same PIN. Anyone holding a viewer token necessarily
knows the PIN (they typed it to log in) and can therefore elevate themselves.
Roles here protect against MISTAKES — an accidental `remove_volume` while
poking around in read-only mode — and they bound what a compromised *agent*
can do with a session it does not own the PIN for. They are not a barrier
between distinct human principals; that would need per-user credentials, which
Phase 3 deliberately did not build.
"""

from __future__ import annotations

from .classification import Tier

# role -> the tiers it may invoke
ROLES: dict[str, frozenset[Tier]] = {
    "viewer": frozenset({Tier.READ}),
    "operator": frozenset({Tier.READ, Tier.MUTATING}),
    "admin": frozenset({Tier.READ, Tier.MUTATING, Tier.DESTRUCTIVE}),
}

DEFAULT_ROLE = "viewer"


def normalize_role(role: object) -> str:
    """Coerce anything to a known role name, defaulting to the least privileged."""
    if not isinstance(role, str):
        return DEFAULT_ROLE
    candidate = role.strip().lower()
    return candidate if candidate in ROLES else DEFAULT_ROLE


def allows(role: object, tier: Tier) -> bool:
    return tier in ROLES[normalize_role(role)]


# Least- to most-privileged. Used to clamp a stored ceiling against a live one.
_ORDER = ["viewer", "operator", "admin"]


def min_role(a: object, b: object) -> str:
    """The lower of two roles — privilege can only ever narrow."""
    return min(normalize_role(a), normalize_role(b), key=_ORDER.index)
