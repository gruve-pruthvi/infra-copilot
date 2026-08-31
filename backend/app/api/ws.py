from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import settings
from ..services import auth
from ..services.event_bus import bus
from ..services.monitor import monitor

router = APIRouter(tags=["ws"])
log = logging.getLogger(__name__)

WS_UNAUTHORIZED = 4401  # application close code the frontend maps to "log in"
WS_FORBIDDEN_ORIGIN = 4403


def _origin_allowed(origin: str | None, host: str | None) -> bool:
    """Cross-origin guard for websockets.

    Browsers do NOT apply CORS to WebSocket connections, so `cors_origins`
    protects the REST surface and nothing else. Without this check any page the
    operator happens to visit could open ws://localhost:8000/ws/events and
    stream live infrastructure state and container logs off their machine — on
    a default no-PIN install, with no interaction beyond following a link.

    A missing Origin means a non-browser client (curl, the test suite, a CLI);
    those are not subject to the drive-by threat and are allowed through.
    """
    if origin is None:
        return True
    if origin in settings.cors_origins:
        return True
    # Same-origin: the built frontend served by this very backend.
    if host:
        for scheme in ("http", "https"):
            if origin == f"{scheme}://{host}":
                return True
    return False


async def _ws_authorized(ws: WebSocket) -> bool:
    """Gate a websocket handshake: origin first, then token.

    A router dependency can't return a 401 to a websocket, so the check lives
    here and must run before accept().
    """
    # Origin is checked even when no PIN is set — the open install is exactly
    # the case the drive-by attack targets.
    if not _origin_allowed(ws.headers.get("origin"), ws.headers.get("host")):
        await ws.close(code=WS_FORBIDDEN_ORIGIN, reason="Cross-origin websocket refused")
        return False
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
