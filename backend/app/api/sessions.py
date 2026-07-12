from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.database import get_db
from ..db.models import Approval, ChatSession, Message
from ..schemas.api import SessionCreate

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _session_out(s: ChatSession) -> dict:
    return {
        "id": s.id,
        "title": s.title,
        "status": s.status,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


@router.post("")
async def create_session(body: SessionCreate, db: AsyncSession = Depends(get_db)):
    s = ChatSession(title=body.title or "New session")
    db.add(s)
    await db.commit()
    return _session_out(s)


@router.get("")
async def list_sessions(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ChatSession).order_by(ChatSession.updated_at.desc()))).scalars().all()
    return [_session_out(s) for s in rows]


@router.get("/{session_id}")
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)):
    s = await db.get(ChatSession, session_id)
    if s is None:
        raise HTTPException(404, "session not found")
    messages = (
        (await db.execute(select(Message).where(Message.session_id == session_id).order_by(Message.created_at)))
        .scalars()
        .all()
    )
    approvals = (
        (
            await db.execute(
                select(Approval).where(Approval.session_id == session_id, Approval.status == "pending")
            )
        )
        .scalars()
        .all()
    )
    return {
        **_session_out(s),
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "tool_calls": json.loads(m.tool_calls) if m.tool_calls else [],
                "usage": json.loads(m.usage) if m.usage else None,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
        "pending_approvals": [
            {
                "id": a.id,
                "tool": a.tool,
                "server": a.server,
                "tier": a.tier,
                "args": json.loads(a.args),
                "plan_summary": a.plan_summary,
                "expires_at": a.expires_at.isoformat(),
            }
            for a in approvals
        ],
    }


@router.delete("/{session_id}")
async def delete_session(session_id: str, db: AsyncSession = Depends(get_db)):
    s = await db.get(ChatSession, session_id)
    if s is None:
        raise HTTPException(404, "session not found")
    await db.execute(delete(Message).where(Message.session_id == session_id))
    await db.delete(s)
    await db.commit()
    return {"deleted": session_id}
