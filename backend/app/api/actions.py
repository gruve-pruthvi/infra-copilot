from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.database import get_db
from ..db.models import Action

router = APIRouter(prefix="/api/actions", tags=["actions"])


@router.get("")
async def list_actions(
    limit: int = 100,
    tier: str | None = None,
    tool: str | None = None,
    session_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(Action).order_by(Action.created_at.desc()).limit(min(limit, 500))
    if tier:
        q = q.where(Action.tier == tier)
    if tool:
        q = q.where(Action.tool == tool)
    if session_id:
        q = q.where(Action.session_id == session_id)
    rows = (await db.execute(q)).scalars().all()
    return [
        {
            "id": a.id,
            "created_at": a.created_at.isoformat(),
            "session_id": a.session_id,
            "server": a.server,
            "tool": a.tool,
            "tier": a.tier,
            "args": json.loads(a.args) if a.args else {},
            "status": a.status,
            "result_summary": a.result_summary,
            "error": a.error,
            "duration_ms": a.duration_ms,
            "approval_id": a.approval_id,
        }
        for a in rows
    ]
