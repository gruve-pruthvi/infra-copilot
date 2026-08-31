from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .auth import require_admin
from ..llm.models import CATALOG, test_provider
from ..schemas.api import ProviderTest, SettingsUpdate
from ..services.settings_store import store

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_settings():
    return {
        "settings": store.masked(),
        "catalog": {
            provider: {"label": cfg["label"], "models": cfg["models"], "default": cfg["default"]}
            for provider, cfg in CATALOG.items()
        },
    }


@router.put("")
async def update_settings(body: SettingsUpdate, identity: dict = Depends(require_admin)):
    # Admin-only: dry_run_mode skips the gateway's approval requirement for
    # every session, so a lower role must not be able to flip it — and this
    # endpoint also writes provider API keys.
    values = {k: v for k, v in body.model_dump().items() if v is not None}
    if "active_provider" in values and values["active_provider"] not in CATALOG:
        raise HTTPException(400, f"unknown provider '{values['active_provider']}'")
    if "dry_run_mode" in values and values["dry_run_mode"].lower() not in ("true", "false"):
        raise HTTPException(400, "dry_run_mode must be 'true' or 'false'")
    await store.update(values)
    return {"settings": store.masked()}


@router.post("/test-provider")
async def provider_test(body: ProviderTest):
    if body.provider not in CATALOG:
        raise HTTPException(400, f"unknown provider '{body.provider}'")
    api_key = body.api_key or store.get(CATALOG[body.provider]["key_setting"])
    if not api_key:
        raise HTTPException(400, "no API key provided or stored")
    return await test_provider(body.provider, body.model, api_key)
