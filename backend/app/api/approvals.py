from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.run_manager import run_manager
from ..db.database import get_db
from ..db.models import Approval
from ..schemas.api import ApprovalDecision

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


@router.get("")
async def list_approvals(status: str = "pending", db: AsyncSession = Depends(get_db)):
    rows = (
        (await db.execute(select(Approval).where(Approval.status == status).order_by(Approval.created_at.desc())))
        .scalars()
        .all()
    )
    return [
        {
            "id": a.id,
            "session_id": a.session_id,
            "tool": a.tool,
            "server": a.server,
            "tier": a.tier,
            "args": json.loads(a.args),
            "plan_summary": a.plan_summary,
            "status": a.status,
            "created_at": a.created_at.isoformat(),
            "expires_at": a.expires_at.isoformat(),
        }
        for a in rows
    ]


@router.post("/{approval_id}/approve")
async def approve(approval_id: str, body: ApprovalDecision):
    try:
        return await run_manager.resolve_approval(approval_id, approved=True, confirm_text=body.confirm_text)
    except KeyError:
        raise HTTPException(404, "approval not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{approval_id}/reject")
async def reject(approval_id: str, body: ApprovalDecision):
    try:
        return await run_manager.resolve_approval(approval_id, approved=False, reason=body.reason)
    except KeyError:
        raise HTTPException(404, "approval not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
