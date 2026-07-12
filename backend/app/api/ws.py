from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..services.event_bus import bus
from ..services.monitor import monitor

router = APIRouter(tags=["ws"])
log = logging.getLogger(__name__)


@router.websocket("/ws/events")
async def events(ws: WebSocket):
    """Multiplexed live-update channel: {topic, payload} frames."""
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
