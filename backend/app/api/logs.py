from __future__ import annotations

import asyncio
import logging
import threading

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["logs"])
log = logging.getLogger(__name__)


def _get_container(container_id: str):
    import docker

    client = docker.from_env()
    return client.containers.get(container_id)


@router.get("/api/containers/{container_id}/logs")
async def container_logs(container_id: str, tail: int = 500):
    try:
        c = await asyncio.to_thread(_get_container, container_id)
        raw = await asyncio.to_thread(lambda: c.logs(tail=min(tail, 5000), timestamps=True))
        return {"container": c.name, "logs": raw.decode("utf-8", errors="replace")}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(404, f"{type(e).__name__}: {e}")


@router.websocket("/ws/logs/{container_id}")
async def tail_logs(ws: WebSocket, container_id: str):
    """Live `docker logs --follow` over a WebSocket, one line per message."""
    await ws.accept()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=2000)
    stop = threading.Event()

    def pump() -> None:
        try:
            c = _get_container(container_id)
            for line in c.logs(stream=True, follow=True, tail=200, timestamps=True):
                if stop.is_set():
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\n")
                loop.call_soon_threadsafe(queue.put_nowait, text)
        except Exception as e:  # noqa: BLE001
            loop.call_soon_threadsafe(queue.put_nowait, f"[stream error] {e}")
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    thread = threading.Thread(target=pump, daemon=True, name=f"logtail-{container_id}")
    thread.start()
    try:
        while True:
            line = await queue.get()
            if line is None:
                break
            await ws.send_text(line)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.debug("log tail websocket closed", exc_info=True)
    finally:
        stop.set()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
