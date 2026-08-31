"""Local single-operator auth.

Model: one local operator, one PIN. Auth is enforced *iff* a PIN has been
configured — a fresh install stays open (it binds to localhost only), and
there is no way to lock yourself out of your own machine by half-configuring
it. Once a PIN exists, every REST route and both WS endpoints require a token.

Storage: the PIN is stored only as a salted scrypt hash; session tokens are
stored only as sha256 digests. Neither the raw PIN nor a raw token is ever
persisted or logged.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from ..db.database import session_factory
from ..db.models import AuthSession
from ..tools.roles import normalize_role
from .settings_store import store

MIN_PIN_LENGTH = 4
TOKEN_TTL_SECONDS = 7 * 24 * 3600
ELEVATION_SECONDS = 900  # 15 min, mirroring the approval TTL
COOKIE_NAME = "infra_copilot_token"

# scrypt work factors — interactive-login grade
_N, _R, _P, _DKLEN = 2**14, 8, 1, 32


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --- PIN ---------------------------------------------------------------------

def hash_pin(pin: str) -> str:
    if not isinstance(pin, str) or len(pin) < MIN_PIN_LENGTH:
        raise ValueError(f"PIN must be at least {MIN_PIN_LENGTH} characters")
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pin.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_pin(pin: str, stored: str) -> bool:
    """Constant-time PIN check. Any malformed or empty hash verifies as False."""
    if not stored or not isinstance(pin, str):
        return False
    try:
        scheme, n, r, p, salt_hex, dk_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(
            pin.encode(), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(dk_hex)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), dk_hex)


def auth_required() -> bool:
    """True once a PIN has been configured."""
    return bool(store.get("auth_pin_hash"))


def default_role() -> str:
    return normalize_role(store.get("auth_default_role", "admin"))


# --- tokens ------------------------------------------------------------------

def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_token(role: str, ttl_seconds: int = TOKEN_TTL_SECONDS) -> str:
    token = secrets.token_urlsafe(32)
    async with session_factory() as db:
        db.add(AuthSession(
            token_hash=_digest(token),
            role=normalize_role(role),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        ))
        await db.commit()
    return token


async def resolve_token(token: str | None) -> AuthSession | None:
    """Return the live session for a raw token, or None if absent/unknown/expired."""
    if not token:
        return None
    async with session_factory() as db:
        row = (await db.execute(
            select(AuthSession).where(AuthSession.token_hash == _digest(token))
        )).scalar_one_or_none()
        if row is None:
            return None
        if (_aware(row.expires_at) or datetime.now(timezone.utc)) < datetime.now(timezone.utc):
            return None
        row.last_seen_at = datetime.now(timezone.utc)
        await db.commit()
        db.expunge(row)
        return row


async def revoke_token(token: str) -> None:
    async with session_factory() as db:
        await db.execute(delete(AuthSession).where(AuthSession.token_hash == _digest(token)))
        await db.commit()


async def revoke_all() -> None:
    async with session_factory() as db:
        await db.execute(delete(AuthSession))
        await db.commit()


async def all_sessions() -> list[AuthSession]:
    async with session_factory() as db:
        return list((await db.execute(select(AuthSession))).scalars().all())


async def set_elevated(token: str, until: datetime) -> None:
    async with session_factory() as db:
        row = (await db.execute(
            select(AuthSession).where(AuthSession.token_hash == _digest(token))
        )).scalar_one_or_none()
        if row is not None:
            row.elevated_until = until
            await db.commit()


def effective_role(sess: AuthSession | None) -> str:
    """Base role, upgraded to admin only while a live elevation is in effect."""
    if sess is None:
        return "viewer"
    until = _aware(sess.elevated_until)
    if until and until > datetime.now(timezone.utc):
        return "admin"
    return normalize_role(sess.role)
