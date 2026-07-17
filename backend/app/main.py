from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .agents.run_manager import recover_on_startup
from .api import actions, approvals, chat, dashboard, logs, recommendations as recommendations_api, sessions, settings as settings_api, ws
from .config import settings
from .db.database import init_db
from .services.monitor import monitor
from .services.recommendations import recommendations
from .services.settings_store import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    await init_db()
    await store.load()
    await recover_on_startup()
    monitor.start()
    recommendations.start()
    yield
    await recommendations.stop()
    await monitor.stop()


app = FastAPI(title="infra-copilot", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessions.router)
app.include_router(chat.router)
app.include_router(approvals.router)
app.include_router(dashboard.router)
app.include_router(logs.router)
app.include_router(actions.router)
app.include_router(recommendations_api.router)
app.include_router(settings_api.router)
app.include_router(ws.router)


@app.get("/healthz")
async def healthz():
    return {
        "ok": True,
        "docker": {"available": monitor.docker_available, "error": monitor.docker_error},
        "k8s": {"available": monitor.k8s.get("available", False), "reason": monitor.k8s.get("reason")},
        "provider": {
            "active": store.get("active_provider"),
            "model": store.get("active_model"),
            "configured": bool(store.get(f"{store.get('active_provider')}_api_key")),
        },
        "dry_run_mode": store.dry_run_mode,
    }
