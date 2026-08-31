"""Local auth endpoints + the dependency every protected router uses.

Public (never guarded): /api/auth/status, /api/auth/setup, /api/auth/login,
and /healthz. Everything else requires a token once a PIN is configured.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..services import auth
from ..services.settings_store import store
from ..tools.roles import ROLES, normalize_role

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SetupBody(BaseModel):
    pin: str = Field(min_length=auth.MIN_PIN_LENGTH)
    default_role: str = "admin"


class PinBody(BaseModel):
    pin: str


def _token_from(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip() or None
    return request.cookies.get(auth.COOKIE_NAME)


async def current_identity(request: Request) -> dict:
    """Resolve the caller. Raises 401 when auth is on and the token is bad.

    When no PIN is configured the install is open and the caller is treated as
    a local admin — identical to the pre-auth behaviour.
    """
    if not auth.auth_required():
        return {"authenticated": False, "role": "admin", "session": None, "enforced": False}
    sess = await auth.resolve_token(_token_from(request))
    if sess is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return {
        "authenticated": True,
        "role": auth.effective_role(sess),
        "session": sess,
        "enforced": True,
    }


async def require_auth(identity: dict = Depends(current_identity)) -> dict:
    """Router-level guard. Attach with dependencies=[Depends(require_auth)]."""
    return identity


async def require_admin(identity: dict = Depends(current_identity)) -> dict:
    """Admin-only guard for controls that affect the whole platform."""
    if identity["role"] != "admin":
        raise HTTPException(
            status_code=403,
            detail=f"role '{identity['role']}' may not change this — elevate to admin first",
        )
    return identity


def _check_throttle() -> None:
    retry = auth.throttle_retry_after()
    if retry:
        raise HTTPException(
            status_code=429, detail=f"Too many failed attempts — try again in {retry}s",
            headers={"Retry-After": str(retry)},
        )


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        auth.COOKIE_NAME, token,
        httponly=True, samesite="lax", secure=False,  # localhost-only, plain http
        max_age=auth.TOKEN_TTL_SECONDS, path="/",
    )


@router.get("/status")
async def status(request: Request) -> dict:
    """Public: does this install have a PIN, and is the caller logged in?"""
    configured = auth.auth_required()
    role, authenticated = "admin", False
    if configured:
        sess = await auth.resolve_token(_token_from(request))
        if sess is not None:
            authenticated, role = True, auth.effective_role(sess)
        else:
            role = "viewer"
    return {
        "configured": configured,
        "authenticated": authenticated or not configured,
        "role": role,
        "roles": sorted(ROLES),
        "default_role": auth.default_role(),
    }


@router.post("/setup")
async def setup(body: SetupBody, response: Response) -> dict:
    """First-run PIN configuration. Refused once a PIN already exists."""
    if auth.auth_required():
        raise HTTPException(status_code=409, detail="A PIN is already configured")
    try:
        pin_hash = await auth.hash_pin_async(body.pin)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    await store.update({"auth_pin_hash": pin_hash, "auth_default_role": normalize_role(body.default_role)})
    token = await auth.issue_token(role=auth.default_role())
    _set_cookie(response, token)
    return {"ok": True, "role": auth.default_role(), "token": token}


@router.post("/login")
async def login(body: PinBody, response: Response) -> dict:
    if not auth.auth_required():
        raise HTTPException(status_code=409, detail="No PIN configured — run setup first")
    _check_throttle()
    if not await auth.verify_pin_async(body.pin, store.get("auth_pin_hash")):
        auth.record_failure()
        raise HTTPException(status_code=401, detail="Incorrect PIN")
    auth.record_success()
    role = auth.default_role()
    token = await auth.issue_token(role=role)
    _set_cookie(response, token)
    return {"ok": True, "role": role, "token": token}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    token = _token_from(request)
    if token:
        await auth.revoke_token(token)
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/elevate")
async def elevate(body: PinBody, request: Request,
                  identity: dict = Depends(require_auth)) -> dict:
    """Re-enter the PIN to hold admin for a short window."""
    _check_throttle()
    if not await auth.verify_pin_async(body.pin, store.get("auth_pin_hash")):
        auth.record_failure()
        raise HTTPException(status_code=401, detail="Incorrect PIN")
    auth.record_success()
    token = _token_from(request)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    until = datetime.now(timezone.utc) + timedelta(seconds=auth.ELEVATION_SECONDS)
    await auth.set_elevated(token, until)
    return {"ok": True, "role": "admin", "elevated_until": until.isoformat()}


@router.get("/me")
async def me(identity: dict = Depends(require_auth)) -> dict:
    return {"role": identity["role"], "enforced": identity["enforced"]}
