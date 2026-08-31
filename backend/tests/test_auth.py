"""Local auth: PIN hashing, token lifecycle, and endpoint enforcement.

Design under test: auth is enforced *iff* a PIN has been configured. A fresh
install stays open (localhost-only, no lockout); the moment a PIN exists every
REST route and both WS endpoints demand a valid token.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.services import auth
from app.services.settings_store import store


@pytest.fixture()
async def clean_auth(db_ready):
    """Start each test with no PIN configured and no live tokens."""
    await store.update({"auth_pin_hash": "", "auth_default_role": "admin"})
    await auth.revoke_all()
    yield
    await store.update({"auth_pin_hash": "", "auth_default_role": "admin"})
    await auth.revoke_all()


# --- PIN hashing -------------------------------------------------------------

def test_hash_pin_is_salted_and_verifiable():
    a = auth.hash_pin("1234")
    b = auth.hash_pin("1234")
    assert a != b, "each hash must use a fresh salt"
    assert auth.verify_pin("1234", a)
    assert auth.verify_pin("1234", b)


def test_verify_pin_rejects_wrong_pin():
    assert not auth.verify_pin("9999", auth.hash_pin("1234"))


def test_verify_pin_never_returns_true_for_empty_hash():
    assert not auth.verify_pin("1234", "")
    assert not auth.verify_pin("", "")


def test_verify_pin_survives_malformed_hash():
    assert not auth.verify_pin("1234", "not-a-real-hash")


def test_hash_pin_rejects_short_pin():
    with pytest.raises(ValueError):
        auth.hash_pin("12")


# --- auth_required gate ------------------------------------------------------

async def test_auth_disabled_until_pin_configured(clean_auth):
    assert auth.auth_required() is False
    await store.update({"auth_pin_hash": auth.hash_pin("1234")})
    assert auth.auth_required() is True


# --- token lifecycle ---------------------------------------------------------

async def test_issue_and_resolve_token(clean_auth):
    token = await auth.issue_token(role="operator")
    sess = await auth.resolve_token(token)
    assert sess is not None
    assert sess.role == "operator"


async def test_raw_token_is_not_stored(clean_auth):
    """Only a hash of the token may hit the DB."""
    token = await auth.issue_token(role="admin")
    rows = await auth.all_sessions()
    assert rows, "expected a persisted session row"
    assert all(token not in r.token_hash for r in rows)


async def test_resolve_rejects_unknown_token(clean_auth):
    assert await auth.resolve_token("nope") is None


async def test_resolve_rejects_empty_token(clean_auth):
    assert await auth.resolve_token("") is None


async def test_expired_token_is_rejected(clean_auth):
    token = await auth.issue_token(role="admin", ttl_seconds=-1)
    assert await auth.resolve_token(token) is None


async def test_revoke_token(clean_auth):
    token = await auth.issue_token(role="admin")
    await auth.revoke_token(token)
    assert await auth.resolve_token(token) is None


async def test_elevation_expires(clean_auth):
    token = await auth.issue_token(role="operator")
    await auth.set_elevated(token, until=datetime.now(timezone.utc) - timedelta(seconds=1))
    sess = await auth.resolve_token(token)
    assert sess is not None
    assert auth.effective_role(sess) == "operator", "stale elevation must not grant admin"


async def test_effective_role_uses_live_elevation(clean_auth):
    token = await auth.issue_token(role="operator")
    await auth.set_elevated(token, until=datetime.now(timezone.utc) + timedelta(minutes=15))
    sess = await auth.resolve_token(token)
    assert auth.effective_role(sess) == "admin"


# --- endpoint enforcement ----------------------------------------------------

@pytest.fixture()
async def client(clean_auth):
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


PROTECTED = [
    ("GET", "/api/sessions"),
    ("GET", "/api/actions"),
    ("GET", "/api/dashboard/containers"),
    ("GET", "/api/dashboard/system"),
    ("GET", "/api/recommendations"),
    ("GET", "/api/settings"),
]


@pytest.mark.parametrize("method,path", PROTECTED)
async def test_open_when_no_pin_configured(client, method, path):
    res = await client.request(method, path)
    assert res.status_code != 401, f"{path} must stay open before a PIN is set"


@pytest.mark.parametrize("method,path", PROTECTED)
async def test_401_without_token_once_pin_set(client, method, path):
    await store.update({"auth_pin_hash": auth.hash_pin("1234")})
    res = await client.request(method, path)
    assert res.status_code == 401


@pytest.mark.parametrize("method,path", PROTECTED)
async def test_authorized_with_bearer_token(client, method, path):
    await store.update({"auth_pin_hash": auth.hash_pin("1234")})
    token = await auth.issue_token(role="admin")
    res = await client.request(method, path, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code != 401


async def test_healthz_always_reachable_and_advertises_auth(client):
    await store.update({"auth_pin_hash": auth.hash_pin("1234")})
    res = await client.get("/healthz")
    assert res.status_code == 200
    assert res.json()["auth_required"] is True


async def test_healthz_hides_detail_when_unauthenticated(client):
    await store.update({"auth_pin_hash": auth.hash_pin("1234")})
    body = (await client.get("/healthz")).json()
    assert "provider" not in body, "unauthenticated healthz must not leak configuration"


# --- login / logout / elevate ------------------------------------------------

async def test_setup_then_login_flow(client):
    res = await client.post("/api/auth/setup", json={"pin": "1234", "default_role": "operator"})
    assert res.status_code == 200

    assert (await client.post("/api/auth/login", json={"pin": "9999"})).status_code == 401

    ok = await client.post("/api/auth/login", json={"pin": "1234"})
    assert ok.status_code == 200
    assert ok.json()["role"] == "operator"
    assert "infra_copilot_token" in ok.cookies or ok.json().get("token")


async def test_setup_is_refused_once_configured(client):
    await client.post("/api/auth/setup", json={"pin": "1234"})
    again = await client.post("/api/auth/setup", json={"pin": "5678"})
    assert again.status_code == 409


async def test_status_endpoint_is_public(client):
    res = await client.get("/api/auth/status")
    assert res.status_code == 200
    assert res.json()["configured"] is False


async def test_elevate_requires_correct_pin(client):
    await client.post("/api/auth/setup", json={"pin": "1234", "default_role": "operator"})
    login = await client.post("/api/auth/login", json={"pin": "1234"})
    token = login.json()["token"]
    h = {"Authorization": f"Bearer {token}"}

    bad = await client.post("/api/auth/elevate", json={"pin": "0000"}, headers=h)
    assert bad.status_code == 401

    good = await client.post("/api/auth/elevate", json={"pin": "1234"}, headers=h)
    assert good.status_code == 200
    assert good.json()["role"] == "admin"


async def test_logout_revokes_the_token(client):
    await client.post("/api/auth/setup", json={"pin": "1234"})
    token = (await client.post("/api/auth/login", json={"pin": "1234"})).json()["token"]
    h = {"Authorization": f"Bearer {token}"}

    assert (await client.get("/api/sessions", headers=h)).status_code != 401
    await client.post("/api/auth/logout", headers=h)
    assert (await client.get("/api/sessions", headers=h)).status_code == 401


async def test_login_is_rejected_before_setup(client):
    res = await client.post("/api/auth/login", json={"pin": "1234"})
    assert res.status_code == 409
