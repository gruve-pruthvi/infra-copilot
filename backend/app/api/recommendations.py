from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.database import get_db
from ..db.models import Recommendation
from ..services.recommendations import recommendations

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])


@router.get("")
async def list_recommendations(status: str = "active", db: AsyncSession = Depends(get_db)):
    rows = (
        (await db.execute(select(Recommendation).where(Recommendation.status == status).order_by(Recommendation.updated_at.desc())))
        .scalars()
        .all()
    )
    return {
        "recommendations": [
            {
                "id": r.id,
                "kind": r.kind,
                "resource": r.resource,
                "detail": json.loads(r.detail or "{}"),
                "status": r.status,
                "updated_at": r.updated_at.isoformat(),
            }
            for r in rows
        ]
    }


@router.post("/{rec_id}/dismiss")
async def dismiss(rec_id: str, db: AsyncSession = Depends(get_db)):
    row = await db.get(Recommendation, rec_id)
    if row is None:
        raise HTTPException(404, "recommendation not found")
    row.status = "dismissed"
    row.dismissed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True}


@router.post("/scan")
async def scan_now():
    await recommendations.scan_now()
    return {"ok": True}
