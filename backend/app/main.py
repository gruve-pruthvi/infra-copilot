from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .agents.run_manager import recover_on_startup
from .api import actions, approvals, auth as auth_api, chat, dashboard, logs, recommendations as recommendations_api, sessions, settings as settings_api, ws
from .api.auth import require_auth
from .services import auth as auth_service
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

# /api/auth/* is public by design (status / setup / login must work while
# logged out); its own routes guard themselves where needed.
app.include_router(auth_api.router)

# Everything below requires a token once a PIN is configured. The guard is a
# no-op on an unconfigured install, so a fresh setup behaves exactly as before.
_guard = [Depends(require_auth)]
app.include_router(sessions.router, dependencies=_guard)
app.include_router(chat.router, dependencies=_guard)
app.include_router(approvals.router, dependencies=_guard)
app.include_router(dashboard.router, dependencies=_guard)
app.include_router(logs.router, dependencies=_guard)
app.include_router(actions.router, dependencies=_guard)
app.include_router(recommendations_api.router, dependencies=_guard)
app.include_router(settings_api.router, dependencies=_guard)
# WS routes authenticate at handshake inside ws.py (a router dependency cannot
# return a 401 to a websocket client).
app.include_router(ws.router)


@app.get("/healthz")
async def healthz(request: Request):
    """Public liveness probe. Configuration detail is only exposed to an
    authenticated caller — an unauthenticated probe learns just that the
    server is up and whether it wants a login."""
    if auth_service.auth_required():
        sess = await auth_service.resolve_token(auth_api._token_from(request))
        if sess is None:
            return {"ok": True, "auth_required": True, "authenticated": False}
    return {
        "ok": True,
        "auth_required": auth_service.auth_required(),
        "authenticated": True,
        "docker": {"available": monitor.docker_available, "error": monitor.docker_error},
        "k8s": {"available": monitor.k8s.get("available", False), "reason": monitor.k8s.get("reason")},
        "provider": {
            "active": store.get("active_provider"),
            "model": store.get("active_model"),
            "configured": bool(store.get(f"{store.get('active_provider')}_api_key")),
        },
        "dry_run_mode": store.dry_run_mode,
    }
