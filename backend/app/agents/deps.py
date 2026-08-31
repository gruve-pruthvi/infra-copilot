from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RunDeps:
    """Per-run dependencies injected into tools via RunContext.deps."""

    session_id: str
    # Authorization ceiling for this run, resolved from the authenticated
    # request. Defaults to the least privileged role so anything that forgets
    # to set it cannot escalate.
    role: str = "viewer"
