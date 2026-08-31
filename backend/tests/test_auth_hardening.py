"""Hardening from the Phase 3 security review.

Each test here corresponds to a specific finding; the docstrings name the
attack so a future edit that reintroduces it fails loudly.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import ws as ws_api
from app.config import settings
from app.services import auth
from app.services.settings_store import store


@pytest.fixture()
async def clean(db_ready):
    await store.update({"auth_pin_hash": "", "auth_default_role": "admin", "dry_run_mode": "false"})
    await auth.revoke_all()
    auth.reset_throttle()
    yield
    await store.update({"auth_pin_hash": "", "auth_default_role": "admin", "dry_run_mode": "false"})
    await auth.revoke_all()
    auth.reset_throttle()


@pytest.fixture()
async def client(clean):
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# --- Finding 1: websocket Origin check ---------------------------------------
#
# Browsers do NOT apply CORS to WebSockets, so cors_origins protects the REST
# surface but not /ws/events. Without an Origin check, any page the operator
# visits can stream container logs from a default (no-PIN) install.

def test_origin_allowed_for_configured_frontends():
    for origin in settings.cors_origins:
        assert ws_api._origin_allowed(origin, host="localhost:8000")


def test_origin_allowed_when_absent():
    """Non-browser clients (tests, CLI, curl) send no Origin — must not break."""
    assert ws_api._origin_allowed(None, host="localhost:8000")


def test_same_origin_allowed_for_bundled_frontend():
    """Serving the built frontend from the backend itself is same-origin."""
    assert ws_api._origin_allowed("http://localhost:8000", host="localhost:8000")


@pytest.mark.parametrize("evil", [
    "http://evil.example",
    "https://attacker.test",
    "http://localhost:3000",
    "null",
    "http://localhost.evil.example",
])
def test_foreign_origins_rejected(evil):
    assert not ws_api._origin_allowed(evil, host="localhost:8000")


def test_origin_check_applies_even_without_a_pin():
    """The drive-by case is precisely the no-PIN install — don't skip it."""
    assert not auth.auth_required()
    assert not ws_api._origin_allowed("http://evil.example", host="localhost:8000")


# --- Finding 3: scrypt must not block the event loop -------------------------

async def test_login_does_not_block_the_event_loop(client):
    """verify_pin is ~24ms of CPU; run off-thread or one attacker stalls everything."""
    await store.update({"auth_pin_hash": auth.hash_pin("1234")})

    ticks = 0
    stop = False

    async def heartbeat():
        nonlocal ticks
        while not stop:
            ticks += 1
            await asyncio.sleep(0.001)

    hb = asyncio.create_task(heartbeat())
    await asyncio.gather(*[client.post("/api/auth/login", json={"pin": "wrong"}) for _ in range(8)])
    stop = True
    await hb

    # If hashing ran inline on the loop these 8 attempts would starve the
    # heartbeat almost completely.
    assert ticks > 20, f"event loop starved during login (only {ticks} ticks)"


# --- Finding 2: login throttling ---------------------------------------------

async def test_repeated_bad_pins_are_throttled(client):
    await store.update({"auth_pin_hash": auth.hash_pin("1234")})
    codes = []
    for _ in range(auth.MAX_FAILED_ATTEMPTS + 2):
        codes.append((await client.post("/api/auth/login", json={"pin": "wrong"})).status_code)
    assert 429 in codes, f"expected lockout after {auth.MAX_FAILED_ATTEMPTS} failures, got {codes}"


async def test_successful_login_clears_the_throttle(client):
    await store.update({"auth_pin_hash": auth.hash_pin("1234")})
    await client.post("/api/auth/login", json={"pin": "wrong"})
    assert (await client.post("/api/auth/login", json={"pin": "1234"})).status_code == 200
    assert auth.failed_attempts() == 0


async def test_throttle_also_covers_elevate(client):
    """/elevate is a second oracle for the same secret."""
    await client.post("/api/auth/setup", json={"pin": "1234", "default_role": "operator"})
    token = (await client.post("/api/auth/login", json={"pin": "1234"})).json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    codes = []
    for _ in range(auth.MAX_FAILED_ATTEMPTS + 2):
        codes.append((await client.post("/api/auth/elevate", json={"pin": "no"}, headers=h)).status_code)
    assert 429 in codes


# --- Finding 5: settings changes require admin -------------------------------

async def test_viewer_cannot_disable_approvals_via_dry_run(client):
    """dry_run_mode skips the approval requirement globally — admin only."""
    await client.post("/api/auth/setup", json={"pin": "1234", "default_role": "viewer"})
    token = (await client.post("/api/auth/login", json={"pin": "1234"})).json()["token"]

    res = await client.put("/api/settings", json={"dry_run_mode": "true"},
                           headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403
    assert store.dry_run_mode is False, "viewer must not be able to flip the global switch"


async def test_viewer_cannot_overwrite_provider_keys(client):
    await client.post("/api/auth/setup", json={"pin": "1234", "default_role": "viewer"})
    token = (await client.post("/api/auth/login", json={"pin": "1234"})).json()["token"]
    res = await client.put("/api/settings", json={"google_api_key": "stolen"},
                           headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403


async def test_admin_can_still_change_settings(client):
    await client.post("/api/auth/setup", json={"pin": "1234", "default_role": "admin"})
    token = (await client.post("/api/auth/login", json={"pin": "1234"})).json()["token"]
    res = await client.put("/api/settings", json={"dry_run_mode": "true"},
                           headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200


async def test_settings_readable_by_any_role(client):
    await client.post("/api/auth/setup", json={"pin": "1234", "default_role": "viewer"})
    token = (await client.post("/api/auth/login", json={"pin": "1234"})).json()["token"]
    res = await client.get("/api/settings", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
