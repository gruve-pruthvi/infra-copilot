from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RunDeps:
    """Per-run dependencies injected into tools via RunContext.deps."""

    session_id: str
