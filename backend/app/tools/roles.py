"""Role ceiling — the second half of the gateway's authorization decision.

Tier classification answers "how dangerous is this tool?"; the role answers
"is this operator allowed to do things that dangerous?". Both are static,
deterministic, server-side, and both fail closed: an unrecognised role is a
viewer, exactly as an unrecognised tool is destructive.
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
