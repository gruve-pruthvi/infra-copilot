from __future__ import annotations

from fastapi import APIRouter

from ..services.monitor import monitor

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/containers")
async def containers():
    return {
        "available": monitor.docker_available,
        "error": monitor.docker_error,
        "containers": monitor.containers,
    }


@router.get("/k8s")
async def k8s():
    return monitor.k8s


@router.get("/system")
async def system():
    return {"current": monitor.system, "history": monitor.history_series()}
