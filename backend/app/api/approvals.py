from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
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


class BatchDecisions(BaseModel):
    approve: list[str] = Field(default_factory=list)
    reject: list[str] = Field(default_factory=list)
    confirm_texts: dict[str, str] = Field(default_factory=dict)
    reason: str | None = None


@router.post("/batch")
async def resolve_batch(body: BatchDecisions):
    """Resolve several approvals in one call. `RunManager.resolve_approval`
    already defers resume until the last sibling of a session is resolved, so
    this is just a loop — the run resumes exactly once, on whichever call
    resolves the final pending approval.
    """
    results: dict[str, dict] = {}
    resumed = False
    for aid in body.reject:
        try:
            r = await run_manager.resolve_approval(aid, approved=False, reason=body.reason or "batch reject")
            resumed = resumed or bool(r.get("resumed"))
            results[aid] = {"ok": True, **r}
        except KeyError:
            results[aid] = {"ok": False, "error": "not found"}
        except ValueError as e:
            results[aid] = {"ok": False, "error": str(e)}
    for aid in body.approve:
        # Reject wins for ids listed in both; skip if already processed by reject loop
        if aid in results:
            continue
        try:
            r = await run_manager.resolve_approval(aid, approved=True, confirm_text=body.confirm_texts.get(aid))
            resumed = resumed or bool(r.get("resumed"))
            results[aid] = {"ok": True, **r}
        except KeyError:
            results[aid] = {"ok": False, "error": "not found"}
        except ValueError as e:
            results[aid] = {"ok": False, "error": str(e)}
    return {"resumed": resumed, "results": results}
