from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..services import auth
from ..services.event_bus import bus
from ..services.monitor import monitor

router = APIRouter(tags=["ws"])
log = logging.getLogger(__name__)

WS_UNAUTHORIZED = 4401  # application close code the frontend maps to "log in"


async def _ws_authorized(ws: WebSocket) -> bool:
    """Validate the auth cookie (or ?token=) at handshake.

    A router dependency can't 401 a websocket, so the check lives here: reject
    before accept() when a PIN is configured and no valid token is presented.
    """
    if not auth.auth_required():
        return True
    token = ws.cookies.get(auth.COOKIE_NAME) or ws.query_params.get("token")
    if await auth.resolve_token(token) is not None:
        return True
    await ws.close(code=WS_UNAUTHORIZED, reason="Authentication required")
    return False


@router.websocket("/ws/events")
async def events(ws: WebSocket):
    """Multiplexed live-update channel: {topic, payload} frames."""
    if not await _ws_authorized(ws):
        return
    await ws.accept()
    queue = bus.subscribe()
    try:
        # prime the client with current state so the UI renders immediately
        await ws.send_text(json.dumps({"topic": "containers_snapshot", "payload": {"available": monitor.docker_available, "error": monitor.docker_error, "containers": monitor.containers}}, default=str))
        await ws.send_text(json.dumps({"topic": "system_metrics", "payload": monitor.system}, default=str))
        await ws.send_text(json.dumps({"topic": "k8s_snapshot", "payload": monitor.k8s}, default=str))
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=25)
            except asyncio.TimeoutError:
                await ws.send_text(json.dumps({"topic": "ping", "payload": {}}))
                continue
            await ws.send_text(json.dumps(event, default=str))
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.debug("events websocket closed", exc_info=True)
    finally:
        bus.unsubscribe(queue)
