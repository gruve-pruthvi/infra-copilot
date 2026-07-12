from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..agents.run_manager import TERMINAL_EVENTS, run_manager
from ..schemas.api import MessageIn

router = APIRouter(prefix="/api/sessions", tags=["chat"])


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


async def _drain(queue: asyncio.Queue, broadcaster, *, follow_only: bool = False):
    """Yield SSE frames until a terminal event (or client disconnect)."""
    try:
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=20)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                if follow_only and broadcaster.last_status in ("idle", "error"):
                    # nothing running and nothing pending — let the client re-poll
                    yield _sse({"type": "status", "status": broadcaster.last_status})
                continue
            yield _sse(ev)
            if ev.get("type") in TERMINAL_EVENTS:
                break
    finally:
        broadcaster.unsubscribe(queue)


@router.post("/{session_id}/messages")
async def send_message(session_id: str, body: MessageIn):
    bcast = run_manager.broadcaster(session_id)
    queue = bcast.subscribe()
    try:
        await run_manager.start_turn(session_id, body.content)
    except KeyError:
        bcast.unsubscribe(queue)
        raise HTTPException(404, "session not found")
    except RuntimeError as e:
        # Only the concurrent-run guard raises here now — a session `awaiting_approval`
        # no longer 409s: start_turn auto-rejects the pending approval(s) and continues.
        bcast.unsubscribe(queue)
        raise HTTPException(409, str(e))
    return StreamingResponse(_drain(queue, bcast), media_type="text/event-stream")


@router.get("/{session_id}/stream")
async def attach_stream(session_id: str):
    """Attach to a session's live event stream (used after approvals resume a run)."""
    bcast = run_manager.broadcaster(session_id)
    queue = bcast.subscribe()
    return StreamingResponse(_drain(queue, bcast, follow_only=True), media_type="text/event-stream")


@router.post("/{session_id}/cancel")
async def cancel_run(session_id: str):
    cancelled = await run_manager.cancel(session_id)
    return {"cancelled": cancelled}
